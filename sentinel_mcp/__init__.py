"""Sentinel-MCP · 在 MCP 客户端（Cursor / Claude Desktop）和上游 MCP Server 之间
插一层安全代理。所有 tools/call 经过 Guard 决策；DENY 直接返回错误响应不下发；
REDACT 改写参数后下发；ALLOW 透传。"""

from sentinel_mcp.proxy import MCPProxy

__version__ = "0.2.0-dev"
__all__ = ["MCPProxy", "__version__"]
