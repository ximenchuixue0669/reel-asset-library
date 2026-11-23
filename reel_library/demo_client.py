"""A small wire client that sends an explicit MCP cancellation midway through a batch."""

import argparse
import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator

import httpx
from mcp.types import LATEST_PROTOCOL_VERSION

ACCEPT = {"Accept": "application/json, text/event-stream"}


async def messages(response: httpx.Response) -> AsyncIterator[dict]:
    response.raise_for_status()
    if response.headers.get("content-type", "").startswith("application/json"):
        yield json.loads(await response.aread())
        return
    async for line in response.aiter_lines():
        if line.startswith("data: ") and line[6:].strip():
            yield json.loads(line[6:])


async def initialize(http: httpx.AsyncClient, url: str) -> dict[str, str]:
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "reel-cancellation-demo", "version": "1.0"},
        },
    }
    async with http.stream("POST", url, headers=ACCEPT, json=request) as response:
        reply = await anext(messages(response))
        headers = {
            **ACCEPT,
            "Mcp-Session-Id": response.headers["Mcp-Session-Id"],
            "MCP-Protocol-Version": reply["result"]["protocolVersion"],
        }
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    (await http.post(url, headers=headers, json=initialized)).raise_for_status()
    return headers


async def cancel_demo(url: str, project: str, cancel_at: int) -> None:
    token = os.environ.get("REEL_ACCESS_TOKEN", "")
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(headers=auth, timeout=25) as http:
        headers = await initialize(http, url)
        try:
            request = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "generate_thumbnails",
                    "arguments": {"project": project},
                    "_meta": {"progressToken": "reel-demo"},
                },
            }
            cancellation_sent = False
            async with http.stream("POST", url, headers=headers, json=request) as response:
                async for message in messages(response):
                    if message.get("method") == "notifications/progress":
                        update = message["params"]
                        print(
                            f"Progress {update['progress']:g}/{update['total']:g} · {update.get('message', '')}",
                            flush=True,
                        )
                        if (
                            not cancellation_sent
                            and cancel_at <= update["progress"] < update["total"]
                        ):
                            cancellation = {
                                "jsonrpc": "2.0",
                                "method": "notifications/cancelled",
                                "params": {
                                    "requestId": 2,
                                    "reason": "Demonstration of mid-batch cancellation",
                                },
                            }
                            (
                                await http.post(url, headers=headers, json=cancellation)
                            ).raise_for_status()
                            cancellation_sent = True
                            print("Sent notifications/cancelled for request 2.", flush=True)
                    elif message.get("id") == 2:
                        if "error" in message and not cancellation_sent:
                            raise RuntimeError(message["error"]["message"])
                        if not cancellation_sent:
                            raise RuntimeError(
                                f"Batch finished before the cancellation point. Result: {message.get('result')}"
                            )
                        if "result" in message:
                            raise RuntimeError("The server returned a result after cancellation.")
            if not cancellation_sent:
                raise RuntimeError("The batch ended before the MCP client could cancel it.")
            print("Inspect the Cancelled row in the live call log.")
        finally:
            (await http.delete(url, headers=headers)).raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url", default="http://127.0.0.1:8765/mcp", help="Streamable HTTP MCP endpoint"
    )
    parser.add_argument("--project", default="neon-harbor")
    parser.add_argument(
        "--at", type=int, default=2, help="Cancel after this many thumbnails have been prepared"
    )
    options = parser.parse_args()
    if options.at < 1:
        parser.error("--at must be positive")
    try:
        asyncio.run(cancel_demo(options.url, options.project, options.at))
    except (httpx.HTTPError, RuntimeError) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
