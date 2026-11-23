"""Model-facing tools, resource, prompt, and correctable validation errors."""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated

from mcp import McpError, types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from reel_library.catalog import (
    PATH_SEGMENT,
    AssetType,
    Catalog,
    CatalogError,
    Search,
    check_relative_path,
)

LOGGER = logging.getLogger(__name__)
STATS_URI = "reel://catalog/statistics"
PROMPT_NAME = "plan_project_organization"
PATH_PREFIX_PATTERN = rf"^{PATH_SEGMENT.pattern}(?:/{PATH_SEGMENT.pattern}){{0,7}}$"
AssetId = Annotated[
    str,
    Field(
        pattern=r"^asset-[0-9]{3}$", description="Asset ID from search_assets, such as asset-001."
    ),
]
Project = Annotated[
    str,
    Field(
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,47}$", description="Project slug, such as neon-harbor."
    ),
]
Tag = Annotated[
    str,
    Field(
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,31}$",
        description="One tag such as night or approved. Matching is case-insensitive.",
    ),
]


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SearchArgs(ToolArgs):
    tag: Tag | None = None
    type: AssetType | None = Field(None, description="Media type: image, video, or audio.")
    project: Project | None = None
    path_prefix: str | None = Field(
        None,
        max_length=255,
        json_schema_extra={"pattern": PATH_PREFIX_PATTERN},
        description="Library-relative folder such as neon-harbor/image. Use slash-separated folders without a leading or trailing slash or dot segments.",
    )
    created_from: AwareDatetime | None = Field(
        None, description="Inclusive lower bound as an RFC 3339 date-time with a time zone."
    )
    created_before: AwareDatetime | None = Field(
        None, description="Exclusive upper bound as an RFC 3339 date-time with a time zone."
    )
    limit: int = Field(10, ge=1, le=20, description="Maximum results in one page (1–20).")
    offset: int = Field(
        0,
        ge=0,
        le=10000,
        description="Start at 0. To continue, use next_offset returned by the previous page.",
    )

    @field_validator("path_prefix")
    @classmethod
    def validate_path(cls, path: str | None) -> str | None:
        return check_relative_path(path) if path is not None else None

    @model_validator(mode="after")
    def validate_dates(self):
        if (
            self.created_from is not None
            and self.created_before is not None
            and self.created_from >= self.created_before
        ):
            raise ValueError("created_before must be later than created_from.")
        return self


class AssetArgs(ToolArgs):
    asset_id: AssetId


class TagArgs(AssetArgs):
    add: list[Tag] = Field(
        default_factory=list, max_length=12, description="Tags to add. Existing tags stay in place."
    )
    remove: list[Tag] = Field(
        default_factory=list, max_length=12, description="Tags to remove. Absent tags are ignored."
    )

    @model_validator(mode="after")
    def validate_changes(self):
        self.add = sorted({tag.lower() for tag in self.add})
        self.remove = sorted({tag.lower() for tag in self.remove})
        if not self.add and not self.remove:
            raise ValueError("Provide at least one tag in add or remove.")
        if set(self.add).intersection(self.remove):
            raise ValueError("Use disjoint add and remove lists for one call.")
        return self


class ProjectArgs(ToolArgs):
    project: Project


@dataclass(frozen=True)
class ToolSpec:
    args: type[ToolArgs]
    description: str
    handler: Callable[..., Awaitable[dict]]
    annotations: types.ToolAnnotations = field(default_factory=types.ToolAnnotations)


def validation_message(error: ValidationError) -> str:
    issues = error.errors(include_url=False, include_context=False, include_input=False)
    return " ".join(
        f"{'.'.join(map(str, issue['loc'])) or 'arguments'}: {issue['msg'].removeprefix('Value error, ')}"
        for issue in issues[:6]
    )


def call_error(code: str, message: str) -> types.CallToolResult:
    return types.CallToolResult(
        isError=True,
        content=[types.TextContent(type="text", text=message)],
        structuredContent={"code": code, "message": message},
    )


def configure_server(
    server: Server, catalog: Catalog, *, extra_tools=None, before_call=None
) -> Server:
    async def search_assets(params: SearchArgs) -> dict:
        return catalog.search(Search(**params.model_dump()))

    async def get_asset(params: AssetArgs) -> dict:
        return catalog.get(params.asset_id)

    async def update_asset_tags(params: TagArgs) -> dict:
        return catalog.update_tags(params.asset_id, add=params.add, remove=params.remove)

    read_only = types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    tools = {
        "search_assets": ToolSpec(
            SearchArgs,
            "Find active media assets by project, tag, type, creation time, or relative folder. Filters are combined with AND. Use this when you need an asset ID or a list of matching assets. Results are newest first. Follow next_offset for another page.",
            search_assets,
            read_only,
        ),
        "get_asset": ToolSpec(
            AssetArgs,
            "Read the complete metadata for one active asset when you have its ID. Use search_assets when you only know a project, tag, or file folder. Returns a relative path, not file contents.",
            get_asset,
            read_only,
        ),
        "update_asset_tags": ToolSpec(
            TagArgs,
            "Add tags, remove tags, or do both in one call for a known asset ID. Use distinct add and remove lists. This action changes metadata without modifying source media.",
            update_asset_tags,
            types.ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
            ),
        ),
        **(extra_tools or {}),
    }

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=name,
                description=spec.description,
                inputSchema=spec.args.model_json_schema(),
                annotations=spec.annotations,
            )
            for name, spec in tools.items()
        ]

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict):
        spec = tools.get(name)
        if spec is None:
            return call_error(
                "UNKNOWN_TOOL", "Unknown tool. Use tools/list and choose an available name."
            )
        try:
            if before_call is not None:
                await before_call()
            params = spec.args.model_validate_json(json.dumps(arguments))
            return await spec.handler(params)
        except ValidationError as exc:
            return call_error("INVALID_ARGUMENTS", validation_message(exc))
        except CatalogError as exc:
            return call_error("CATALOG_ERROR", str(exc))
        except Exception:
            LOGGER.exception("MCP tool failed: %s", name)
            return call_error(
                "INTERNAL_ERROR",
                "The catalog could not finish this call. Retry once or check the server.",
            )

    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        return [
            types.Resource(
                uri=STATS_URI,
                name="catalog_statistics",
                description="Active catalog counts by media type and project.",
                mimeType="application/json",
            )
        ]

    @server.read_resource()
    async def read_resource(uri):
        if str(uri) != STATS_URI:
            raise McpError(
                types.ErrorData(
                    code=types.INVALID_PARAMS,
                    message="Unknown resource URI. Use resources/list to find a valid URI.",
                )
            )
        return [
            ReadResourceContents(
                content=json.dumps(catalog.statistics()), mime_type="application/json"
            )
        ]

    @server.list_prompts()
    async def list_prompts() -> list[types.Prompt]:
        return [
            types.Prompt(
                name=PROMPT_NAME,
                description="Draft a safe tagging plan for a project after inspecting its assets.",
                arguments=[
                    types.PromptArgument(
                        name="project",
                        required=True,
                        description="Project slug, such as neon-harbor.",
                    )
                ],
            )
        ]

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict | None) -> types.GetPromptResult:
        if name != PROMPT_NAME:
            raise McpError(
                types.ErrorData(
                    code=types.INVALID_PARAMS,
                    message="Unknown prompt. Use prompts/list to find a valid name.",
                )
            )
        try:
            params = ProjectArgs.model_validate(arguments or {})
        except ValidationError as exc:
            raise McpError(
                types.ErrorData(code=types.INVALID_PARAMS, message=validation_message(exc))
            ) from exc
        instructions = (
            f"Inspect project {params.project} with search_assets and follow each next_offset. "
            "Identify useful tag changes and cite the asset IDs. List the proposed add and remove "
            "tags before making edits. Ask for approval before changing tags, and never delete assets as part of organization."
        )
        return types.GetPromptResult(
            messages=[
                types.PromptMessage(
                    role="user", content=types.TextContent(type="text", text=instructions)
                )
            ]
        )

    return server
