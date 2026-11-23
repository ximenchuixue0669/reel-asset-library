import anyio
import pytest
from mcp.server import Server
from mcp.shared.memory import create_connected_server_and_client_session
from PIL import Image

from reel_library.action_tools import action_tools
from reel_library.catalog import Catalog, CatalogError
from reel_library.operations import Operations
from reel_library.protocol import configure_server
from reel_library.seed import seed_demo


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def operations(tmp_path):
    catalog = Catalog(tmp_path)
    seed_demo(catalog)
    return Operations(catalog, lambda: None, thumbnail_seconds=0.06)


def test_approval_is_scoped_one_time_and_soft(operations):
    pending = operations.delete_asset("asset-001", None)
    assert pending["state"] == "pending_human_confirmation"
    assert "confirmation_code" not in pending
    assert operations.catalog.get("asset-001")["id"] == "asset-001"
    code = operations.approve_deletion(pending["approval_id"])["confirmation_code"]
    with pytest.raises(CatalogError, match="Invalid or expired"):
        operations.delete_asset("asset-002", code)
    assert operations.delete_asset("asset-001", code)["state"] == "soft_deleted"
    with pytest.raises(CatalogError, match="Invalid or expired"):
        operations.delete_asset("asset-001", code)
    assert operations.catalog.source_path(pending["asset"]["path"]).is_file()
    assert operations.reset()["total"] == 30


def test_expired_code_cannot_delete(operations, monkeypatch):
    now = 1000
    monkeypatch.setattr("reel_library.operations.monotonic", lambda: now)
    pending = operations.delete_asset("asset-001", None)
    code = operations.approve_deletion(pending["approval_id"])["confirmation_code"]
    now += 181
    with pytest.raises(CatalogError, match="expired"):
        operations.delete_asset("asset-001", code)
    assert operations.catalog.get("asset-001")["id"] == "asset-001"


@pytest.mark.anyio
async def test_mcp_progress_and_real_thumbnails(operations):
    server = Server("test")
    configure_server(server, operations.catalog, extra_tools=action_tools(server, operations))
    events = []

    async def progress(done, total, message):
        events.append((done, total))

    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool(
            "generate_thumbnails", {"project": "neon-harbor"}, progress_callback=progress
        )
    assert not result.isError
    assert events == [(i, 4) for i in range(5)]
    for relative_path in result.structuredContent["thumbnail_paths"]:
        with Image.open(operations.catalog.data_dir / relative_path) as thumbnail:
            assert thumbnail.format == "JPEG" and thumbnail.size == (240, 135)


@pytest.mark.anyio
async def test_cancel_discards_staged_files_and_allows_reset(operations):
    with anyio.CancelScope() as scope:

        async def cancel_after_first_file(done, total, message):
            if done == 1:
                with pytest.raises(CatalogError, match="active"):
                    operations.reset()
                scope.cancel()
                await anyio.lowlevel.checkpoint()

        await operations.generate_thumbnails(None, cancel_after_first_file)
    assert scope.cancelled_caught
    assert list(operations.thumbnails.iterdir()) == []
    assert operations.active_jobs == 0
    assert operations.reset()["total"] == 30


@pytest.mark.anyio
async def test_injected_failure_is_consumed_once(operations):
    operations.set_debug("fail_next", True)
    with pytest.raises(CatalogError, match="Injected demo failure"):
        await operations.before_tool()
    await operations.before_tool()
    assert operations.debug()["fail_next"] is False
