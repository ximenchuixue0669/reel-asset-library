"""Observe the SDK's public message streams without changing protocol behavior."""

import asyncio
import json
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import monotonic, time

import anyio
from mcp import types
from mcp.server import Server
from mcp.shared.message import SessionMessage

MAX_CALLS = 80
MAX_PAYLOAD_CHARS = 12000
SENSITIVE_KEYS = {
    "apikey",
    "authorization",
    "confirmationcode",
    "cookie",
    "password",
    "secret",
    "token",
}


def _scrub(value, depth=0):
    if depth >= 14:
        return "[MAX_DEPTH]"
    if isinstance(value, dict):
        sanitized = {}
        for key, child in list(value.items())[:60]:
            normalized = key.lower().replace("_", "").replace("-", "")
            sanitized[key[:160]] = (
                "[REDACTED]" if normalized in SENSITIVE_KEYS else _scrub(child, depth + 1)
            )
        return sanitized
    if isinstance(value, list):
        return [_scrub(child, depth + 1) for child in value[:60]]
    if isinstance(value, str) and len(value) > 1500:
        return value[:1500] + " …"
    return value


def sanitized_payload(value):
    clean = _scrub(value)
    serialized = json.dumps(clean, ensure_ascii=True, separators=(",", ":"))
    if len(serialized) > MAX_PAYLOAD_CHARS:
        return {"truncated": True, "preview": serialized[:MAX_PAYLOAD_CHARS]}
    return clean


@dataclass
class Call:
    method: str
    parameters: dict
    id: int
    started: float = field(default_factory=monotonic)
    started_at_ms: int = field(default_factory=lambda: int(time() * 1000))
    state: str = "running"
    duration_ms: float | None = None
    result: dict | None = None
    error: dict | None = None
    progress: dict | None = None

    def public(self) -> dict:
        return {
            "id": self.id,
            "method": self.method,
            "parameters": self.parameters,
            "started_at_ms": self.started_at_ms,
            "state": self.state,
            "duration_ms": self.duration_ms,
            "result": self.result,
            "error": self.error,
            "progress": self.progress,
        }


class CallLog:
    def __init__(self):
        self._calls: OrderedDict[int, Call] = OrderedDict()
        self.revision = 0
        self._next_id = 0
        self.signal = asyncio.Event()

    def changed(self) -> None:
        self.revision += 1
        self.signal.set()
        self.signal = asyncio.Event()

    def start(self, method: str, parameters: dict) -> Call:
        self._next_id += 1
        call = Call(method, sanitized_payload(parameters), self._next_id)
        self._calls[call.id] = call
        if len(self._calls) > MAX_CALLS:
            oldest_completed = next(
                (key for key, value in self._calls.items() if value.state != "running"), None
            )
            self._calls.pop(
                oldest_completed if oldest_completed is not None else next(iter(self._calls))
            )
        self.changed()
        return call

    def finish(self, call: Call, state: str, *, result=None, error=None) -> None:
        if call.state != "running":
            return
        call.state = state
        call.duration_ms = round((monotonic() - call.started) * 1000, 1)
        call.result = sanitized_payload(result)
        call.error = sanitized_payload(error)
        self.changed()

    def snapshot(self) -> list[dict]:
        return [call.public() for call in reversed(self._calls.values())]


class SessionTrace:
    def __init__(self, log: CallLog):
        self.log = log
        self.pending: dict[types.RequestId, Call] = {}

    def inbound(self, message: SessionMessage | Exception) -> None:
        if isinstance(message, Exception):
            return
        rpc = message.message.root
        if isinstance(rpc, types.JSONRPCRequest):
            if (previous := self.pending.pop(rpc.id, None)) is not None:
                self.log.finish(
                    previous, "error", error={"message": "Client reused an in-flight request ID."}
                )
            self.pending[rpc.id] = self.log.start(rpc.method, rpc.params or {})
        elif isinstance(rpc, types.JSONRPCNotification):
            call = self.log.start(rpc.method, rpc.params or {})
            self.log.finish(call, "received")

    def outbound(self, message: SessionMessage) -> None:
        rpc = message.message.root
        if not isinstance(rpc, types.JSONRPCResponse | types.JSONRPCError):
            return
        call = self.pending.pop(rpc.id, None)
        if call is None:
            return
        if isinstance(rpc, types.JSONRPCError):
            state = (
                "cancelled"
                if rpc.error.code == 0 and rpc.error.message == "Request cancelled"
                else "error"
            )
            self.log.finish(
                call, state, error={"code": rpc.error.code, "message": rpc.error.message}
            )
        elif rpc.result.get("isError"):
            self.log.finish(
                call,
                "error",
                error=rpc.result.get("structuredContent") or rpc.result.get("content"),
            )
        else:
            result = rpc.result.get("structuredContent", rpc.result)
            self.log.finish(call, "complete", result=result)

    def progress(self, request_id: types.RequestId, done: int, total: int, message: str) -> None:
        if (call := self.pending.get(request_id)) is not None:
            call.progress = {"done": done, "total": total, "message": message}
            self.log.changed()

    def close(self) -> None:
        for call in self.pending.values():
            self.log.finish(call, "disconnected", error={"message": "The MCP session closed."})
        self.pending.clear()


_current_trace: ContextVar[SessionTrace] = ContextVar("reel_session_trace")


class TracedServer(Server):
    def __init__(self, log: CallLog):
        super().__init__("reel-asset-library", version="0.1.0")
        self.log = log

    @staticmethod
    def dashboard_progress(
        request_id: types.RequestId, done: int, total: int, message: str
    ) -> None:
        _current_trace.get().progress(request_id, done, total, message)

    async def run(
        self,
        read_stream,
        write_stream,
        initialization_options,
        raise_exceptions=False,
        stateless=False,
    ):
        trace = SessionTrace(self.log)
        token = _current_trace.set(trace)
        inbound_write, inbound_read = anyio.create_memory_object_stream[SessionMessage | Exception](
            0
        )
        outbound_write, outbound_read = anyio.create_memory_object_stream[SessionMessage](0)

        async def relay_inbound():
            async with inbound_write:
                async for message in read_stream:
                    trace.inbound(message)
                    await inbound_write.send(message)

        async def relay_outbound():
            async for message in outbound_read:
                trace.outbound(message)
                await write_stream.send(message)

        try:
            async with (
                read_stream,
                write_stream,
                inbound_read,
                outbound_read,
                outbound_write,
                anyio.create_task_group() as tasks,
            ):
                tasks.start_soon(relay_inbound)
                tasks.start_soon(relay_outbound)
                try:
                    await super().run(
                        inbound_read,
                        outbound_write,
                        initialization_options,
                        raise_exceptions,
                        stateless,
                    )
                finally:
                    tasks.cancel_scope.cancel()
        finally:
            trace.close()
            _current_trace.reset(token)
