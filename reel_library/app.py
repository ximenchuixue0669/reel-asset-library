"""One ASGI process for Streamable HTTP, dashboard events, and demo controls."""

import asyncio
import base64
import binascii
import hmac
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from dotenv import load_dotenv
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from reel_library.action_tools import action_tools
from reel_library.catalog import Catalog, CatalogError
from reel_library.operations import Operations
from reel_library.protocol import configure_server
from reel_library.seed import seed_demo
from reel_library.trace import CallLog, TracedServer

FRONTEND_ROOT = Path(__file__).resolve().parents[1] / "frontend" / "dist"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")
NO_STORE = {"Cache-Control": "no-store"}
PAGE_HEADERS = {
    **NO_STORE,
    "Content-Security-Policy": "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


class AccessGate:
    def __init__(self, app, token: str):
        self.app = app
        self.token = token.encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.token or scope["path"].endswith("/healthz"):
            await self.app(scope, receive, send)
            return
        is_mcp = scope["path"].endswith("/mcp")
        auth = Headers(scope=scope).get("authorization", "")
        scheme, _, value = auth.partition(" ")
        if scheme.lower() == "bearer":
            allowed = hmac.compare_digest(value.encode("utf-8"), self.token)
        elif scheme.lower() == "basic" and not is_mcp:
            try:
                parsed = base64.b64decode(value, validate=True)
                allowed = hmac.compare_digest(parsed, b"reel:" + self.token)
            except (binascii.Error, ValueError):
                allowed = False
        else:
            allowed = False
        if allowed:
            await self.app(scope, receive, send)
        else:
            challenge = 'Bearer realm="Reel Library"' if is_mcp else 'Basic realm="Reel Library"'
            response = PlainTextResponse(
                "Authentication required.",
                status_code=401,
                headers={"WWW-Authenticate": challenge, **NO_STORE},
            )
            await response(scope, receive, send)


class McpEndpoint:
    def __init__(self, sessions: StreamableHTTPSessionManager, log: CallLog):
        self.sessions = sessions
        self.log = log

    async def __call__(self, scope, receive, send):
        async def observe_response(message):
            if message["type"] == "http.response.start" and message["status"] >= 400:
                call = self.log.start(f"transport/{scope['method']}", {})
                self.log.finish(call, "error", error={"http_status": message["status"]})
            await send(message)

        await self.sessions.handle_request(scope, receive, observe_response)


def create_app(
    data_dir: Path,
    *,
    token="",
    public_origin="",
    frontend: Path = FRONTEND_ROOT,
    thumbnail_seconds=15.0,
):
    public = urlsplit(public_origin)
    if public_origin and (
        public.scheme not in ("http", "https")
        or not public.hostname
        or public.path
        or public.username
        or public.query
        or public.fragment
    ):
        raise ValueError("REEL_PUBLIC_ORIGIN must be a web origin without a path.")
    origins = [f"http://{host}{port}" for host in LOCAL_HOSTS for port in ("", ":*")]
    authorities = [f"{host}{port}" for host in LOCAL_HOSTS for port in ("", ":*")]
    if public_origin:
        origins.append(public_origin)
        authorities.append(public.netloc)

    catalog = Catalog(data_dir)
    seed_demo(catalog)
    call_log = CallLog()
    operations = Operations(catalog, call_log.changed, thumbnail_seconds=thumbnail_seconds)
    server = TracedServer(call_log)
    configure_server(
        server,
        catalog,
        extra_tools=action_tools(server, operations, server.dashboard_progress),
        before_call=operations.before_tool,
    )
    sessions = StreamableHTTPSessionManager(
        server,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=authorities, allowed_origins=origins
        ),
        max_sessions=32,
        max_request_body_size=64 * 1024,
        session_idle_timeout=900,
    )

    @asynccontextmanager
    async def lifespan(app):
        async with sessions.run():
            yield

    def snapshot() -> dict:
        return {
            "calls": call_log.snapshot(),
            "debug": operations.debug(),
            "approvals": operations.pending_approvals(),
            "catalog": catalog.statistics(),
        }

    async def index(request: Request):
        index_path = frontend / "index.html"
        if not index_path.is_file():
            return PlainTextResponse("Dashboard not built. Run make dev.", status_code=503)
        return FileResponse(index_path, headers=PAGE_HEADERS)

    async def health(request: Request):
        return JSONResponse(
            {"status": "ok", "build": os.environ.get("REEL_BUILD_SHA", "development")},
            headers=NO_STORE,
        )

    async def dashboard_state(request: Request):
        return JSONResponse(snapshot(), headers=NO_STORE)

    async def dashboard_events(request: Request):
        async def events():
            revision = -1
            while True:
                signal = call_log.signal
                if call_log.revision != revision:
                    revision = call_log.revision
                    data = snapshot()
                    approvals = data["approvals"]
                    yield f"event: snapshot\ndata: {json.dumps(data, ensure_ascii=True, separators=(',', ':'))}\n\n"
                try:
                    await asyncio.wait_for(signal.wait(), timeout=15)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    if operations.pending_approvals() != approvals:
                        revision = -1

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={**NO_STORE, "X-Accel-Buffering": "no"},
        )

    def dashboard_action(request: Request, operation):
        if request.headers.get("x-reel-dashboard") != "1":
            return JSONResponse(
                {"error": "Use the dashboard to change demo state."},
                status_code=403,
                headers=NO_STORE,
            )
        try:
            return JSONResponse(operation(), headers=NO_STORE)
        except CatalogError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409, headers=NO_STORE)

    async def debug_toggle(request: Request):
        name = request.path_params["name"]
        value = request.path_params["value"]
        if value not in {"on", "off"}:
            return JSONResponse({"error": "Use on or off."}, status_code=400, headers=NO_STORE)
        return dashboard_action(request, lambda: operations.set_debug(name, value == "on"))

    async def debug_reset(request: Request):
        return dashboard_action(request, operations.reset)

    async def approve_deletion(request: Request):
        approval_id = request.path_params["approval_id"]
        return dashboard_action(request, lambda: operations.approve_deletion(approval_id))

    routes = [
        Route("/", index),
        Route("/healthz", health),
        Route("/api/state", dashboard_state),
        Route("/api/events", dashboard_events),
        Route("/api/debug/reset", debug_reset, methods=["POST"]),
        Route("/api/debug/{name:str}/{value:str}", debug_toggle, methods=["POST"]),
        Route("/api/approvals/{approval_id:str}/approve", approve_deletion, methods=["POST"]),
        Route("/mcp", McpEndpoint(sessions, call_log)),
        Mount(
            "/assets", StaticFiles(directory=frontend / "assets", check_dir=False), name="assets"
        ),
    ]
    middleware = [
        Middleware(
            TrustedHostMiddleware,
            allowed_hosts=[
                "localhost",
                "127.0.0.1",
                "[::1]",
                *([public.hostname] if public.hostname else []),
            ],
        ),
        Middleware(AccessGate, token=token),
    ]
    return Starlette(routes=routes, lifespan=lifespan, middleware=middleware)


def main():
    load_dotenv()
    app = create_app(
        Path(os.environ.get("REEL_DATA_DIR", ".data")),
        token=os.environ.get("REEL_ACCESS_TOKEN", ""),
        public_origin=os.environ.get("REEL_PUBLIC_ORIGIN", ""),
    )
    uvicorn.run(
        app,
        host=os.environ.get("REEL_HOST", "127.0.0.1"),
        port=int(os.environ.get("REEL_PORT", "8765")),
        limit_concurrency=96,
    )


if __name__ == "__main__":
    main()
