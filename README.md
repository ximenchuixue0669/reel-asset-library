# Reel Asset Library

An MCP server for finding and managing sample media assets. The browser dashboard observes MCP traffic and applies controlled failures while a separate MCP client runs the tools.

## Run it

Use Python 3.11–3.14, [uv](https://docs.astral.sh/uv/getting-started/installation/), Node.js 20 or later, and `make`.

```sh
git clone https://github.com/ximenchuixue0669/reel-asset-library.git
cd reel-asset-library
make dev
```

The first run creates `.data/catalog.sqlite3` and 30 local media files. Open `http://127.0.0.1:8765/` for the dashboard. The Streamable HTTP endpoint is `http://127.0.0.1:8765/mcp`. Everything runs offline once dependencies are installed. No application model API key is needed.

```sh
make check
```

This rebuilds the TypeScript dashboard, checks Python style, and runs the catalog, SDK, and HTTP integration tests. Windows users without `make` can run `npm --prefix frontend ci`, `npm --prefix frontend run build`, and `uv run --locked python -m reel_library.app`.

## Connect an MCP client

Start [MCP Inspector](https://github.com/modelcontextprotocol/inspector) with `npx -y @modelcontextprotocol/inspector`. Select **Streamable HTTP**, enter the `/mcp` URL above, and connect. Use the dashboard in a separate tab. To use Codex CLI as the model-driven client, configure a server named `reel` with the same URL.

```toml
[mcp_servers.reel]
url = "http://127.0.0.1:8765/mcp"
```

The server exposes a `reel://catalog/statistics` resource and a `plan_project_organization` prompt. Discover both through the client.

## Tools

| Tool | When to use it |
| --- | --- |
| `search_assets` | Find active assets by project, tag, type, creation bounds, or relative folder. Continue from `next_offset`. |
| `get_asset` | Read metadata for one asset ID. |
| `update_asset_tags` | Apply distinct `add` and `remove` lists to one asset. |
| `delete_asset` | Preview a soft deletion or complete it with a human-approved code for that ID. |
| `generate_thumbnails` | Generate up to 12 JPEG thumbnails from active images. Accepts a project filter and supports progress and cancellation. |

Separate tools give the model small, distinct schemas and let clients identify the destructive operation before a call. `update_asset_tags` combines the two tag changes because they can be applied atomically to one asset. Each description identifies the appropriate input, the result or side effect, and the next step where applicable. The deletion description describes the human approval step, and the thumbnail description calls out the expected duration and cancellation.

## Guided demo

1. List tools, resources, and prompts in Inspector. Open the catalog statistics resource. Watch the calls appear on the dashboard.
2. Ask a model-driven client, “Find the two newest approved images for neon-harbor since January 6, 2026 UTC.” Inspect the resulting `search_assets` call and arguments in the dashboard.
3. Try `search_assets` with `{"type":"movie"}`. The result names the valid values. Retry with `{"type":"video","limit":1}`. Set **Next call returns an error** and make another tool call to verify the one-shot failure. **Tools are slow** adds two seconds per tool call.
4. Try `search_assets` with `{"path_prefix":"../../outside"}`. The error explains how to supply a library-relative folder. Try `{"path_prefix":"neon-harbor/image"}` next.
5. Call `delete_asset` with `{"asset_id":"asset-001"}`. Verify that the count stays at 30 and a pending request appears. The dashboard **Approve** button issues a one-time code. Paste it as `confirmation_code` on a second MCP call for the same ID to soft-delete it. **Reset data** restores the sample dataset. Keep the confirmation code out of screenshots and model conversations that are shared publicly.
6. With the dashboard open, run the following command. It sends an MCP cancellation after the second of four thumbnails. The progress bar stops at `2 / 4`, and the request is labelled **Cancelled**. A batch allowed to finish takes about 15 seconds and writes JPEG files under `.data/thumbnails/`.

```sh
uv run --locked python -m reel_library.demo_client
```

## Configuration and assumptions

See `.env.example` for environment variables. `REEL_ACCESS_TOKEN` enables an optional shared-token gate. MCP clients send a `Bearer` authorization header. The dashboard uses HTTP Basic with username `reel` and the token as the password. For a reverse proxy, set `REEL_PUBLIC_ORIGIN` to its scheme and authority and forward the original Host header. For a path prefix, proxy with the prefix stripped and open the dashboard at a URL with the trailing slash.

The media library and debug switches are shared by clients within one server process. Use a local instance when running an isolated walkthrough. Inputs and media are synthetic. Thumbnails target images. An interrupted batch publishes no output. The browser retains the latest 80 log rows and hides confirmation codes. See the [design notes](docs/design.md) for the data and concurrency boundaries.

## Source and evidence

Read this page first, then [the design notes](docs/design.md) and [the review notes](docs/review.md). `reel_library/` contains the Python service, `frontend/src/` contains the TypeScript and styles, and `tests/` covers the catalog and actual MCP sessions. See [AI usage and records](ai-logs/README.md) for the Codex tools, the recorded model walkthrough, and publication limitations.
