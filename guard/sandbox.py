"""策略沙箱：文件系统 / 网络 / Shell"""

from __future__ import annotations

import fnmatch
import ipaddress
import re
import shlex
from pathlib import Path
from urllib.parse import urlparse


class FilesystemPolicy:
    """文件系统策略

    - allowlist：允许的路径模式（glob）
    - denylist：禁止的路径模式（优先级高于 allowlist）
    - 路径在比对前会被规范化（resolve），防止 ../../ 穿越和软链接绕过
    """

    def __init__(self, allowlist: list[str], denylist: list[str]):
        self.allowlist = [self._expand(p) for p in allowlist]
        self.denylist = [self._expand(p) for p in denylist]

    @staticmethod
    def _expand(p: str) -> str:
        return str(Path(p).expanduser())

    def check(self, path: str) -> tuple[bool, str]:
        try:
            # 注意：strict=False 允许尚不存在的路径（比如 write 场景）
            resolved = Path(path).expanduser().resolve(strict=False)
        except (OSError, RuntimeError) as e:
            return False, f"路径无法规范化：{e}"
        abs_path = str(resolved)

        # 1) denylist 优先：匹配则一票否决
        for pattern in self.denylist:
            if self._match(pattern, abs_path):
                return False, f"命中 denylist：{pattern}（实际访问 {abs_path}）"

        # 2) allowlist：必须命中至少一条
        if not self.allowlist:
            return True, "未配置 allowlist，默认放行"
        for pattern in self.allowlist:
            if self._match(pattern, abs_path):
                return True, f"命中 allowlist：{pattern}"
        return False, f"路径不在 allowlist 中：{abs_path}"

    @staticmethod
    def _match(pattern: str, path: str) -> bool:
        # 同时支持 glob 和前缀匹配
        if fnmatch.fnmatch(path, pattern):
            return True
        # 处理目录前缀（如 ~/.ssh/** 也要拦 ~/.ssh 本身）
        if "**" in pattern:
            base = pattern.split("**")[0].rstrip("/")
            if base and (path == base or path.startswith(base + "/")):
                return True
        return False


class NetworkPolicy:
    """网络策略

    - allowed_domains：允许的域名（支持前导通配 *.foo.com）
    - blocked_domains：明确禁止的域名（优先级最高）
    - block_private_ip：默认 True，拦截链路本地 / 私网 / 元数据服务地址
    """

    def __init__(
        self,
        allowed_domains: list[str],
        blocked_domains: list[str],
        block_private_ip: bool = True,
    ):
        self.allowed = allowed_domains
        self.blocked = blocked_domains
        self.block_private_ip = block_private_ip

    def check(self, url: str) -> tuple[bool, str]:
        if not url:
            return False, "URL 为空"
        try:
            parsed = urlparse(url if "://" in url else f"http://{url}")
        except Exception as e:
            return False, f"URL 解析失败：{e}"

        host = (parsed.hostname or "").lower()
        if not host:
            return False, "无法提取主机名"

        # 1) IP 直连检查
        ip_obj = self._try_ip(host)
        if ip_obj is not None:
            if self.block_private_ip and (
                ip_obj.is_private
                or ip_obj.is_loopback
                or ip_obj.is_link_local
                or ip_obj.is_reserved
                or ip_obj.is_multicast
            ):
                return False, f"拦截内网/链路本地 IP（防 SSRF）：{host}"

        # 2) 黑名单
        for blocked in self.blocked:
            if self._match_domain(blocked, host):
                return False, f"命中域名 denylist：{blocked}"

        # 3) 白名单
        if not self.allowed:
            return True, "未配置 allowlist，默认放行"
        for allowed in self.allowed:
            if self._match_domain(allowed, host):
                return True, f"命中 allowlist：{allowed}"
        return False, f"域名 {host} 不在 allowlist 中"

    @staticmethod
    def _try_ip(host: str):
        try:
            return ipaddress.ip_address(host)
        except ValueError:
            return None

    @staticmethod
    def _match_domain(pattern: str, host: str) -> bool:
        pattern = pattern.lower().lstrip(".")
        if pattern.startswith("*."):
            suffix = pattern[2:]
            return host == suffix or host.endswith("." + suffix)
        return host == pattern or host.endswith("." + pattern)


class ShellPolicy:
    """Shell 命令策略

    - allowlist：允许执行的命令（按可执行名匹配）
    - blocked_patterns：明确禁止的子串/正则
    """

    def __init__(self, allowlist: list[str], blocked_patterns: list[str]):
        self.allowlist = [c.strip() for c in allowlist]
        self.blocked_patterns = [re.compile(p) for p in blocked_patterns]

    def check(self, command: str) -> tuple[bool, str]:
        if not command:
            return False, "命令为空"

        # 1) 黑名单优先（应对编码绕过 / 拼接）
        for pat in self.blocked_patterns:
            if pat.search(command):
                return False, f"命中危险模式：{pat.pattern}"

        # 2) 拒绝管道、命令拼接、子命令
        risky_chars = [";", "&&", "||", "|", "`", "$(", ">(", "<("]
        for ch in risky_chars:
            if ch in command:
                return False, f"包含命令拼接/管道字符：{ch}"

        # 3) 解析首个命令
        try:
            tokens = shlex.split(command)
        except ValueError as e:
            return False, f"命令解析失败：{e}"
        if not tokens:
            return False, "无可执行命令"

        head = tokens[0]
        # allowlist 同时支持 "git status" 这种带子命令前缀
        for allowed in self.allowlist:
            allowed_tokens = allowed.split()
            if tokens[: len(allowed_tokens)] == allowed_tokens:
                return True, f"命中 allowlist：{allowed}"

        return False, f"命令 {head} 不在 allowlist 中"
