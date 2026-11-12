"""Acceptance and behavior tests for archive vision worker and pipeline."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
import pytest

from app.application.archive_vision import (
    run_pipeline,
    ArchiveVisionRunner,
    ArchiveVisionLock,
    PROMPT_VERSION,
    MODEL_ID,
    SCHEMA_VERSION,
)
from app.infrastructure.archive_vision_store import ArchiveVisionStore


def _create_source_db(db_path: Path, listing_id: str, floor_plan_url: str) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS listing_current (
                listing_id TEXT PRIMARY KEY,
                is_active INTEGER,
                payload_json TEXT
            )"""
        )
        payload = {
            "listing_id": listing_id,
            "list": {
                "floor_plan_image_url": floor_plan_url,
                "layout": "2室1厅1卫",
            },
        }
        conn.execute(
            "INSERT OR REPLACE INTO listing_current (listing_id, is_active, payload_json) VALUES (?, 1, ?)",
            (listing_id, json.dumps(payload))
        )
        conn.commit()


def test_existing_ok_miss_not_requested(tmp_path: Path) -> None:
    rental_db = tmp_path / "rental.sqlite3"
    vision_db = tmp_path / "vision.sqlite3"
    _create_source_db(rental_db, "L-1", "https://img.ljcdn.com/fp1.jpg")

    download_calls = 0
    model_calls = 0

    def mock_downloader(url: str) -> bytes:
        nonlocal download_calls
        download_calls += 1
        return b"\xff\xd8\xff\xe0\x00\x10JFIF..."  # valid jpeg start

    def mock_model(img_bytes: bytes):
        nonlocal model_calls
        model_calls += 1
        return ("户型图", "readable", ["卧室", "客厅"], {})

    # First run
    metrics1 = run_pipeline(str(rental_db), None, str(vision_db), downloader=mock_downloader, model_caller=mock_model)
    assert metrics1["success"] == 1
    assert download_calls == 1
    assert model_calls == 1

    # Second run: cache hit should not download or call model
    metrics2 = run_pipeline(str(rental_db), None, str(vision_db), downloader=mock_downloader, model_caller=mock_model)
    assert metrics2["cache_hit"] == 1
    assert download_calls == 1  # unchanged
    assert model_calls == 1     # unchanged


def test_shared_sha_not_re_inferring(tmp_path: Path) -> None:
    rental_db = tmp_path / "rental.sqlite3"
    vision_db = tmp_path / "vision.sqlite3"
    _create_source_db(rental_db, "L-1", "https://img.ljcdn.com/fp1.jpg")

    # Add another listing sharing the exact same image URL / binary
    with sqlite3.connect(str(rental_db)) as conn:
        payload = {
            "listing_id": "L-2",
            "list": {"floor_plan_image_url": "https://img.ljcdn.com/fp1.jpg"},
        }
        conn.execute(
            "INSERT OR REPLACE INTO listing_current (listing_id, is_active, payload_json) VALUES (?, 1, ?)",
            ("L-2", json.dumps(payload))
        )
        conn.commit()

    model_calls = 0

    def mock_downloader(url: str) -> bytes:
        return b"\xff\xd8\xff\xe0\x00\x10JFIF..."

    def mock_model(img_bytes: bytes):
        nonlocal model_calls
        model_calls += 1
        return ("户型图", "readable", ["卧室"], {})

    run_pipeline(str(rental_db), None, str(vision_db), downloader=mock_downloader, model_caller=mock_model)
    # L-1 downloads and calls model. L-2 downloads same bytes, finds existing observation by SHA256, so model is called only once!
    assert model_calls == 1


def test_batch_budget_quota_and_failure(tmp_path: Path) -> None:
    rental_db = tmp_path / "rental.sqlite3"
    vision_db = tmp_path / "vision.sqlite3"
    
    # Create 3 listings
    rental_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(rental_db)) as conn:
        conn.execute("CREATE TABLE listing_current (listing_id TEXT PRIMARY KEY, is_active INTEGER, payload_json TEXT)")
        for i in (1, 2, 3):
            p = {"listing_id": f"L-{i}", "list": {"floor_plan_image_url": f"https://img.ljcdn.com/fp{i}.jpg"}}
            conn.execute("INSERT INTO listing_current VALUES (?, 1, ?)", (f"L-{i}", json.dumps(p)))
        conn.commit()

    download_attempts = 0

    def mock_downloader(url: str) -> bytes | None:
        nonlocal download_attempts
        download_attempts += 1
        if "fp1" in url:
            return None  # download failure (403/not jpeg)
        return b"\xff\xd8\xff\xe0\x00\x10JFIF..."

    def mock_model(img_bytes: bytes):
        return ("户型图", "readable", ["卧室"], {})

    # Run with limit=2 (budget=2)
    metrics = run_pipeline(str(rental_db), None, str(vision_db), limit=2, downloader=mock_downloader, model_caller=mock_model)
    # Budget=2 means at most 2 real image fetch/attempt tasks are consumed.
    # L-1 fails download (consumes budget 1). L-2 succeeds model call (consumes budget 2). L-3 is pending due to budget.
    assert metrics["error"] == 1
    assert metrics["success"] == 1
    assert metrics["pending"] == 1


def test_error_cooling_down(tmp_path: Path) -> None:
    vision_db = tmp_path / "vision.sqlite3"
    store = ArchiveVisionStore(str(vision_db))

    # Insert a recent error observation (< 15 mins)
    store.upsert_observation(
        "https://img.ljcdn.com/err.jpg",
        status="error",
        error="download_failed",
        prompt_version=PROMPT_VERSION,
        model_id=MODEL_ID,
        labeled_at=datetime.now(UTC).isoformat(),
        schema_version=SCHEMA_VERSION,
    )

    rental_db = tmp_path / "rental.sqlite3"
    _create_source_db(rental_db, "L-err", "https://img.ljcdn.com/err.jpg")

    download_calls = 0

    def mock_downloader(url: str) -> bytes:
        nonlocal download_calls
        download_calls += 1
        return b"\xff\xd8..."

    # Because error is recent (< 15 min cooling down), pipeline should skip retrying it
    metrics = run_pipeline(
        str(rental_db), None, str(vision_db), limit=1,
        downloader=mock_downloader,
        model_caller=lambda b: ("户型图", "readable", ["卧室"], {})
    )
    assert download_calls == 0


def test_url_change_keeps_stale(tmp_path: Path) -> None:
    rental_db = tmp_path / "rental.sqlite3"
    vision_db = tmp_path / "vision.sqlite3"
    _create_source_db(rental_db, "L-1", "https://img.ljcdn.com/old.jpg")

    def mock_downloader(url: str) -> bytes:
        return b"\xff\xd8..."

    def mock_model(img_bytes: bytes):
        return ("户型图", "readable", ["卧室"], {})

    run_pipeline(str(rental_db), None, str(vision_db), downloader=mock_downloader, model_caller=mock_model)
    lv_before = ArchiveVisionStore(str(vision_db)).get_listing_vision("rent", "L-1")
    assert lv_before["vision_status"] == "ready"

    # Change image URL in source db, run with limit=0 (budget exhausted / new image unsettled)
    _create_source_db(rental_db, "L-1", "https://img.ljcdn.com/new.jpg")

    run_pipeline(str(rental_db), None, str(vision_db), limit=0, downloader=mock_downloader, model_caller=mock_model)
    lv_after = ArchiveVisionStore(str(vision_db)).get_listing_vision("rent", "L-1")
    assert lv_after["vision_status"] == "stale"


def test_double_process_lock(tmp_path: Path) -> None:
    vision_db = tmp_path / "vision.sqlite3"
    lock_file = vision_db.with_suffix(".lock")

    lock1 = ArchiveVisionLock(lock_file)
    assert lock1.acquire() is True

    lock2 = ArchiveVisionLock(lock_file)
    assert lock2.acquire() is False

    lock1.release()
    assert lock2.acquire() is True
    lock2.release()


def test_watch_runner_lifecycle(tmp_path):
    source = tmp_path / "r.db"
    _create_source_db(source, "L1", "https://img.ljcdn.com/plan.jpg")
    runner = ArchiveVisionRunner(str(source), None, str(tmp_path / "v.db"), poll_interval=1.,
                                downloader=lambda _: b"\xff\xd8fake",
                                model_caller=lambda _: ("户型图", "readable", ["阳台"], {}))
    runner.start()
    assert runner._thread is not None and runner._thread.is_alive()
    runner.stop()
    assert not runner._thread.is_alive()


def test_old_schema_migration(tmp_path: Path) -> None:
    vision_db = tmp_path / "vision.sqlite3"
    # Create old schema table without schema_version or rules_version
    with sqlite3.connect(str(vision_db)) as conn:
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
            )
        """)
        conn.commit()

    store = ArchiveVisionStore(str(vision_db))
    # Connecting should trigger migration / column add without error
    obs = store.get_observation("https://example.com/test.jpg")
    assert obs is None
