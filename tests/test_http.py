import asyncio
import json
import socket
from contextlib import asynccontextmanager

import anyio
import httpx
import pytest
import uvicorn
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from reel_library.app import create_app

ACCEPT = {"Accept": "application/json, text/event-stream"}
DASHBOARD = {"x-reel-dashboard": "1"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@asynccontextmanager
async def running(app):
    with socket.socket() as binding:
        binding.bind(("127.0.0.1", 0))
        port = binding.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", timeout_graceful_shutdown=2))
        task = asyncio.create_task(server.serve(sockets=[binding]))
        try:
            with anyio.fail_after(4):
                while not server.started:
                    await anyio.sleep(0.01)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            await task


async def messages(response):
    async for line in response.aiter_lines():
        if line.startswith("data: ") and line[6:].strip():
            yield json.loads(line[6:])


@pytest.mark.anyio
async def test_inspector_flow_with_real_streamable_http_and_dashboard(tmp_path):
    app = create_app(tmp_path, thumbnail_seconds=2)
    async with (
        running(app) as base,
        httpx.AsyncClient(base_url=base, timeout=5) as http,
        streamable_http_client(f"{base}/mcp") as (read, write, _),
        ClientSession(read, write) as sdk,
    ):
        await sdk.initialize()
        listing = await sdk.list_tools()
        assert len(listing.tools) == 5
        await sdk.list_resources()
        await sdk.list_prompts()
        assert (await http.post("/api/debug/fail_next/on", headers=DASHBOARD)).is_success
        failure = await sdk.call_tool("search_assets", {})
        assert failure.isError and "Injected demo failure" in failure.content[0].text
        invalid = await sdk.call_tool("search_assets", {"type": "movie"})
        assert invalid.isError and "video" in invalid.content[0].text
        assert not (await sdk.call_tool("search_assets", {"type": "video"})).isError
        pending = await sdk.call_tool("delete_asset", {"asset_id": "asset-001"})
        assert pending.structuredContent["state"] == "pending_human_confirmation"
        state = (await http.get("/api/state")).json()
        assert state["catalog"]["total"] == 30
        approval_id = pending.structuredContent["approval_id"]
        path = f"/api/approvals/{approval_id}/approve"
        assert (await http.post(path)).status_code == 403
        token = (await http.post(path, headers=DASHBOARD)).json()["confirmation_code"]
        deleted = await sdk.call_tool(
            "delete_asset", {"asset_id": "asset-001", "confirmation_code": token}
        )
        assert deleted.structuredContent["state"] == "soft_deleted"
        async with http.stream("GET", "/api/events") as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            state = await anext(messages(response))
            assert token not in json.dumps(state)
            assert state["catalog"]["total"] == 29
            assert state["calls"][0]["parameters"]["arguments"]["confirmation_code"] == "[REDACTED]"
            assert any(row["state"] == "error" for row in state["calls"])
            assert {row["method"] for row in state["calls"]} >= {
                "initialize",
                "tools/list",
                "resources/list",
                "prompts/list",
                "tools/call",
            }
        assert (await http.post("/api/debug/reset", headers=DASHBOARD)).json()["total"] == 30


@pytest.mark.anyio
async def test_protocol_cancel_updates_log_and_cleans_partial_files(tmp_path):
    app = create_app(tmp_path, thumbnail_seconds=4)
    async with running(app) as base, httpx.AsyncClient(base_url=base, timeout=8) as http:
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": types.LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test-harness", "version": "1"},
            },
        }
        async with http.stream("POST", "/mcp", headers=ACCEPT, json=initialize) as response:
            response.raise_for_status()
            session_id = response.headers["Mcp-Session-Id"]
            version = (await anext(messages(response)))["result"]["protocolVersion"]
        headers = {**ACCEPT, "Mcp-Session-Id": session_id, "MCP-Protocol-Version": version}
        initialized = await http.post(
            "/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        assert initialized.status_code == 202
        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "generate_thumbnails",
                "arguments": {"project": "neon-harbor"},
                "_meta": {"progressToken": "example"},
            },
        }
        progress_seen = False
        async with http.stream("POST", "/mcp", headers=headers, json=call) as response:
            async for message in messages(response):
                if (
                    message.get("method") == "notifications/progress"
                    and message["params"]["progress"] == 1
                ):
                    progress_seen = True
                    state = (await http.get("/api/state")).json()
                    assert state["calls"][0]["progress"]["done"] == 1
                    assert (
                        await http.post("/api/debug/reset", headers=DASHBOARD)
                    ).status_code == 409
                    cancel = {
                        "jsonrpc": "2.0",
                        "method": "notifications/cancelled",
                        "params": {"requestId": 2, "reason": "integration test"},
                    }
                    assert (
                        await http.post("/mcp", headers=headers, json=cancel)
                    ).status_code == 202
                elif "id" in message:
                    break
        assert progress_seen
        with anyio.fail_after(3):
            while True:
                state = (await http.get("/api/state")).json()
                if state["debug"]["active_jobs"] == 0:
                    break
                await anyio.sleep(0.01)
        assert any(
            row["state"] == "cancelled" and row["progress"]["done"] == 1 for row in state["calls"]
        )
        assert list((tmp_path / "thumbnails").iterdir()) == []
        assert (await http.delete("/mcp", headers=headers)).is_success


@pytest.mark.anyio
async def test_access_gate_origin_and_body_limits(tmp_path):
    token = "local-test-access-value"
    async with (
        running(create_app(tmp_path, token=token)) as base,
        httpx.AsyncClient(base_url=base) as http,
    ):
        assert (await http.get("/healthz")).status_code == 200
        assert (await http.get("/api/state")).status_code == 401
        assert (await http.get("/api/state", auth=("reel", token))).status_code == 200
        assert (await http.post("/mcp", headers=ACCEPT, json={})).status_code == 401
        headers = {**ACCEPT, "Authorization": f"Bearer {token}"}
        rejected = await http.post(
            "/mcp", headers={**headers, "Origin": "https://untrusted.example"}, json={}
        )
        assert rejected.status_code == 403
        assert (
            await http.post("/mcp", headers=headers, json={"oversize": "x" * (65 * 1024)})
        ).status_code == 413
