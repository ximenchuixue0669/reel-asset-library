"""MCP wrappers for operations that need approval or progress reporting."""

from typing import Annotated

from mcp import types
from mcp.server import Server
from pydantic import Field

from reel_library.operations import Operations
from reel_library.protocol import AssetArgs, Project, ToolArgs, ToolSpec

ConfirmationCode = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{32}$")]


class DeleteArgs(AssetArgs):
    confirmation_code: ConfirmationCode | None = Field(
        None,
        description="Omit this to preview a soft deletion. To confirm, use the one-time code given by a human after approving this asset on the dashboard.",
    )


class ThumbnailArgs(ToolArgs):
    project: Project | None = None


def action_tools(
    server: Server, operations: Operations, dashboard_progress=None
) -> dict[str, ToolSpec]:
    async def delete_asset(params: DeleteArgs) -> dict:
        return operations.delete_asset(params.asset_id, params.confirmation_code)

    async def generate_thumbnails(params: ThumbnailArgs) -> dict:
        context = server.request_context
        token = context.meta.progressToken if context.meta else None

        async def progress(done: int, total: int, message: str) -> None:
            if dashboard_progress is not None:
                dashboard_progress(context.request_id, done, total, message)
            if token is not None:
                await context.session.send_progress_notification(token, done, total, message)

        return await operations.generate_thumbnails(params.project, progress)

    return {
        "delete_asset": ToolSpec(
            DeleteArgs,
            "Request a SOFT deletion for one known asset ID. First call with just asset_id to preview. Ask a human to review the pending request on the dashboard and provide a one-time code. Submit that code with the same asset_id only after approval. The original source file stays on disk.",
            delete_asset,
            types.ToolAnnotations(
                readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
            ),
        ),
        "generate_thumbnails": ToolSpec(
            ThumbnailArgs,
            "Generate JPEG thumbnails from up to 12 active images, optionally within one project. Takes about 15 seconds. Supply an MCP progress token to receive updates. Cancel via the MCP client to discard the in-progress batch. On success, returns relative paths to the generated thumbnails.",
            generate_thumbnails,
            types.ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
            ),
        ),
    }
