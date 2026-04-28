"""MCP stdio 代理：在客户端 ↔ 上游 server 之间拦截 tools/call。

MCP stdio transport 协议：每条 JSON-RPC 2.0 消息一行（newline-delimited），
没有 Content-Length 头。

拦截策略：
  - method == "tools/call"  → 走 Guard.check_tool_call()
      · ALLOW   → 透传给上游
      · DENY    → 直接给客户端回 JSON-RPC error，不下发
      · REDACT  → 改写 params.arguments 后透传
      · ASK_USER → 写一条 pending_decisions 行 → 在线程池里同步等待
                   （PendingDecisions.wait 内部 sleep-poll，配合 run_in_executor
                    不阻塞 event loop）
  - 其他方法（initialize / tools/list / resources/* …）原样透传
  - 上游 → 客户端方向：v0.2 暂时只透传；W2 加 L4 DLP 输出脱敏
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from guard import Decision, Guard, GuardResult, ToolCall
from guard.detectors.dlp import DLPDetector


class MCPProxy:
    def __init__(
        self,
        upstream_cmd: list[str],
        guard: Guard,
        log_stream=None,
        dlp_outbound: bool = True,
    ):
        if not upstream_cmd:
            raise ValueError("upstream_cmd 必须给出至少一个参数")
        self.upstream_cmd = upstream_cmd
        self.guard = guard
        self.log = log_stream or sys.stderr  # 不能写 stdout，会污染 JSON-RPC 流
        self.upstream: asyncio.subprocess.Process | None = None
        self._stdout_lock = asyncio.Lock()
        # L4 出向 DLP：上游返回的 tools/call result 里出现的敏感数据原地脱敏
        self.dlp_outbound = dlp_outbound
        self._dlp = DLPDetector() if dlp_outbound else None

    async def run(self) -> int:
        self.upstream = await asyncio.create_subprocess_exec(
            *self.upstream_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=sys.stderr,
        )
        self._info(f"upstream spawned: {' '.join(self.upstream_cmd)} (pid={self.upstream.pid})")

        try:
            await asyncio.gather(
                self._client_to_upstream(),
                self._upstream_to_client(),
            )
        finally:
            if self.upstream and self.upstream.returncode is None:
                self.upstream.terminate()
                try:
                    await asyncio.wait_for(self.upstream.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    self.upstream.kill()

        return self.upstream.returncode if self.upstream else 0

    # ---- 方向 1：client → proxy → upstream -----------------------

    async def _client_to_upstream(self) -> None:
        reader = await self._wrap_stdin()
        assert self.upstream and self.upstream.stdin
        while True:
            line = await reader.readline()
            if not line:
                self.upstream.stdin.close()
                return
            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue
            try:
                msg = json.loads(decoded)
            except json.JSONDecodeError:
                self._info(f"非 JSON 行透传: {decoded[:80]}")
                self.upstream.stdin.write(line)
                await self.upstream.stdin.drain()
                continue

            forwarded = await self._handle_client_msg(msg)
            if forwarded is not None:
                self.upstream.stdin.write((json.dumps(forwarded, ensure_ascii=False) + "\n").encode("utf-8"))
                await self.upstream.stdin.drain()

    async def _handle_client_msg(self, msg: dict) -> dict | None:
        if msg.get("method") != "tools/call":
            return msg

        params = msg.get("params") or {}
        tool_name = params.get("name", "<unknown>")
        args = params.get("arguments") or {}
        msg_id = msg.get("id")

        call = ToolCall(tool_name=tool_name, args=args, source="mcp")
        # Guard.check_tool_call 是同步的；当工具需要 user authz 时，回调里
        # 会 sleep-poll 等审批结果。把整个调用挪到线程池避免阻塞 event loop，
        # 这样代理仍能在等待审批时收发其他 JSON-RPC 消息。
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, self.guard.check_tool_call, call)

        self._info(
            f"tool_call name={tool_name} id={msg_id} "
            f"decision={result.decision.value} risk={result.risk_score:.2f} "
            f"rules={result.triggered_rules}"
        )

        if result.decision == Decision.ALLOW:
            return msg

        if result.decision == Decision.REDACT and result.redacted_args is not None:
            params["arguments"] = result.redacted_args
            msg["params"] = params
            return msg

        # DENY（含审批被拒 / 审批超时）→ 给客户端回 error，不下发
        await self._send_to_client(self._make_error(msg_id, result))
        return None

    # ---- 方向 2：upstream → proxy → client -----------------------

    async def _upstream_to_client(self) -> None:
        assert self.upstream and self.upstream.stdout
        while True:
            line = await self.upstream.stdout.readline()
            if not line:
                return
            redacted_line = self._maybe_redact_line(line) if self._dlp else line
            await self._write_stdout(redacted_line)

    def _maybe_redact_line(self, line: bytes) -> bytes:
        """L4 出向 DLP：扫描 tools/call result 里的文本，命中即原地脱敏。

        MCP 响应结构通常是：
            {"jsonrpc":"2.0","id":N,"result":{"content":[{"type":"text","text":"..."}], ...}}
        我们对所有 result.content[*].text 走 DLP，命中即替换并写一条审计。
        其他响应（initialize / tools/list / 错误帧 / notifications）原样透传。
        """
        try:
            decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not decoded:
                return line
            msg = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return line
        if not isinstance(msg, dict):
            return line

        result = msg.get("result")
        if not isinstance(result, dict):
            return line
        content = result.get("content")
        if not isinstance(content, list):
            return line

        total_findings: list[dict] = []
        any_redacted = False
        for item in content:
            if not isinstance(item, dict):
                continue
            txt = item.get("text")
            if not isinstance(txt, str) or not txt:
                continue
            findings, redacted = self._dlp.scan(txt)
            if findings:
                item["text"] = redacted
                total_findings.extend(findings)
                any_redacted = True

        if not any_redacted:
            return line

        types = sorted({f["type"] for f in total_findings})
        self._info(
            f"dlp_outbound id={msg.get('id')} hits={len(total_findings)} types={types}"
        )
        try:
            self.guard.audit.log_event(
                event_type="output_check",
                tool_name=f"resp_id={msg.get('id', '?')}",
                args={"findings": len(total_findings), "types": types},
                result=GuardResult(
                    decision=Decision.REDACT,
                    reason=f"L4 出向 DLP 命中 {len(total_findings)} 处",
                    risk_score=min(0.3 + 0.1 * len(total_findings), 1.0),
                    triggered_rules=[f"dlp:{t}" for t in types],
                ),
            )
        except Exception:
            pass
        return (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")

    # ---- helpers ---------------------------------------------------

    async def _wrap_stdin(self) -> asyncio.StreamReader:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        return reader

    async def _send_to_client(self, msg: dict) -> None:
        line = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        await self._write_stdout(line)

    async def _write_stdout(self, data: bytes) -> None:
        async with self._stdout_lock:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

    @staticmethod
    def _make_error(msg_id: Any, result) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {
                "code": -32000,
                "message": f"[Sentinel-MCP] blocked: {result.reason}",
                "data": {
                    "decision": result.decision.value,
                    "risk_score": result.risk_score,
                    "triggered_rules": result.triggered_rules,
                },
            },
        }

    def _info(self, msg: str) -> None:
        try:
            self.log.write(f"[sentinel-mcp] {msg}\n")
            self.log.flush()
        except Exception:
            pass
