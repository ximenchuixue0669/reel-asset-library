"""Human-approved soft deletion and cancellable thumbnail batches."""

import hashlib
import secrets
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from hmac import compare_digest
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

import anyio
from PIL import Image

from reel_library.catalog import Catalog, CatalogError, Search
from reel_library.seed import seed_demo

APPROVAL_TTL = 180
MAX_PENDING_APPROVALS = 24
MAX_BATCHES = 8
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_PIXELS = 8_000_000
Progress = Callable[[int, int, str], Awaitable[None]]


@dataclass(slots=True)
class Approval:
    id: str
    asset_id: str
    expires_at: float
    code_digest: bytes | None = None

    def public(self) -> dict:
        return {
            "approval_id": self.id,
            "asset_id": self.asset_id,
            "ready": self.code_digest is not None,
            "expires_in_seconds": max(0, int(self.expires_at - monotonic())),
        }


class Operations:
    def __init__(self, catalog: Catalog, changed: Callable[[], None], *, thumbnail_seconds=15.0):
        self.catalog = catalog
        self.changed = changed
        self.thumbnail_seconds = thumbnail_seconds
        self.thumbnails = catalog.data_dir / "thumbnails"
        self.thumbnails.mkdir(exist_ok=True)
        self._approvals: dict[str, Approval] = {}
        self.active_jobs = 0
        self.fail_next = False
        self.slow_tools = False

    def _prune_approvals(self) -> None:
        now = monotonic()
        for asset_id, approval in list(self._approvals.items()):
            if approval.expires_at <= now:
                del self._approvals[asset_id]

    def pending_approvals(self) -> list[dict]:
        self._prune_approvals()
        return [approval.public() for approval in self._approvals.values()]

    def delete_asset(self, asset_id: str, confirmation_code: str | None) -> dict:
        self._prune_approvals()
        approval = self._approvals.get(asset_id)
        if confirmation_code is not None:
            if (
                approval is None
                or approval.code_digest is None
                or not compare_digest(approval.code_digest, digest(confirmation_code))
            ):
                raise CatalogError(
                    "Invalid or expired confirmation code for this asset. Request deletion and approve it on the dashboard before retrying."
                )
            result = self.catalog.soft_delete(asset_id)
            del self._approvals[asset_id]
            self.changed()
            return result
        asset = self.catalog.get(asset_id)
        if approval is None:
            if len(self._approvals) >= MAX_PENDING_APPROVALS:
                raise CatalogError("Too many pending approvals. Wait for one to expire and retry.")
            approval = Approval(secrets.token_hex(12), asset_id, monotonic() + APPROVAL_TTL)
            self._approvals[asset_id] = approval
            self.changed()
        return {
            "state": "pending_human_confirmation",
            **approval.public(),
            "asset": asset,
            "message": "No data was changed. Have a human review the pending deletion on the dashboard, then pass their confirmation code for this asset ID.",
        }

    def approve_deletion(self, approval_id: str) -> dict:
        self._prune_approvals()
        approval = next(
            (entry for entry in self._approvals.values() if entry.id == approval_id), None
        )
        if approval is None:
            raise CatalogError("Approval has expired. Request deletion again in the MCP client.")
        self.catalog.get(approval.asset_id)
        code = secrets.token_urlsafe(24)
        approval.code_digest = digest(code)
        self.changed()
        return {**approval.public(), "confirmation_code": code}

    def set_debug(self, name: str, enabled: bool) -> dict:
        if name == "fail_next":
            self.fail_next = enabled
        elif name == "slow_tools":
            self.slow_tools = enabled
        else:
            raise CatalogError("Unknown debug control.")
        self.changed()
        return self.debug()

    def debug(self) -> dict:
        return {
            "fail_next": self.fail_next,
            "slow_tools": self.slow_tools,
            "active_jobs": self.active_jobs,
        }

    async def before_tool(self) -> None:
        if self.fail_next:
            self.fail_next = False
            self.changed()
            raise CatalogError(
                "Injected demo failure. The switch is cleared. Retry your previous tool call."
            )
        if self.slow_tools:
            await anyio.sleep(2)

    def reset(self) -> dict:
        if self.active_jobs:
            raise CatalogError(
                "A thumbnail batch is active. Cancel it in the MCP client or wait for it to finish, then reset."
            )
        seed_demo(self.catalog, replace=True)
        for path in self._batch_dirs():
            shutil.rmtree(path)
        self._approvals.clear()
        self.fail_next = self.slow_tools = False
        self.changed()
        return self.catalog.statistics()

    def _batch_dirs(self) -> list[Path]:
        return [
            path
            for path in self.thumbnails.iterdir()
            if path.is_dir() and not path.is_symlink() and not path.name.startswith(".")
        ]

    async def generate_thumbnails(self, project: str | None, progress: Progress) -> dict:
        if self.active_jobs >= 2:
            raise CatalogError(
                "Two thumbnail batches are already active. Wait for one to finish and retry."
            )
        assets = self.catalog.search(Search(type="image", project=project, limit=12))["assets"]
        if not assets:
            raise CatalogError(
                "No active images in that project. Use search_assets to find an image project."
            )
        job_id = secrets.token_hex(12)
        count = len(assets)
        self.active_jobs += 1
        self.changed()
        try:
            with TemporaryDirectory(prefix=".pending-", dir=self.thumbnails) as temporary:
                staging = Path(temporary)
                await progress(0, count, "Starting thumbnail batch")
                for index, asset in enumerate(assets, 1):
                    await anyio.sleep(self.thumbnail_seconds / count)
                    source = self.catalog.source_path(asset["path"])
                    destination = staging / f"{asset['id']}.jpg"
                    write_thumbnail(source, destination)
                    await progress(index, count, f"Prepared {asset['id']}")
                # A cancellation cannot interrupt the commit of a completed batch.
                staging.replace(self.thumbnails / job_id)
                batches = sorted(self._batch_dirs(), key=lambda path: path.stat().st_mtime_ns)
                for old_batch in batches[:-MAX_BATCHES]:
                    shutil.rmtree(old_batch)
        finally:
            self.active_jobs -= 1
            self.changed()
        return {
            "job_id": job_id,
            "generated": count,
            "thumbnail_paths": [f"thumbnails/{job_id}/{asset['id']}.jpg" for asset in assets],
        }


def digest(code: str) -> bytes:
    return hashlib.sha256(code.encode("ascii")).digest()


def write_thumbnail(source: Path, destination: Path) -> None:
    if source.stat().st_size > MAX_SOURCE_BYTES:
        raise CatalogError("A source image exceeds the demo file size limit.")
    with Image.open(source, formats=["PNG"]) as image:
        if image.width * image.height > MAX_SOURCE_PIXELS:
            raise CatalogError("A source image exceeds the demo pixel limit.")
        image.thumbnail((240, 135))
        with image.convert("RGB") as rgb:
            rgb.save(destination, format="JPEG", quality=82)
