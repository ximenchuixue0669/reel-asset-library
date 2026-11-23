import wave
from datetime import UTC, datetime

import pytest
from PIL import Image

from reel_library.catalog import Catalog, CatalogError, Search, check_relative_path
from reel_library.seed import seed_demo


@pytest.fixture
def catalog(tmp_path):
    catalog = Catalog(tmp_path)
    seed_demo(catalog)
    return catalog


def test_seed_writes_real_media_and_30_records(catalog):
    assert catalog.statistics()["by_type"] == {"image": 12, "video": 9, "audio": 9}
    for asset_id in ("asset-001", "asset-003", "asset-004"):
        path = catalog.source_path(catalog.get(asset_id)["path"])
        if path.suffix == ".png":
            with Image.open(path) as image:
                image.verify()
        elif path.suffix == ".wav":
            with wave.open(str(path), "rb") as audio:
                assert audio.getnframes() > 0
        else:
            assert b"ftyp" in path.read_bytes()[:12]


def test_search_combines_filters_and_paginates_stably(catalog):
    filters = dict(
        project="NEON-HARBOR",
        tag="approved",
        type="image",
        path_prefix="neon-harbor/image",
        created_from=datetime(2026, 1, 6, 12, tzinfo=UTC),
        created_before=datetime(2026, 1, 14, 12, tzinfo=UTC),
    )
    first = catalog.search(Search(**filters, limit=1))
    second = catalog.search(Search(**filters, limit=1, offset=first["next_offset"]))
    assert [a["id"] for a in first["assets"] + second["assets"]] == ["asset-005", "asset-001"]
    assert second["next_offset"] is None


def test_update_delete_and_reset(catalog):
    assert catalog.update_tags("asset-001", add=["hero"], remove=["opening"])["tags"] == [
        "approved",
        "hero",
    ]
    assert catalog.soft_delete("asset-001")["state"] == "soft_deleted"
    assert catalog.statistics()["total"] == 29
    seed_demo(catalog)
    with pytest.raises(CatalogError, match="No active asset"):
        catalog.get("asset-001")
    assert catalog.source_path("neon-harbor/image/asset-001.png").is_file()
    seed_demo(catalog, replace=True)
    assert catalog.get("asset-001")["tags"] == ["approved", "opening"]


@pytest.mark.parametrize(
    "path", ["../../etc/passwd", "/etc", "x/../etc", "x\\outside", "%2e%2e/etc", "x//y"]
)
def test_bad_library_paths_are_rejected(path):
    with pytest.raises(CatalogError, match="library-relative"):
        check_relative_path(path)


def test_symlink_cannot_escape_media_root(catalog, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (catalog.media_dir / "outside.txt").symlink_to(outside)
    with pytest.raises(CatalogError, match="inside the library"):
        catalog.source_path("outside.txt")
