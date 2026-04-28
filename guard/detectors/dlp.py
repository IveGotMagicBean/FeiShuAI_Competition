"""DLP（敏感信息检测与脱敏）"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class DLPRule:
    name: str
    pattern: re.Pattern
    mask: str
    description: str


_DEFAULT_RULES: list[tuple[str, str, str, str]] = [
    ("phone_cn", r"(?<![\d])1[3-9]\d{9}(?![\d])", "[PHONE]", "中国手机号"),
    ("email", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[EMAIL]", "邮箱地址"),
    ("id_card_cn", r"(?<![\d])[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![\d])", "[ID_CARD]", "中国身份证"),
    ("bank_card", r"(?<![\d])\d{16,19}(?![\d])", "[BANK_CARD]", "银行卡号"),
    ("aws_access_key", r"AKIA[0-9A-Z]{16}", "[AWS_ACCESS_KEY]", "AWS Access Key"),
    ("aws_secret_key", r"(?i)aws_secret[_-]?access[_-]?key['\"\s:=]+[A-Za-z0-9/+=]{40}", "[AWS_SECRET_KEY]", "AWS Secret Key"),
    ("openai_key", r"sk-[A-Za-z0-9]{32,}", "[OPENAI_KEY]", "OpenAI / Anthropic 风格 API Key"),
    ("github_token", r"gh[ps]_[A-Za-z0-9]{36,}", "[GITHUB_TOKEN]", "GitHub Token"),
    ("slack_token", r"xox[baprs]-[A-Za-z0-9-]{10,}", "[SLACK_TOKEN]", "Slack Token"),
    ("private_key", r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+PRIVATE KEY-----", "[PRIVATE_KEY]", "RSA/EC 私钥"),
    ("jwt", r"eyJ[\w-]+\.eyJ[\w-]+\.[\w-]+", "[JWT]", "JWT Token"),
    ("generic_api_key", r"(?i)(?:api[_-]?key|secret|token)['\"\s:=]+[\w\-]{24,}", "[API_KEY]", "通用 API Key 模式"),
    ("ssn_us", r"(?<![\d])\d{3}-\d{2}-\d{4}(?![\d])", "[SSN]", "美国社保号"),
]


class DLPDetector:
    """敏感信息检测 + 脱敏"""

    def __init__(self, extra_patterns: dict | None = None):
        self.rules: list[DLPRule] = [
            DLPRule(name, re.compile(pat), mask, desc)
            for name, pat, mask, desc in _DEFAULT_RULES
        ]
        if extra_patterns:
            for name, conf in extra_patterns.items():
                self.rules.append(
                    DLPRule(
                        name=name,
                        pattern=re.compile(conf["pattern"]),
                        mask=conf.get("mask", f"[{name.upper()}]"),
                        description=conf.get("description", name),
                    )
                )

    def scan(self, text: str) -> tuple[list[dict], str]:
        """返回 (命中项列表, 脱敏后文本)"""
        if not isinstance(text, str) or not text:
            return [], text or ""
        findings: list[dict] = []
        redacted = text
        for rule in self.rules:
            for m in rule.pattern.finditer(text):
                findings.append(
                    {
                        "type": rule.name,
                        "description": rule.description,
                        "value_preview": _preview(m.group()),
                        "span": list(m.span()),
                    }
                )
            redacted = rule.pattern.sub(rule.mask, redacted)
        return findings, redacted


def _preview(s: str, max_len: int = 12) -> str:
    """脱敏值预览：只保留前后各几位"""
    if len(s) <= max_len:
        return "*" * len(s)
    return s[:3] + "*" * (len(s) - 6) + s[-3:]
