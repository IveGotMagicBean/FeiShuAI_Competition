"""飞书消息审批通道：把 ASK_USER 待审批推成飞书卡片消息，回调里把决策回灌。

一条审批的完整闭环：
  proxy → PendingDecisions.add()
        → SSE 推 dashboard
        → Web Push 推手机浏览器
        → LarkNotifier.send_pending_card() 推飞书群/私信  ← 本模块
                              │
                  飞书用户点[批准]/[拒绝]按钮
                              │
                              ▼
            POST /api/lark/callback (server.py)
                              │
                              ▼
            LarkCallbackHandler.handle_card_action()
                              │
                              ▼
            approvals.decide(pid, approved=...)
                              │
                              ▼
            update_card_after_decision()  ← 卡片改成 "✅ 已批准 by @张三"

设计要点：
  - 不强制依赖飞书：lark-oapi 装了才启用，没装时 dashboard 其它功能都不受影响
  - App ID/Secret 存 ~/.sentinel-mcp/lark_config.json，权限 0o600
  - 回调 endpoint 必须验证 token / 签名（对外网开放的 webhook 不能裸跑）
  - 卡片用 JSON 构造，不依赖飞书 card-builder GUI
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# lark-oapi 是可选依赖：没装时本模块仍可 import，但 LarkNotifier 实例化会抛错
try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
        PatchMessageRequest,
        PatchMessageRequestBody,
    )
    _LARK_AVAILABLE = True
except ImportError:
    _LARK_AVAILABLE = False
    lark = None  # type: ignore


# ---------- 配置持久化 -----------------------------------------------

DEFAULT_CONFIG_PATH = Path.home() / ".sentinel-mcp" / "lark_config.json"


@dataclass
class LarkConfig:
    """从磁盘加载的飞书集成配置。"""
    app_id: str
    app_secret: str
    target_chat_id: str = ""           # 群 chat_id（oc_xxx）或个人 open_id（ou_xxx）
    encrypt_key: str = ""              # 飞书事件订阅的 Encrypt Key（消息加密时必填）
    verification_token: str = ""       # 飞书事件订阅的 Verification Token

    def to_dict(self) -> dict[str, str]:
        return {
            "app_id": self.app_id,
            "app_secret": self.app_secret,
            "target_chat_id": self.target_chat_id,
            "encrypt_key": self.encrypt_key,
            "verification_token": self.verification_token,
        }


def load_config(path: Path | None = None) -> LarkConfig | None:
    """读 ~/.sentinel-mcp/lark_config.json，不存在或缺字段返回 None。"""
    p = path or DEFAULT_CONFIG_PATH
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not raw.get("app_id") or not raw.get("app_secret"):
        return None
    return LarkConfig(
        app_id=raw["app_id"],
        app_secret=raw["app_secret"],
        target_chat_id=raw.get("target_chat_id", ""),
        encrypt_key=raw.get("encrypt_key", ""),
        verification_token=raw.get("verification_token", ""),
    )


def save_config(cfg: LarkConfig, path: Path | None = None) -> Path:
    """落盘 + chmod 0o600，仅当前 user 可读写。"""
    p = path or DEFAULT_CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    return p


# ---------- 卡片渲染（不依赖 SDK，纯 dict） -------------------------

_PILL_BY_DECISION = {"allow": "🟢", "deny": "🔴", "redact": "🔵", "ask_user": "🟡"}


def build_pending_card(pending: dict[str, Any]) -> dict[str, Any]:
    """构造待审批卡片：标题 + 工具调用详情 + Approve/Deny 按钮。"""
    pid = pending["id"]
    tool = pending.get("tool_name", "?")
    reason = pending.get("reason", "")
    risk = pending.get("risk_score", 0.0) or 0.0
    args = pending.get("args") or {}
    args_brief = json.dumps(args, ensure_ascii=False, default=str)
    if len(args_brief) > 280:
        args_brief = args_brief[:277] + "..."

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": "🛡 Sentinel-MCP · 待您授权"},
        },
        "elements": [
            {
                "tag": "div",
                "fields": [
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**工具**\n{tool}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**风险分**\n{risk:.2f}"}},
                ],
            },
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**原因**\n{reason or '（无）'}"}},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**调用参数**\n```\n{args_brief}\n```"}},
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "✅ 批准"},
                        "type": "primary",
                        "value": {"action": "approve", "pid": pid},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🚫 拒绝"},
                        "type": "danger",
                        "value": {"action": "deny", "pid": pid},
                    },
                ],
            },
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": f"id={pid}  ·  请在 60s 内决断，超时按拒绝处理"},
                ],
            },
        ],
    }


def build_decided_card(pending: dict[str, Any], approved: bool, by: str) -> dict[str, Any]:
    """决策后用这个 card 替换原卡片，按钮变只读状态。"""
    pid = pending["id"]
    tool = pending.get("tool_name", "?")
    status_emoji = "✅" if approved else "🚫"
    status_text = "已批准" if approved else "已拒绝"
    template = "green" if approved else "red"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": f"{status_emoji} Sentinel-MCP · {status_text}"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**工具**: {tool}\n**操作人**: {by}\n**时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}"}},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": f"id={pid}"}]},
        ],
    }


# ---------- 主类 ------------------------------------------------------

class LarkNotifierUnavailable(RuntimeError):
    """lark-oapi 没装，所有 send/patch 操作都不可用。"""


class LarkNotifier:
    """飞书消息推送 + 卡片更新封装。"""

    def __init__(self, cfg: LarkConfig):
        if not _LARK_AVAILABLE:
            raise LarkNotifierUnavailable(
                "lark-oapi 未安装；先 `pip install lark-oapi` 再启用飞书集成"
            )
        if not cfg.app_id or not cfg.app_secret:
            raise ValueError("LarkConfig 缺 app_id 或 app_secret")
        self.cfg = cfg
        self.client = lark.Client.builder() \
            .app_id(cfg.app_id) \
            .app_secret(cfg.app_secret) \
            .build()

    # ---- 发送 ----

    def send_pending(self, pending: dict[str, Any]) -> str:
        """发一张待审批卡片，返回飞书 message_id（后续 patch 用）。"""
        if not self.cfg.target_chat_id:
            raise ValueError("target_chat_id 未设置；先配置发送目标")
        card = build_pending_card(pending)
        receive_id_type = self._infer_id_type(self.cfg.target_chat_id)
        req = CreateMessageRequest.builder() \
            .receive_id_type(receive_id_type) \
            .request_body(
                CreateMessageRequestBody.builder()
                    .receive_id(self.cfg.target_chat_id)
                    .msg_type("interactive")
                    .content(json.dumps(card, ensure_ascii=False))
                    .build()
            ).build()
        resp = self.client.im.v1.message.create(req)
        if not resp.success():
            raise RuntimeError(f"飞书发送失败: code={resp.code} msg={resp.msg}")
        return resp.data.message_id

    def patch_to_decided(
        self, message_id: str, pending: dict[str, Any], approved: bool, by: str
    ) -> bool:
        """把已决断的卡片更新成结果状态。失败不抛异常（决策本身已生效）。"""
        try:
            card = build_decided_card(pending, approved, by)
            req = PatchMessageRequest.builder() \
                .message_id(message_id) \
                .request_body(
                    PatchMessageRequestBody.builder()
                        .content(json.dumps(card, ensure_ascii=False))
                        .build()
                ).build()
            resp = self.client.im.v1.message.patch(req)
            return resp.success()
        except Exception:
            return False

    def list_chats(self, page_size: int = 50) -> list[dict[str, Any]]:
        """拿机器人当前所在的所有 chat（群 / 私信），dashboard 自动列下拉。

        走 `/open-apis/im/v1/chats` 直 HTTP（不绕 SDK，因为 SDK 这条路径也是
        REST，自己写更可控）。如果连不通 / 鉴权失败 → 返回 [] 让前端降级到
        手输 chat_id。
        """
        import urllib.request
        import urllib.error

        # 1) 拿 tenant_access_token
        try:
            token_req = urllib.request.Request(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                data=json.dumps({"app_id": self.cfg.app_id, "app_secret": self.cfg.app_secret}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(token_req, timeout=8) as resp:
                token_data = json.loads(resp.read().decode("utf-8"))
            token = token_data.get("tenant_access_token")
            if not token:
                return []
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError):
            return []

        # 2) 列 chats
        try:
            chats_req = urllib.request.Request(
                f"https://open.feishu.cn/open-apis/im/v1/chats?page_size={page_size}",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(chats_req, timeout=8) as resp:
                chats_data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError):
            return []

        items = (chats_data.get("data") or {}).get("items") or []
        # 简化字段：name / chat_id / chat_mode（group / p2p）/ avatar
        return [
            {
                "chat_id": it.get("chat_id", ""),
                "name": it.get("name", "(无名群)"),
                "chat_mode": it.get("chat_mode", ""),
                "avatar": it.get("avatar", ""),
            }
            for it in items
            if it.get("chat_id")
        ]

    def send_test(self, text: str = "Sentinel-MCP 飞书集成测试 ✓") -> str:
        """配置面板「测试连接」按钮：发一条简单文本消息验证全链路。"""
        if not self.cfg.target_chat_id:
            raise ValueError("target_chat_id 未设置")
        receive_id_type = self._infer_id_type(self.cfg.target_chat_id)
        req = CreateMessageRequest.builder() \
            .receive_id_type(receive_id_type) \
            .request_body(
                CreateMessageRequestBody.builder()
                    .receive_id(self.cfg.target_chat_id)
                    .msg_type("text")
                    .content(json.dumps({"text": text}, ensure_ascii=False))
                    .build()
            ).build()
        resp = self.client.im.v1.message.create(req)
        if not resp.success():
            raise RuntimeError(f"飞书测试消息失败: code={resp.code} msg={resp.msg}")
        return resp.data.message_id

    # ---- 工具方法 ----

    @staticmethod
    def _infer_id_type(target: str) -> str:
        """根据 ID 前缀猜 receive_id_type。oc_=群、ou_=open_id、cli_=app_id（不合理）。"""
        if target.startswith("oc_"):
            return "chat_id"
        if target.startswith("ou_"):
            return "open_id"
        if target.startswith("on_"):
            return "union_id"
        # 兜底当 open_id（飞书 user_id 不一定带前缀）
        return "open_id"


# ---------- 回调验证 + AES 解密 ---------------------------------------
# 飞书事件订阅有 3 种保护层，按出现顺序：
#   1. AES-256-CBC 加密（设了 Encrypt Key 时所有事件 payload 都被包成 {"encrypt": "<base64>"}）
#   2. URL verification 握手（首次配 callback URL 时，用 challenge / token 字段）
#   3. 普通事件携带 X-Lark-Signature 头（HMAC over timestamp+nonce+key+body）
# 我们都要支持。

def decrypt_payload(encrypted_b64: str, encrypt_key: str) -> dict[str, Any]:
    """飞书 AES 解密：key = sha256(encrypt_key)，IV = 密文前 16 字节，模式 CBC + PKCS7。"""
    from base64 import b64decode
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    raw = b64decode(encrypted_b64)
    iv, ciphertext = raw[:16], raw[16:]
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()  # 32 字节
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plaintext = unpadder.update(padded) + unpadder.finalize()
    return json.loads(plaintext.decode("utf-8"))


def maybe_decrypt(payload: dict[str, Any], cfg: LarkConfig) -> dict[str, Any]:
    """如果 payload 是飞书加密格式 {"encrypt": "..."}，解密返回内层；否则原样返回。"""
    if not isinstance(payload, dict):
        return payload
    enc = payload.get("encrypt")
    if isinstance(enc, str) and cfg.encrypt_key:
        try:
            return decrypt_payload(enc, cfg.encrypt_key)
        except Exception as e:
            raise ValueError(f"飞书 payload 解密失败：{e}") from e
    return payload


def verify_url_challenge(payload: dict[str, Any], cfg: LarkConfig) -> dict[str, Any] | None:
    """如果是 URL 验证握手请求，返回应该 200 回的 body；否则返回 None。

    注意：调用前需先用 maybe_decrypt 解密，否则握手 payload 还在 {"encrypt": ...} 里。
    旧 schema 1.0 的 token 字段需要跟 cfg.verification_token 比对，不一致就拒绝。
    """
    # schema 1.0：{type, challenge, token}
    if payload.get("type") == "url_verification":
        # 如果配了 verification_token，必须匹配
        if cfg.verification_token and payload.get("token") != cfg.verification_token:
            raise ValueError("verification_token 不匹配")
        return {"challenge": payload.get("challenge", "")}
    # schema 2.0：握手放在 header.event_type
    header = payload.get("header") or {}
    if header.get("event_type") == "url_verification":
        if cfg.verification_token and header.get("token") != cfg.verification_token:
            raise ValueError("verification_token 不匹配")
        return {"challenge": payload.get("challenge", "")}
    return None


def verify_signature(
    timestamp: str, nonce: str, body: bytes, signature: str, cfg: LarkConfig
) -> bool:
    """验证 X-Lark-Signature。Encrypt Key 没配时跳过（开发模式）。"""
    if not cfg.encrypt_key:
        return True  # 没设密钥就不强校验，方便本地调试
    raw = (timestamp + nonce + cfg.encrypt_key).encode() + body
    expected = hashlib.sha256(raw).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_card_action(payload: dict[str, Any]) -> tuple[str, str, str, dict] | None:
    """解析飞书卡片按钮回调，返回 (pid, action, operator_name, raw_event)。

    payload 形态（schema 2.0）:
      {
        "header": {"event_type": "card.action.trigger", ...},
        "event": {
          "operator": {"open_id": "ou_xxx", "tenant_key": "..."},
          "action": {"tag": "button", "value": {"action": "approve", "pid": "xxx"}},
          "context": {"open_message_id": "om_xxx", ...}
        }
      }

    旧版 schema 1.0 也兼容（直接顶层 action 字段）。
    """
    # 旧 schema
    if "action" in payload and "value" in payload["action"]:
        v = payload["action"]["value"]
        return (
            v.get("pid", ""),
            v.get("action", ""),
            payload.get("operator", {}).get("user_id", "") or "anonymous",
            payload,
        )

    # 新 schema 2.0
    event = payload.get("event") or {}
    action = event.get("action") or {}
    value = action.get("value") or {}
    if "pid" not in value or "action" not in value:
        return None
    operator = event.get("operator") or {}
    name = (
        operator.get("open_id")
        or operator.get("user_id")
        or operator.get("tenant_key")
        or "anonymous"
    )
    return value["pid"], value["action"], name, payload
