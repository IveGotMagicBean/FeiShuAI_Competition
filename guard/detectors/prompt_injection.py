"""提示注入检测器（规则 + 边界识别）"""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern


@dataclass
class Rule:
    rule_id: str
    pattern: Pattern[str]
    weight: float
    description: str


# 中英文混合的注入特征库
_RAW_RULES: list[tuple[str, str, float, str]] = [
    # ==== 角色覆写类 ====
    ("R-INJ-001", r"(?i)ignore\s+(?:the\s+)?(?:previous|all|prior|above)\s+(?:instructions?|prompts?|rules?)", 0.7, "英文：忽略前面的指令"),
    ("R-INJ-002", r"(?i)disregard\s+(?:the\s+)?(?:above|previous|prior|earlier)", 0.6, "英文：disregard 前文"),
    ("R-INJ-003", r"(?i)forget\s+(?:everything|all)\s+(?:above|before|prior)", 0.6, "英文：forget everything"),
    ("R-INJ-004", r"忽略\s*(前面|之前|上面|所有).{0,4}(指令|提示|规则|要求)", 0.7, "中文：忽略前面的指令"),
    ("R-INJ-005", r"(?:不要|别)\s*(?:再)?\s*(?:遵循|遵守|按照).{0,8}(指令|规则)", 0.6, "中文：不要遵守"),
    # ==== 角色扮演 / 越狱 ====
    ("R-INJ-010", r"(?i)you\s+are\s+now\s+(?:a|an|the)?\s*\w+", 0.5, "英文：you are now"),
    ("R-INJ-011", r"(?i)pretend\s+(?:you\s+are|to\s+be)", 0.5, "英文：pretend you are"),
    ("R-INJ-012", r"(?i)act\s+as\s+(?:if\s+)?", 0.4, "英文：act as"),
    ("R-INJ-013", r"(?i)roleplay\s+as", 0.5, "英文：roleplay as"),
    ("R-INJ-014", r"(?i)\bDAN\b\s*(?:mode|prompt)?", 0.8, "DAN 越狱"),
    ("R-INJ-015", r"(?i)developer\s+mode", 0.7, "开发者模式越狱"),
    ("R-INJ-016", r"(?i)jailbreak", 0.8, "越狱关键词"),
    ("R-INJ-017", r"扮演\s*(?:一个|一名)?\s*\w+", 0.4, "中文：扮演"),
    ("R-INJ-018", r"假装(?:你|你是)", 0.5, "中文：假装"),
    ("R-INJ-019", r"(?:开发者|管理员|root)\s*模式", 0.7, "中文：开发者模式"),
    # ==== 系统消息伪造 ====
    ("R-INJ-020", r"(?:^|\n)\s*\[?\s*SYSTEM\s*\]?\s*[:：]", 0.8, "伪造系统消息"),
    ("R-INJ-021", r"<\|?(?:system|im_start|admin)\|?>", 0.9, "伪造特殊 token"),
    ("R-INJ-022", r"</user>\s*<system>", 0.95, "伪造对话边界"),
    ("R-INJ-023", r"^\s*system\s*[:：].{0,50}你是", 0.7, "中文伪造系统"),
    # ==== 工具特征类（高危） ====
    ("R-INJ-030", r"(?i)(?:read|cat|open).{0,30}(?:\.ssh|\.aws|\.env|/etc/passwd|/etc/shadow|id_rsa|credentials)", 0.9, "敏感文件读取意图"),
    ("R-INJ-031", r"(?i)(?:send|upload|post|exfil).{0,30}(?:to|→).{0,30}(?:http|attacker|pastebin|requestbin|webhook)", 0.9, "数据外发意图"),
    ("R-INJ-032", r"(?i)(?:rm|del|delete)\s+-rf?\s+", 0.95, "删除文件意图"),
    ("R-INJ-033", r"(?:把|将).{0,20}(?:发送|上传|发到|发给).{0,30}(?:http|外部|服务器|attacker)", 0.9, "中文：数据外发"),
    ("R-INJ-034", r"(?:读取|打开|查看).{0,20}(\.ssh|\.env|id_rsa|私钥|密钥|credentials)", 0.9, "中文：敏感文件"),
    # ==== 编码 / 混淆 ====
    ("R-INJ-040", r"(?i)base64\s*\(\s*['\"]?[A-Za-z0-9+/=]{20,}", 0.5, "base64 编码注入"),
    ("R-INJ-041", r"\\x[0-9a-fA-F]{2}\\x[0-9a-fA-F]{2}\\x[0-9a-fA-F]{2}", 0.5, "十六进制串注入"),
    ("R-INJ-042", r"[​-‏‪-‮﻿]", 0.6, "零宽字符（隐写）"),
    # ==== 边界欺骗 ====
    ("R-INJ-050", r"(?i)###\s*(?:end|stop|new)\s+(?:instruction|task|prompt)", 0.6, "伪造 markdown 边界"),
    ("R-INJ-051", r"(?i)---+\s*(?:new\s+)?(?:instruction|prompt|system)", 0.6, "伪造分隔线"),
    # ==== 权限自我声明（社工） ====
    ("R-INJ-060", r"(?i)\bI\s+am\s+(?:the\s+)?(?:admin|administrator|root|system\s+administrator|owner|developer)\b", 0.6, "自称管理员/root"),
    ("R-INJ-061", r"(?i)\boverride\s+(?:all\s+)?(?:guards?|rules?|safety|restrictions?|protections?)", 0.6, "声称豁免/绕过"),
    ("R-INJ-062", r"(?:我是|本人是)\s*(?:管理员|系统管理员|超级用户|root|开发者|owner)", 0.6, "中文：自称管理员"),
    # ==== 中文角色扮演（兼容中文姓名/称谓） ====
    ("R-INJ-063", r"扮演\s*(?:我|一个|一名)?\s*[一-龥A-Za-z]+", 0.5, "中文扮演（含中文字符）"),
    ("R-INJ-064", r"(?:序列号|激活码|许可证|license\s*key|product\s*key|cd-?key)", 0.7, "盗版凭据请求"),
    # ==== 续写攻击：伪造 assistant: 已答应的回答 ====
    ("R-INJ-070", r"(?i)\bassistant\s*[:：]\s*(?:好的|当然|没问题|sure|ok|here|下面|certainly)", 0.7, "伪造 assistant 已答应"),
    ("R-INJ-071", r"(?:列出|输出|显示|打印|读取).{0,20}(\.ssh|\.env|id_rsa|私钥|密钥|credentials|环境变量|secret|token)", 0.8, "中文：敏感数据动作词扩展"),
]


class PromptInjectionDetector:
    """基于规则 + 边界识别的注入检测器"""

    def __init__(self, custom_rules: list[Rule] | None = None):
        self.rules: list[Rule] = [
            Rule(rid, re.compile(pat), w, desc)
            for rid, pat, w, desc in _RAW_RULES
        ]
        if custom_rules:
            self.rules.extend(custom_rules)

    def detect(self, text: str) -> tuple[float, list[str]]:
        """返回 (风险分 0~1, 命中规则 ID 列表)"""
        if not text:
            return 0.0, []
        risk = 0.0
        hits: list[str] = []
        for rule in self.rules:
            if rule.pattern.search(text):
                risk += rule.weight
                hits.append(f"{rule.rule_id}({rule.description})")
        # 边界识别加分项：检测对话标签欺骗
        if self._has_boundary_spoof(text):
            risk += 0.3
            hits.append("R-BNDR-001(对话边界欺骗)")
        return min(risk, 1.0), hits

    @staticmethod
    def _has_boundary_spoof(text: str) -> bool:
        """检测是否试图伪造对话边界（user/assistant/system 标签）"""
        spoof_patterns = [
            r"</?(?:user|assistant|system|human|ai)>",
            r"\|im_(?:start|end)\|",
            r"\<\|.*?\|\>",
        ]
        for pat in spoof_patterns:
            if re.search(pat, text, flags=re.IGNORECASE):
                return True
        return False

    def explain(self, text: str) -> dict:
        """详细诊断输出（给 dashboard 用）"""
        risk, hits = self.detect(text)
        return {
            "risk_score": risk,
            "hits": hits,
            "boundary_spoof": self._has_boundary_spoof(text),
            "text_length": len(text),
        }
