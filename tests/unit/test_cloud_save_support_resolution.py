"""Cloud-save support resolution: store-authoritative first, catalog second.

Reported against 0.7.3: The Witcher 3 on GOG showed "Cloud saves: Unknown"
even though the reporter has cloud saves for it. Two independent causes:

1. The unifiDB bucket lookup stripped leading articles while the catalog
   buckets by the RAW title, so "The Witcher 3…" was searched for in
   ``w/wi.json`` while its record sat in ``t/th.json`` — covered by
   ``test_unifidb_bucket_articles.py``.
2. Even with the catalog reachable, it is a third-party aggregation
   (PCGamingWiki via Ludusavi) that records only POSITIVE support and has
   real gaps. Epic ships the answer itself, in metadata legendary already
   caches on disk for every owned game, so we prefer that.

The button (installed games) and the App-Details line (pre-install) share
this resolver precisely so they can never disagree on screen.
"""
from __future__ import annotations

import json

import pytest

from unifideck.services.cloud_save import support


@pytest.fixture
def _legendary(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Point the Epic metadata reader at a temp dir and write games into it."""
    meta_dir = tmp_path / "metadata"
    meta_dir.mkdir()
    monkeypatch.setattr(support, "_LEGENDARY_METADATA_DIR", str(meta_dir))

    def _write(app_name: str, payload: dict) -> None:
        (meta_dir / f"{app_name}.json").write_text(json.dumps(payload))
    return _write


def _epic_payload(cloud_folder: object) -> dict:
    attrs = {} if cloud_folder is None else {"CloudSaveFolder": cloud_folder}
    return {"app_name": "g1", "metadata": {"customAttributes": attrs}}


# ── Epic: the store's own answer ─────────────────────────────────────


def test_epic_cloud_folder_means_supported(_legendary) -> None:
    _legendary("g1", _epic_payload({"value": "{AppData}/Foo/Saves"}))
    assert support.epic_cloud_support("g1") is True


def test_epic_without_cloud_folder_means_unsupported(_legendary) -> None:
    _legendary("g1", _epic_payload(None))
    assert support.epic_cloud_support("g1") is False


def test_epic_empty_cloud_folder_is_unsupported(_legendary) -> None:
    """A declared-but-blank path syncs nothing."""
    _legendary("g1", _epic_payload({"value": ""}))
    assert support.epic_cloud_support("g1") is False


def test_epic_missing_metadata_is_unknown(_legendary) -> None:
    """No file = "Epic not synced yet", which is NOT "no cloud saves"."""
    assert support.epic_cloud_support("never-seen") is None


def test_epic_corrupt_metadata_is_unknown(tmp_path, monkeypatch) -> None:
    meta_dir = tmp_path / "metadata"
    meta_dir.mkdir()
    (meta_dir / "g1.json").write_text("{ not json")
    monkeypatch.setattr(support, "_LEGENDARY_METADATA_DIR", str(meta_dir))
    assert support.epic_cloud_support("g1") is None


# ── Catalog fallback ─────────────────────────────────────────────────


def test_catalog_reports_per_store() -> None:
    enriched = {"cloud": {"gog": True, "steam": True}}
    assert support.catalog_cloud_support(enriched, "gog") is True


def test_catalog_absent_store_is_unknown_not_false() -> None:
    """The map lists only storefronts WITH cloud support.

    Absence means "not recorded", so claiming False here would invent an
    answer — exactly the kind of wrong flag that prompted this work.
    """
    enriched = {"cloud": {"gog": True}}
    assert support.catalog_cloud_support(enriched, "epic") is None


def test_catalog_handles_missing_or_malformed_entries() -> None:
    assert support.catalog_cloud_support(None, "gog") is None
    assert support.catalog_cloud_support({}, "gog") is None
    assert support.catalog_cloud_support({"cloud": "yes"}, "gog") is None


# ── Precedence ───────────────────────────────────────────────────────


def test_epic_native_answer_beats_the_catalog(_legendary) -> None:
    """Epic says no; a stale catalog says yes. The store wins."""
    _legendary("g1", _epic_payload(None))
    assert support.resolve_cloud_support("epic", "g1", {"cloud": {"epic": True}}) is False


def test_catalog_used_when_epic_has_no_metadata(_legendary) -> None:
    assert (
        support.resolve_cloud_support("epic", "unknown", {"cloud": {"epic": True}})
        is True
    )


def test_gog_falls_through_to_the_catalog(_legendary) -> None:
    """GOG's authoritative signal costs two network calls, so it is not on
    this path — GOG resolves from the catalog only."""
    enriched = {"cloud": {"epic": True, "gog": True, "steam": True}}
    assert support.resolve_cloud_support("gog", "1495134320", enriched) is True


def test_unknown_when_nobody_knows() -> None:
    assert support.resolve_cloud_support("gog", "123", {}) is None
