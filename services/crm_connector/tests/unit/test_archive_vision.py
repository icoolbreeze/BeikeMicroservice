"""Unit tests for offline floor-plan label pipeline."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.application.archive_vision import (
    PROMPT_VERSION,
    MODEL_ID,
    Derived,
    ImageRef,
    CrmHints,
    ListingVision,
    derive_labels,
    extract_image_refs,
    image_set_fingerprint,
    normalize_image_url,
    rollup_listing,
    run_pipeline,
    rebuild_derived,
)
from app.infrastructure.archive_vision_store import ArchiveVisionStore


def test_normalize_image_url() -> None:
    assert normalize_image_url("https://img.ljcdn.com/abc.jpg!800x.jpg") == "https://img.ljcdn.com/abc.jpg"
    assert normalize_image_url("https://img.ljcdn.com/abc.jpg.1200x.jpg") == "https://img.ljcdn.com/abc.jpg"
    assert normalize_image_url("/abc.jpg!1200x.jpg") == "https://img.ljcdn.com/abc.jpg"
    assert normalize_image_url("abc.jpg.") == "https://img.ljcdn.com/abc.jpg"


def test_extract_image_refs_sources() -> None:
    rent_payload = {
        "listing_id": "r-1",
        "list": {"floor_plan_image_url": "https://img.ljcdn.com/fp1.jpg!800x.jpg"},
        "detail": {
            "prospect": {"floor_plan_url": "https://img.ljcdn.com/fp2.jpg"},
            "title_image_url": "https://img.ljcdn.com/title.jpg",
        },
    }
    refs = extract_image_refs(rent_payload, "rent")
    sources = {r.source for r in refs}
    assert "list_floor_plan" in sources
    assert "prospect_floor_plan" in sources

    trust_payload = {
        "listing_id": "r-2",
        "del_type": 5,
        "detail": {
            "head": {
                "house_type_images": [{"url": "https://img.ljcdn.com/trust1.jpg"}]
            }
        },
    }
    refs_trust = extract_image_refs(trust_payload, "rent")
    assert len(refs_trust) == 1
    assert refs_trust[0].source == "trusteeship_house_type"

    sale_payload = {
        "listing_id": "s-1",
        "list": {"floor_plan_image_url": "https://img.ljcdn.com/sale_fp.jpg"},
    }
    refs_sale = extract_image_refs(sale_payload, "sale")
    assert len(refs_sale) == 1
    assert refs_sale[0].source == "sale_list_floor_plan"


def test_derive_labels_negation_and_absent() -> None:
    d_neg = derive_labels("户型图", "readable", ["无阳台", "卧室"])
    assert d_neg.has_balcony == 0
    assert d_neg.is_residential == 1

    d_absent = derive_labels("户型图", "readable", [])
    assert d_absent.status == "miss"
    assert d_absent.has_balcony is None


def test_derive_labels_study_negation() -> None:
    d = derive_labels("户型图", "readable", ["无书房"])
    assert d.has_study == 0


def test_derive_labels_room_counts_none() -> None:
    d = derive_labels("户型图", "readable", ["客厅"])
    assert d.room_counts is None or d.room_counts.get("bedroom") is None


def test_derive_labels_ignored_tokens() -> None:
    d = derive_labels("户型图", "readable", ["幸福花园", "飘窗", "门厅", "2.3m²"])
    assert d.status == "miss"
    assert d.has_garden is None


def test_derive_labels_balcony_count() -> None:
    d = derive_labels("户型图", "readable", ["阳台A", "阳台B"])
    assert d.has_balcony == 1
    assert d.balcony_count == 2
    assert json.loads(d.balcony_kinds) == ["阳台A", "阳台B"]


def test_rollup_balcony_kinds() -> None:
    ref1 = ImageRef(biz="rent", listing_id="l-1", source="list_floor_plan", image_url="https://img.ljcdn.com/1.jpg")
    ref2 = ImageRef(biz="rent", listing_id="l-1", source="list_floor_plan", image_url="https://img.ljcdn.com/2.jpg")
    d1 = Derived(image_kind="户型图", readability="readable", labels=["阳台A"], status="ok", has_balcony=1, balcony_kinds=json.dumps(["阳台A"]))
    d2 = Derived(image_kind="户型图", readability="readable", labels=["阳台B"], status="ok", has_balcony=1, balcony_kinds=json.dumps(["阳台B"]))

    lv = rollup_listing([(ref1, d1), (ref2, d2)], crm=CrmHints())
    kinds = json.loads(lv.balcony_kinds)
    assert sorted(kinds) == ["阳台A", "阳台B"]


def test_rollup_rooms_conflict() -> None:
    ref = ImageRef(biz="rent", listing_id="l-1", source="list_floor_plan", image_url="https://img.ljcdn.com/1.jpg")
    d = Derived(image_kind="户型图", readability="readable", labels=["卧室A", "卧室B", "客厅"], status="ok", room_counts={"bedroom": 2, "living": 1, "bathroom": 1, "kitchen": 0})
    lv = rollup_listing([(ref, d)], crm=CrmHints(layout="3室1厅1卫"))
    assert lv.rooms_conflict == 1

    lv2 = rollup_listing([(ref, d)], crm=CrmHints(layout="2室1厅1卫"))
    assert lv2.rooms_conflict is None


def test_rollup_crm_use_conflict() -> None:
    ref = ImageRef(biz="rent", listing_id="l-1", source="list_floor_plan", image_url="https://img.ljcdn.com/1.jpg")
    d = Derived(image_kind="户型图", readability="readable", labels=["卧室"], status="ok", use_kind="住宅", is_residential=1)

    lv1 = rollup_listing([(ref, d)], crm=CrmHints(stat_function="107500000003"))
    assert lv1.use_conflict is None

    lv2 = rollup_listing([(ref, d)], crm=CrmHints(house_usage="商铺"))
    assert lv2.use_conflict == 1


def test_derive_labels_parking() -> None:
    d = derive_labels("户型图", "readable", ["车位"])
    assert d.is_parking == 1
    assert d.image_kind == "户型图"
    assert d.use_kind == "车位"


def test_derive_labels_photo() -> None:
    d = derive_labels("实拍", "readable", ["卧室", "客厅"])
    assert d.status == "miss"
    assert d.has_balcony is None


def test_derive_labels_shop_levels() -> None:
    d1 = derive_labels("户型图", "readable", ["商铺"])
    assert d1.is_shop == 1
    assert d1.is_ground_shop is None

    d2 = derive_labels("户型图", "readable", ["商铺", "首层", "临街"])
    assert d2.is_shop == 1
    assert d2.shop_level == "ground"
    assert d2.is_ground_shop == 1


def test_derive_labels_garden_positions() -> None:
    d_entry = derive_labels("户型图", "readable", ["入户花园"])
    assert d_entry.garden_position == "entry"

    d_roof = derive_labels("户型图", "readable", ["屋顶花园"])
    assert d_roof.garden_position == "roof"
    assert d_roof.shop_level is None

    d_plain = derive_labels("户型图", "readable", ["花园"])
    assert d_plain.has_garden == 1
    assert d_plain.garden_position is None

    d_roof_bare = derive_labels("户型图", "readable", ["屋面"])
    assert d_roof.garden_position == "roof"
    assert d_roof_bare.has_garden is None


def test_rollup_conflict() -> None:
    ref = ImageRef(biz="rent", listing_id="l-1", source="list_floor_plan", image_url="https://img.ljcdn.com/1.jpg")
    d1 = Derived(image_kind="户型图", readability="readable", labels=["商铺"], status="ok", is_shop=1, use_kind="商铺")
    d2 = Derived(image_kind="户型图", readability="readable", labels=["卧室"], status="ok", is_residential=1, use_kind="住宅")

    lv = rollup_listing([(ref, d1), (ref, d2)], crm=CrmHints())
    assert lv.floor_plan_status == "conflict"
    assert lv.use_kind is None


def test_rollup_absent() -> None:
    lv = rollup_listing([], crm=CrmHints())
    assert lv.floor_plan_status == "absent"
    assert lv.vision_status == "none"


def test_filter_listing_ids(tmp_path: Path) -> None:
    db_path = str(tmp_path / "vision.db")
    store = ArchiveVisionStore(db_path)
    store.upsert_listing_vision(
        "rent", "l-ready",
        {"floor_plan_status": "ok", "vision_status": "ready", "has_balcony": 1, "is_ground_shop": 0, "use_kind": "住宅"}
    )
    store.upsert_listing_vision(
        "rent", "l-stale",
        {"floor_plan_status": "ok", "vision_status": "stale", "has_balcony": 1, "is_ground_shop": 0, "use_kind": "住宅"}
    )
    store.upsert_listing_vision(
        "rent", "l-neg",
        {"floor_plan_status": "ok", "vision_status": "ready", "has_balcony": 0, "is_ground_shop": 0, "use_kind": "住宅"}
    )

    ids_ready_balcony = store.filter_listing_ids("rent", has_balcony=1)
    assert ids_ready_balcony == ["l-ready"]

    ids_neg = store.filter_listing_ids("rent", has_balcony=0)
    assert ids_neg == ["l-neg"]


def test_pipeline_backfill_and_download_error(tmp_path: Path) -> None:
    rental_db = tmp_path / "rental.db"
    vision_db = tmp_path / "vision.db"

    conn = sqlite3.connect(rental_db)
    conn.execute(
        "CREATE TABLE listing_current (listing_id TEXT PRIMARY KEY, payload_json TEXT, content_hash TEXT)"
    )
    payload = {
        "listing_id": "r-fix-1",
        "list": {"floor_plan_image_url": "https://img.ljcdn.com/fix.jpg"},
    }
    conn.execute(
        "INSERT INTO listing_current VALUES (?, ?, ?)",
        ("r-fix-1", json.dumps(payload), "hash-1"),
    )
    conn.commit()
    conn.close()

    model_calls = 0

    def mock_downloader(url: str) -> bytes | None:
        if "bad" in url:
            return b"not-jpeg"
        return b"\xff\xd8fakejpegbytes"

    def mock_model(img_bytes: bytes):
        nonlocal model_calls
        model_calls += 1
        return "户型图", "readable", ["卧室", "厨房"], {"image_kind": "户型图", "readability": "readable", "labels": ["卧室", "厨房"]}

    run_pipeline(
        rental_db=str(rental_db),
        sale_db=None,
        vision_db=str(vision_db),
        downloader=mock_downloader,
        model_caller=mock_model,
    )
    assert model_calls == 1

    run_pipeline(
        rental_db=str(rental_db),
        sale_db=None,
        vision_db=str(vision_db),
        downloader=mock_downloader,
        model_caller=mock_model,
    )
    assert model_calls == 1  # zero additional calls

    conn = sqlite3.connect(rental_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT payload_json, content_hash FROM listing_current WHERE listing_id = 'r-fix-1'").fetchone()
    conn.close()
    assert row["content_hash"] == "hash-1"
    assert json.loads(row["payload_json"])["listing_id"] == "r-fix-1"


def test_non_jpeg_download_error(tmp_path: Path) -> None:
    rental_db = tmp_path / "rental.db"
    vision_db = tmp_path / "vision.db"

    conn = sqlite3.connect(rental_db)
    conn.execute(
        "CREATE TABLE listing_current (listing_id TEXT PRIMARY KEY, payload_json TEXT, content_hash TEXT)"
    )
    payload = {
        "listing_id": "r-err-1",
        "list": {"floor_plan_image_url": "https://img.ljcdn.com/bad.jpg"},
    }
    conn.execute(
        "INSERT INTO listing_current VALUES (?, ?, ?)",
        ("r-err-1", json.dumps(payload), "hash-err"),
    )
    conn.commit()
    conn.close()

    def bad_downloader(url: str) -> bytes:
        return b"HTML 403 Forbidden"

    run_pipeline(
        rental_db=str(rental_db),
        sale_db=None,
        vision_db=str(vision_db),
        downloader=bad_downloader,
        model_caller=lambda b: None,
    )

    store = ArchiveVisionStore(str(vision_db))
    obs = store.get_observation("https://img.ljcdn.com/bad.jpg")
    assert obs["status"] == "error"

    lv = store.get_listing_vision("rent", "r-err-1")
    assert lv["floor_plan_status"] == "error"


def _seed_rental(path: Path, listing_id: str, payload: dict) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE listing_current (listing_id TEXT PRIMARY KEY, payload_json TEXT, content_hash TEXT)"
    )
    conn.execute(
        "INSERT INTO listing_current VALUES (?, ?, ?)",
        (listing_id, json.dumps(payload), "hash-1"),
    )
    conn.commit()
    conn.close()


def test_no_floor_plan_null_hash_not_stale(tmp_path: Path) -> None:
    rental_db = tmp_path / "rental.db"
    vision_db = tmp_path / "vision.db"
    _seed_rental(rental_db, "r-empty", {"listing_id": "r-empty", "list": {}})

    store = ArchiveVisionStore(str(vision_db))
    store.upsert_listing_vision(
        "rent",
        "r-empty",
        {"floor_plan_status": "absent", "vision_status": "none", "image_set_sha256": None},
    )

    calls = 0

    def boom(_url: str) -> bytes:
        nonlocal calls
        calls += 1
        return b"\xff\xd8nope"

    def model(_img: bytes):
        nonlocal calls
        calls += 1
        return None

    for _ in range(2):
        run_pipeline(
            rental_db=str(rental_db),
            sale_db=None,
            vision_db=str(vision_db),
            downloader=boom,
            model_caller=model,
        )

    assert calls == 0
    lv = store.get_listing_vision("rent", "r-empty")
    assert lv["vision_status"] == "none"
    assert lv["floor_plan_status"] == "absent"
    assert lv["image_set_sha256"] == image_set_fingerprint([])


def test_relabeled_url_set_clears_stale(tmp_path: Path) -> None:
    rental_db = tmp_path / "rental.db"
    vision_db = tmp_path / "vision.db"
    url = "https://img.ljcdn.com/already.jpg"
    _seed_rental(rental_db, "r-ready", {"listing_id": "r-ready", "list": {"floor_plan_image_url": url}})

    derived = derive_labels("户型图", "readable", ["卧室", "厨房"])
    store = ArchiveVisionStore(str(vision_db))
    store.upsert_observation(
        url,
        image_kind="户型图",
        readability="readable",
        status="ok",
        labels_json=json.dumps(["卧室", "厨房"], ensure_ascii=False),
        derived_json=json.dumps(asdict(derived), ensure_ascii=False),
        prompt_version=PROMPT_VERSION,
        model_id=MODEL_ID,
    )
    store.upsert_listing_vision(
        "rent",
        "r-ready",
        {"floor_plan_status": "ok", "vision_status": "stale", "image_set_sha256": "old"},
    )

    calls = 0

    def model(_img: bytes):
        nonlocal calls
        calls += 1
        return None

    run_pipeline(
        rental_db=str(rental_db),
        sale_db=None,
        vision_db=str(vision_db),
        downloader=lambda _url: b"\xff\xd8unused",
        model_caller=model,
    )

    assert calls == 0
    lv = store.get_listing_vision("rent", "r-ready")
    assert lv["vision_status"] == "ready"
    assert lv["image_set_sha256"] == image_set_fingerprint([url])


def test_limit_skips_unlabeled_image_marks_stale(tmp_path: Path) -> None:
    rental_db = tmp_path / "rental.db"
    vision_db = tmp_path / "vision.db"
    url = "https://img.ljcdn.com/new.jpg"
    _seed_rental(rental_db, "r-new", {"listing_id": "r-new", "list": {"floor_plan_image_url": url}})

    calls = 0

    def model(_img: bytes):
        nonlocal calls
        calls += 1
        return None

    run_pipeline(
        rental_db=str(rental_db),
        sale_db=None,
        vision_db=str(vision_db),
        limit=0,
        downloader=lambda _url: b"\xff\xd8unused",
        model_caller=model,
    )

    assert calls == 0
    store = ArchiveVisionStore(str(vision_db))
    lv = store.get_listing_vision("rent", "r-new")
    assert lv["vision_status"] == "stale"
    assert lv["image_set_sha256"] == image_set_fingerprint([url])


def test_legacy_database_migration_and_rebuild(tmp_path: Path) -> None:
    old_db = tmp_path / "archive_vision.sqlite3"
    rental_db = tmp_path / "rental.db"

    # 1. Create old database with legacy schema (no schema_version / rules_version)
    conn = sqlite3.connect(old_db)
    conn.execute("""
    CREATE TABLE image_observation (
        image_url TEXT PRIMARY KEY,
        image_sha256 TEXT,
        image_kind TEXT,
        readability TEXT,
        status TEXT,
        labels_json TEXT,
        derived_json TEXT,
        model_id TEXT,
        prompt_version TEXT,
        labeled_at TEXT,
        error TEXT
    );
    """)
    conn.execute("""
    CREATE TABLE image_link (
        biz TEXT NOT NULL,
        listing_id TEXT NOT NULL,
        image_url TEXT NOT NULL,
        source TEXT NOT NULL,
        PRIMARY KEY (biz, listing_id, image_url)
    );
    """)
    conn.execute("""
    CREATE TABLE listing_vision (
        biz TEXT NOT NULL,
        listing_id TEXT NOT NULL,
        floor_plan_status TEXT NOT NULL,
        vision_status TEXT NOT NULL,
        has_balcony INTEGER,
        has_garden INTEGER,
        has_terrace INTEGER,
        has_study INTEGER,
        is_shop INTEGER,
        is_office INTEGER,
        is_residential INTEGER,
        is_parking INTEGER,
        is_ground_shop INTEGER,
        is_photo_not_plan INTEGER,
        rooms_conflict INTEGER,
        use_conflict INTEGER,
        use_kind TEXT,
        balcony_count INTEGER,
        balcony_kinds TEXT,
        garden_kind TEXT,
        garden_position TEXT,
        shop_level TEXT,
        source_url TEXT,
        image_set_sha256 TEXT,
        model_id TEXT,
        prompt_version TEXT,
        labeled_at TEXT,
        PRIMARY KEY (biz, listing_id)
    );
    """)

    url = "https://img.ljcdn.com/legacy_balcony.jpg"
    derived = derive_labels("户型图", "readable", ["阳台", "卧室"])
    labeled_at = "2026-10-01T12:00:00Z"
    sha = "legacy-sha-abc"

    conn.execute(
        """INSERT INTO image_observation (image_url, image_sha256, image_kind, readability, status, labels_json, derived_json, model_id, prompt_version, labeled_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (url, sha, "户型图", "readable", "ok", json.dumps(["阳台", "卧室"]), json.dumps(asdict(derived)), MODEL_ID, PROMPT_VERSION, labeled_at)
    )
    conn.execute(
        "INSERT INTO image_link (biz, listing_id, image_url, source) VALUES (?, ?, ?, ?)",
        ("rent", "legacy-r1", url, "list_floor_plan")
    )
    conn.execute(
        """INSERT INTO listing_vision (biz, listing_id, floor_plan_status, vision_status, has_balcony, model_id, prompt_version, labeled_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("rent", "legacy-r1", "ok", "stale", None, MODEL_ID, PROMPT_VERSION, labeled_at)
    )
    conn.commit()
    conn.close()

    _seed_rental(rental_db, "legacy-r1", {"listing_id": "legacy-r1", "list": {"floor_plan_image_url": url}})

    # 2. First connection / rebuild on legacy DB successfully migrates and reconstructs derived data
    rebuild_derived(str(rental_db), None, str(old_db))

    store = ArchiveVisionStore(str(old_db))
    obs = store.get_observation(url)
    assert obs["image_sha256"] == sha
    assert json.loads(obs["labels_json"]) == ["阳台", "卧室"]
    assert obs["model_id"] == MODEL_ID
    assert obs["labeled_at"] == labeled_at

    lv = store.get_listing_vision("rent", "legacy-r1")
    assert lv["has_balcony"] == 1
    assert lv["vision_status"] == "ready"

    # 3. Repeat execution successfully
    rebuild_derived(str(rental_db), None, str(old_db))
    store2 = ArchiveVisionStore(str(old_db))
    lv2 = store2.get_listing_vision("rent", "legacy-r1")
    assert lv2["has_balcony"] == 1
