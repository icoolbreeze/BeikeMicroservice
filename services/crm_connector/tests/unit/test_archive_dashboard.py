"""Dashboard boundaries: freshness, unavailable sources, liveness and safe drill-down."""

from __future__ import annotations

import importlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.application import archive_dashboard as dashboard
from app.application import archive_vision as vision
from app.application.archive_telemetry import WorkerTelemetry, new_model_stats, record_usage
from app.infrastructure.archive_vision_store import ArchiveVisionStore
from app.infrastructure.settings import Settings
from app.main import create_app


def source(path, biz="rental", entries=None):
    module = importlib.import_module("app.infrastructure." + biz + "_archive_store")
    now = datetime.now(UTC)
    entries = entries or [
        ("fresh", "complete", 1, 1),
        ("old", "complete", 20, 1),
        ("failure", "failed", 1, 1),
        ("pending", "none", None, 1),
        ("skipped", "skipped_unsupported", 1, 1),
        ("inactive", "complete", 1, 0),
    ]
    with sqlite3.connect(path) as conn:
        conn.executescript(module._SCHEMA)
        for listing_id, status, days, active in entries:
            captured = (now - timedelta(days=days)).isoformat() if days is not None else None
            payload = {
                "list": {"floor_plan_image_url": "https://img.ljcdn.com/" + listing_id + ".jpg"}
            }
            conn.execute(
                "INSERT INTO listing_current(listing_id,district_id,payload_json,content_hash,"
                "detail_status,detail_captured_at,first_seen_at,last_seen_at,is_active) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    listing_id,
                    "510104",
                    json.dumps(payload),
                    "hash-" + listing_id,
                    status,
                    captured,
                    now.isoformat(),
                    now.isoformat(),
                    active,
                ),
            )


@pytest.mark.parametrize("biz", ["rental", "sale"])
def test_active_freshness_is_disjoint_from_history(tmp_path, biz):
    path = tmp_path / (biz + ".db")
    source(path, biz)
    result = dashboard.archive_snapshot(str(path), 14)
    t = result["totals"]
    assert t["total"] == 6 and t["active"] == 5 and t["inactive"] == 1
    assert t["complete"] == 3
    assert [
        t[k]
        for k in (
            "active_fresh",
            "active_stale",
            "active_failed",
            "active_pending",
            "active_skipped",
        )
    ] == [1] * 5
    assert (
        sum(
            t[k]
            for k in (
                "active_fresh",
                "active_stale",
                "active_failed",
                "active_pending",
                "active_skipped",
                "active_unknown",
            )
        )
        == t["active"]
    )


def test_missing_databases_are_unknown_and_never_created(tmp_path):
    src, target = tmp_path / "missing.db", tmp_path / "missing-vision.db"
    assert dashboard.archive_snapshot(str(src), 14)["totals"] is None
    assert (
        dashboard.issues(str(src), status="failed", district_id=None, page=1, page_size=20)["total"]
        is None
    )
    v = dashboard.vision_status(str(target), {"rental": str(src)})
    assert not v["available"] and v["pending_count"] is None and v["worker_state"] == "unknown"
    assert not list(tmp_path.iterdir())


def test_read_connection_rejects_writes(tmp_path):
    path = tmp_path / "source.db"
    source(path)
    with dashboard.readonly(str(path)) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM listing_current")


def test_future_or_missing_capture_time_is_never_fresh(tmp_path):
    path = tmp_path / "source.db"
    source(path, entries=[("future", "complete", -10, 1), ("missing", "complete", None, 1)])
    counts = dashboard.archive_snapshot(str(path), 14)["totals"]
    assert counts["active_fresh"] == 0 and counts["active_stale"] == 2


def test_issue_pagination_and_sensitive_error_text(tmp_path):
    path = tmp_path / "source.db"
    source(path, entries=[("f" + str(i), "failed", 1, 1) for i in range(23)])
    secret = "Bearer secret-value https://example.test/?token=private"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO crawl_log(kind, listing_id, status, note, requested_at) VALUES(?,?,?,?,?)",
            ("detail", "f0", "error", secret, datetime.now(UTC).isoformat()),
        )
    first = dashboard.issues(str(path), status="failed", district_id="510104", page=1, page_size=20)
    second = dashboard.issues(
        str(path), status="failed", district_id="510104", page=2, page_size=20
    )
    assert first["total"] == second["total"] == 23
    assert len(first["items"]) == 20 and len(second["items"]) == 3
    assert not (
        {r["listing_id"] for r in first["items"]} & {r["listing_id"] for r in second["items"]}
    )
    assert "secret-value" not in json.dumps([first, second])
    assert "https://" not in json.dumps([first, second])


@pytest.mark.parametrize(
    "state,seconds,expected",
    [
        ("running", 0, "running"),
        ("paused_storage", 0, "paused_storage"),
        ("running", 150, "stale"),
        ("idle", 150, "stale"),
        ("stopped", 150, "stopped"),
    ],
)
def test_vision_heartbeat_requires_recency(tmp_path, state, seconds, expected):
    path = str(tmp_path / "vision.db")
    store = ArchiveVisionStore(path)
    store.set_meta(
        "dashboard:heartbeat",
        json.dumps(
            {
                "state": state,
                "updated_at": (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat(),
            }
        ),
    )
    result = dashboard.vision_status(path, {"rental": str(tmp_path / "missing.db")})
    assert result["worker_state"] == expected


def test_storage_pause_sidecar_does_not_create_vision_db(tmp_path):
    path = str(tmp_path / "vision.db")
    with WorkerTelemetry(path, 2) as telemetry:
        telemetry.state("paused_storage", disk_free_gib=0.5)
        telemetry.batch({"status": "paused_storage", "attempted": 0})
        assert not (tmp_path / "vision.db").exists()
        result = dashboard.vision_status(path, {})
        assert result["worker_state"] == "paused_storage" and not result["available"]
    assert dashboard.vision_status(path, {})["worker_state"] == "stopped"


def test_rental_storage_biz_is_rent_and_sources_are_joined(tmp_path):
    src, target = tmp_path / "rental.db", str(tmp_path / "vision.db")
    source(src)
    store = ArchiveVisionStore(target)
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO listing_vision(biz,listing_id,floor_plan_status,vision_status,"
            "model_id,prompt_version,schema_version,rules_version) VALUES(?,?,?,?,?,?,?,?)",
            (
                "rent",
                "fresh",
                "ok",
                "ready",
                vision.MODEL_ID,
                vision.PROMPT_VERSION,
                vision.SCHEMA_VERSION,
                vision.RULES_VERSION,
            ),
        )
    result = dashboard.vision_status(target, {"rental": str(src)})["businesses"]["rental"]
    assert result["source_active"] == 5 and result["stored_listing_results"] == 1
    assert result["current_version_results"] == 1 and result["source_without_summary"] == 4
    assert result["pending_count"] is None and result["effective_valid_count"] is None


def test_real_model_calls_exclude_sha_and_url_cache_hits(tmp_path):
    src, target = tmp_path / "rental.db", str(tmp_path / "vision.db")
    source(src, entries=[("one", "complete", 1, 1), ("two", "complete", 1, 1)])
    calls = []

    def model(blob):
        calls.append(blob)
        return "户型图", "readable", ["阳台"], {}

    worker = vision.ArchiveVisionRunner(
        str(src),
        None,
        target,
        batch_size=10,
        scan_size=20,
        min_free_gib=0,
        downloader=lambda _: b"\xff\xd8same-image",
        model_caller=model,
    )
    first = worker.run_once()
    assert first["model_calls"] == 1 and first["sha_cache_hit"] == 1
    assert first["url_cache_hit"] == 0 and len(calls) == 1
    assert first["total_tokens"] is None
    second = worker.run_once()
    assert second["model_calls"] == 0 and second["url_cache_hit"] == 2
    status = dashboard.vision_status(target, {"rental": str(src)})
    assert status["worker_state"] == "stopped"
    assert status["metrics"]["model_calls"] == 0


def test_usage_is_only_from_provider_response(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "offline-unit-test")
    stats = new_model_stats()

    class Reply:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            content = json.dumps({"image_kind": "户型图", "readability": "readable", "labels": []})
            return json.dumps(
                {
                    "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                }
            ).encode()

    monkeypatch.setattr(vision.urllib.request, "urlopen", lambda *a, **kw: Reply())
    assert vision.default_call_model(b"\xff\xd8test", telemetry=stats) is not None
    assert (
        stats["total_tokens"] == 10
        and stats["usage_responses"] == 1
        and stats["http_attempts"] == 1
    )
    record_usage(stats, {"prompt_tokens": True, "total_tokens": -4})
    assert stats["total_tokens"] == 10 and stats["prompt_tokens"] == 7


def test_dashboard_api_is_additive_and_read_only(tmp_path, monkeypatch):
    rental, sale = tmp_path / "rental.db", tmp_path / "sale.db"
    source(rental)
    source(sale, "sale")
    for path in (rental, sale):
        with sqlite3.connect(path) as conn:
            conn.execute(
                "UPDATE listing_current SET district_id='999999' WHERE listing_id='failure'"
            )
    monkeypatch.setenv("CC_ARCHIVE_VISION_DB_PATH", str(tmp_path / "missing-vision.db"))
    app = create_app(
        Settings(
            upstream_profile="unconfigured",
            rental_archive_enabled=False,
            sale_archive_enabled=False,
            session_watchdog_enabled=False,
            rental_archive_db_path=str(rental),
            sale_archive_db_path=str(sale),
        )
    )
    with TestClient(app) as client:
        for biz in ("rental", "sale"):
            legacy = client.get("/api/v1/archive/" + biz + "/status").json()
            response = client.get("/api/v1/archive/" + biz + "/dashboard")
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            body = response.json()
            assert body["listing_total"] == legacy["listing_total"] == 6
            assert body["dashboard"]["totals"]["active_fresh"] == 1
            assert sum(row["total"] for row in body["dashboard"]["districts"]) == 6
            assert any(row["district_id"] == "999999" for row in body["dashboard"]["districts"])
            assert client.get("/api/v1/archive/" + biz + "/issues?page_size=51").status_code == 422
        assert client.get("/api/v1/archive/vision/status").json()["available"] is False
        assert client.get("/api/v1/archive/unknown/dashboard").status_code == 422


def test_manual_disable_and_window_wait_are_not_failures():
    assert dashboard.activity({"enabled": False}, {})["state"] == "inactive"
    result = dashboard.activity(
        {"enabled": True, "within_window": False, "connection_state": "auth_required"}, {}
    )
    assert result["state"] == "waiting" and result["reason"] == "outside_window"


def test_future_progress_does_not_claim_healthy():
    status = {"enabled": True, "running": True, "within_window": True, "connection_state": "ready"}
    snapshot = {"last_progress_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
    assert dashboard.activity(status, snapshot)["state"] == "unknown"


@pytest.mark.parametrize("biz", ["rental", "sale"])
def test_freshness_query_uses_covering_index_without_payload_reads(tmp_path, monkeypatch, biz):
    from contextlib import contextmanager

    path = tmp_path / (biz + ".db")
    source(path, biz)
    statements = []
    original = dashboard.readonly

    @contextmanager
    def traced(value):
        with original(value) as conn:
            if conn:
                conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(dashboard, "readonly", traced)
    dashboard.archive_snapshot(str(path), 14)
    aggregation = next(sql for sql in statements if sql.upper().startswith("SELECT DISTRICT_ID"))
    with sqlite3.connect(path) as conn:
        plan = " ".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + aggregation))
    assert "USING COVERING INDEX" in plan
    assert "idx_listing_current_freshness" in plan
    assert not any(sql.startswith("SELECT MAX(detail_captured_at)") for sql in statements)
