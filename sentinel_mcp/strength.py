"""拦截强度档位：5 档预设 + 高级单条阈值微调 + 工具白/黑名单。

设计哲学：
  - 档位是「policy 上的 view」，不是 policy 替代品。底层仍走 config/policies.yaml。
  - 切档位 = 写一份 ~/.sentinel-mcp/strength.json，guard 读 policy 时顺带读这个 override。
  - 可以理解为「strict 把 dlp.threshold 从 0.5 → 0.3，把更多工具拉进 require_user_authz」

5 档：
  Strict     ：所有写操作都问；DLP 灵敏；网络只允许白名单；shell 几乎全拦
  Strong     ：默认值更激进；有 DLP / injection 拦截
  Balanced   ：v0.2 默认值
  Lenient    ：DLP 阈值放宽；少数高频写操作直放
  Permissive ：只拦明确危险（rm -rf / 写 /etc / 私钥），其它放
  Custom     ：用户自己调，每个 detector / 工具独立配

档位影响的字段：
  - detectors.prompt_injection.threshold   (0.3 ~ 0.8)
  - detectors.dlp.enabled                  (true/false)
  - tools.<name>.require_user_authz        (扩缩 sensitive 集合)
  - filesystem.denylist 严格度             (Strict 加 ~/.config/**, ~/.bashrc 等)
  - shell.allowlist                         (Permissive 多放几条)

API：
  read_level()        → 当前档位（Literal）
  write_level(name)   → 切档位
  effective_overrides() → 当前档位 + 用户 custom 后，要叠加到 policy 上的 dict
  custom_setters     → 各种 set_* 用于 Custom 档位下细调
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

LevelName = Literal["strict", "strong", "balanced", "lenient", "permissive", "custom"]
ALL_LEVELS: list[LevelName] = ["strict", "strong", "balanced", "lenient", "permissive", "custom"]
DEFAULT_LEVEL: LevelName = "balanced"

DEFAULT_PATH = Path(os.environ.get(
    "SENTINEL_STRENGTH_FILE",
    str(Path.home() / ".sentinel-mcp" / "strength.json"),
))


# ============== 5 档预设的语义化定义 ==============
# 每档生成一个 overrides dict，guard.from_yaml 加载完 policy 后会被它「覆盖合并」

PRESETS: dict[str, dict[str, Any]] = {
    "strict": {
        "label": "🔒 严格",
        "tagline": "所有写/网络/shell 操作都需审批 · DLP 极敏感",
        "color": "#dc2626",
        "overrides": {
            "detectors.prompt_injection.threshold": 0.3,
            "detectors.dlp.enabled": True,
            "tools_require_authz_extra": ["read_file", "list_dir", "http_request"],
            "filesystem_denylist_extra": [
                "~/.config/**", "~/.bashrc", "~/.zshrc", "~/.profile",
                "~/.gitconfig", "~/.npmrc", "~/.pypirc",
            ],
        },
    },
    "strong": {
        "label": "🛡 强",
        "tagline": "敏感写 + 网络出站需审批 · DLP 灵敏",
        "color": "#ea580c",
        "overrides": {
            "detectors.prompt_injection.threshold": 0.4,
            "detectors.dlp.enabled": True,
            "tools_require_authz_extra": ["http_request"],
        },
    },
    "balanced": {
        "label": "⚖ 平衡（默认）",
        "tagline": "v0.2 默认策略 · 写敏感路径 / shell 危险命令需审批",
        "color": "#16a34a",
        "overrides": {},  # 空 = 用 policy.yaml 原值
    },
    "lenient": {
        "label": "🌿 宽松",
        "tagline": "只拦明显危险 · 高频写直放 · DLP 仅扫输出",
        "color": "#2563eb",
        "overrides": {
            "detectors.prompt_injection.threshold": 0.7,
            "tools_authz_relax": ["write_file"],
        },
    },
    "permissive": {
        "label": "🔓 极宽松",
        "tagline": "只拦 rm -rf / /etc / 私钥 · 几乎全放",
        "color": "#64748b",
        "overrides": {
            "detectors.prompt_injection.threshold": 0.9,
            "detectors.dlp.enabled": False,
            "tools_authz_relax": ["write_file", "shell_exec", "http_request", "clipboard_write"],
        },
    },
    "custom": {
        "label": "⚙ 自定义",
        "tagline": "高级模式 — 单条阈值 + 工具白/黑名单自己配",
        "color": "#9333ea",
        "overrides": {},  # custom 由 _state["custom_overrides"] 提供
    },
}


# ============== 持久化 ==============

def _read(path: Path | None = None) -> dict[str, Any]:
    p = path if path is not None else DEFAULT_PATH
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write(state: dict[str, Any], path: Path | None = None) -> Path:
    p = path if path is not None else DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    tmp.replace(p)
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return p


def read_level(path: Path | None = None) -> LevelName:
    state = _read(path)
    lvl = (state.get("level") or DEFAULT_LEVEL).lower()
    if lvl not in ALL_LEVELS:
        return DEFAULT_LEVEL
    return lvl  # type: ignore[return-value]


def write_level(level: LevelName, path: Path | None = None) -> Path:
    if level not in ALL_LEVELS:
        raise ValueError(f"未知 level: {level}（必须是 {', '.join(ALL_LEVELS)}）")
    state = _read(path)
    state["level"] = level
    return _write(state, path)


def get_state(path: Path | None = None) -> dict[str, Any]:
    """完整状态：level + custom 设置 + 当前生效的 overrides。"""
    state = _read(path)
    level = read_level(path)
    return {
        "level": level,
        "custom_overrides": state.get("custom_overrides") or {},
        "tool_allowlist": state.get("tool_allowlist") or [],
        "tool_denylist": state.get("tool_denylist") or [],
        "presets": {k: {kk: v for kk, v in p.items() if kk != "overrides"} for k, p in PRESETS.items()},
        "effective": effective_overrides(path),
    }


# ============== Custom 档位的高级设置 ==============

def set_custom_override(key: str, value: Any, path: Path | None = None) -> Path:
    """更新 custom 档位下的单个 override。例：set_custom_override('dlp.threshold', 0.4)"""
    state = _read(path)
    co = state.setdefault("custom_overrides", {})
    co[key] = value
    return _write(state, path)


def set_tool_allowlist(tools: list[str], path: Path | None = None) -> Path:
    state = _read(path)
    state["tool_allowlist"] = sorted(set(tools))
    return _write(state, path)


def set_tool_denylist(tools: list[str], path: Path | None = None) -> Path:
    state = _read(path)
    state["tool_denylist"] = sorted(set(tools))
    return _write(state, path)


# ============== 给 guard 用的 ==============

def effective_overrides(path: Path | None = None) -> dict[str, Any]:
    """guard 加载 policy 后调它，把档位 + custom 的 overrides 合并出来。

    返回结构（guard 自己负责把这些应用到内部数据结构）：
      {
        "thresholds": {"prompt_injection": 0.4, "dlp_enabled": true},
        "tools_extra_authz": ["http_request"],
        "tools_no_authz":   ["write_file"],
        "filesystem_denylist_extra": [...],
        "tool_allowlist": ["read_file", ...],   # 用户全局白名单（无视 detector）
        "tool_denylist":  ["http_request_external"],  # 用户全局黑名单
      }
    """
    state = _read(path)
    level = read_level(path)
    preset = PRESETS.get(level, PRESETS["balanced"])
    src = dict(preset.get("overrides") or {})
    if level == "custom":
        # custom 档位用 state 里的细调字段
        src.update(state.get("custom_overrides") or {})

    out: dict[str, Any] = {
        "level": level,
        "thresholds": {},
        "tools_extra_authz": [],
        "tools_no_authz": [],
        "filesystem_denylist_extra": [],
        "tool_allowlist": state.get("tool_allowlist") or [],
        "tool_denylist": state.get("tool_denylist") or [],
    }
    for k, v in src.items():
        if k == "detectors.prompt_injection.threshold":
            out["thresholds"]["prompt_injection"] = v
        elif k == "detectors.dlp.enabled":
            out["thresholds"]["dlp_enabled"] = v
        elif k == "tools_require_authz_extra":
            out["tools_extra_authz"] = list(v)
        elif k == "tools_authz_relax":
            out["tools_no_authz"] = list(v)
        elif k == "filesystem_denylist_extra":
            out["filesystem_denylist_extra"] = list(v)
    return out


def is_tool_globally_blocked(tool_name: str, path: Path | None = None) -> bool:
    """工具被用户拉黑了 → guard 直接 DENY，不走 detector。"""
    eff = effective_overrides(path)
    return tool_name in (eff.get("tool_denylist") or [])


def is_tool_globally_allowed(tool_name: str, path: Path | None = None) -> bool:
    """工具在用户白名单 → guard 直接 ALLOW，跳过 detector + ASK_USER。"""
    eff = effective_overrides(path)
    return tool_name in (eff.get("tool_allowlist") or [])
