"""Deterministic synthetic samples. No input files or network access needed."""

import math
import shutil
import struct
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

from reel_library.catalog import Catalog, utc_text

PROJECTS = {
    "neon-harbor": ("#152640", "#72dbea"),
    "paper-moons": ("#42354c", "#f4c1c0"),
    "last-train": ("#294345", "#e9ce88"),
}
ASSET_TYPES = [
    "image",
    "image",
    "video",
    "audio",
    "image",
    "video",
    "audio",
    "image",
    "video",
    "audio",
]
TAGS = [
    "opening",
    "night",
    "dialogue",
    "ambience",
    "street",
    "action",
    "music",
    "portrait",
    "closing",
    "foley",
]
EXTENSIONS = {"image": "png", "video": "mp4", "audio": "wav"}
VIDEO_FIXTURE = Path(__file__).with_name("fixtures") / "slate.mp4"


def seed_demo(catalog: Catalog, *, replace=False) -> None:
    rows = []
    start = datetime(2026, 1, 5, 12, tzinfo=UTC)
    for project_index, (project, palette) in enumerate(PROJECTS.items()):
        for index, (asset_type, tag) in enumerate(zip(ASSET_TYPES, TAGS, strict=True)):
            number = project_index * len(ASSET_TYPES) + index + 1
            asset_id = f"asset-{number:03}"
            relative_path = f"{project}/{asset_type}/{asset_id}.{EXTENSIONS[asset_type]}"
            path = catalog.source_path(relative_path, must_exist=False)
            if not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                write_sample(path, asset_type, asset_id, palette, number)
            rows.append(
                {
                    "id": asset_id,
                    "type": asset_type,
                    "project": project,
                    "path": relative_path,
                    "created_at": utc_text(start + timedelta(days=number)),
                    "tags": sorted({tag, "approved" if index % 2 == 0 else "review"}),
                }
            )
    catalog.seed(rows, replace=replace)


def write_sample(path: Path, asset_type: str, asset_id: str, palette: tuple[str, str], number: int):
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        if asset_type == "video":
            shutil.copyfile(VIDEO_FIXTURE, temporary)
        elif asset_type == "audio":
            samples = (
                int(1200 * math.sin(2 * math.pi * (180 + 7 * number) * index / 8000))
                for index in range(3200)
            )
            with wave.open(str(temporary), "wb") as audio:
                audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                audio.writeframes(struct.pack("<3200h", *samples))
        else:
            background, accent = palette
            with Image.new("RGB", (640, 360), background) as image:
                draw = ImageDraw.Draw(image)
                drawing_offset = number * 7 % 160
                draw.ellipse((90 + drawing_offset, 44, 330 + drawing_offset, 284), fill=accent)
                draw.rectangle((0, 258, 640, 360), fill=background)
                draw.text(
                    (38, 301), f"REEL LIBRARY / {asset_id.upper()}", fill=accent, font_size=24
                )
                image.save(temporary, format="PNG")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
