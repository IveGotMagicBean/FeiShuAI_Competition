"""加载 YAML 配置并构建策略对象"""

from __future__ import annotations

from guard.sandbox import FilesystemPolicy, NetworkPolicy, ShellPolicy


def load_policies(config: dict) -> dict:
    """从配置 dict 构造各类策略对象"""
    fs_cfg = config.get("filesystem", {})
    net_cfg = config.get("network", {})
    shell_cfg = config.get("shell", {})

    return {
        "filesystem": FilesystemPolicy(
            allowlist=fs_cfg.get("allowlist", []),
            denylist=fs_cfg.get("denylist", []),
        ),
        "network": NetworkPolicy(
            allowed_domains=net_cfg.get("allowed_domains", []),
            blocked_domains=net_cfg.get("blocked_domains", []),
            block_private_ip=net_cfg.get("block_private_ip", True),
        ),
        "shell": ShellPolicy(
            allowlist=shell_cfg.get("allowlist", []),
            blocked_patterns=shell_cfg.get("blocked_patterns", []),
        ),
    }
