import json

import pytest
from mcp.server import Server
from mcp.shared.memory import create_connected_server_and_client_session

from reel_library.catalog import Catalog
from reel_library.protocol import PROMPT_NAME, STATS_URI, configure_server
from reel_library.seed import seed_demo


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def protocol_server(tmp_path):
    catalog = Catalog(tmp_path)
    seed_demo(catalog)
    return configure_server(Server("test"), catalog)


@pytest.mark.anyio
async def test_tool_discovery_and_calls(protocol_server):
    async with create_connected_server_and_client_session(protocol_server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert set(tools) == {"search_assets", "get_asset", "update_asset_tags"}
        assert tools["get_asset"].inputSchema["required"] == ["asset_id"]
        assert tools["get_asset"].inputSchema["additionalProperties"] is False
        types = tools["search_assets"].inputSchema["properties"]["type"]["anyOf"][0]["enum"]
        assert types == ["image", "video", "audio"]
        first = await client.call_tool(
            "search_assets", {"type": "image", "project": "neon-harbor", "limit": 1}
        )
        assert first.structuredContent["next_offset"] == 1
        asset_id = first.structuredContent["assets"][0]["id"]
        detail = await client.call_tool("get_asset", {"asset_id": asset_id})
        assert detail.structuredContent["id"] == asset_id
        result = await client.call_tool(
            "update_asset_tags", {"asset_id": asset_id, "add": ["Hero"]}
        )
        assert "hero" in result.structuredContent["tags"]


@pytest.mark.anyio
async def test_validation_explains_how_to_correct_calls(protocol_server):
    async with create_connected_server_and_client_session(protocol_server) as client:
        for arguments, field in [
            ({"type": "movie"}, "type"),
            ({"path_prefix": "../../outside"}, "library-relative path"),
            ({"limit": "2"}, "limit"),
            ({"created_from": "2026-01-01T12:00:00"}, "created_from"),
            ({"created_from": 1789100000}, "created_from"),
        ]:
            result = await client.call_tool("search_assets", arguments)
            assert result.isError and result.structuredContent["code"] == "INVALID_ARGUMENTS"
            assert field in result.content[0].text
        result = await client.call_tool("get_asset", {})
        assert result.isError and "asset_id" in result.content[0].text
        corrected = await client.call_tool(
            "search_assets", {"type": "video", "created_from": "2026-01-01T00:00:00Z"}
        )
        assert not corrected.isError and len(corrected.structuredContent["assets"]) == 9


@pytest.mark.anyio
async def test_resource_and_prompt(protocol_server):
    async with create_connected_server_and_client_session(protocol_server) as client:
        assert str((await client.list_resources()).resources[0].uri) == STATS_URI
        statistics = await client.read_resource(STATS_URI)
        assert json.loads(statistics.contents[0].text)["total"] == 30
        assert (await client.list_prompts()).prompts[0].name == PROMPT_NAME
        prompt = await client.get_prompt(PROMPT_NAME, {"project": "neon-harbor"})
        assert "neon-harbor" in prompt.messages[0].content.text
