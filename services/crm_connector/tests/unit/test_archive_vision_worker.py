"""Behavioral acceptance for bounded standalone worker (no remote network)."""
import json
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import sqlite3
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from test_archive_vision_boundaries import source, runner, model, av, ArchiveVisionStore
from test_client_rebuild_acceptance import Reply, server


def wait_for_result(worker):
    end = time.monotonic() + 3
    while worker.last_metrics is None and time.monotonic() < end:
        time.sleep(.01)
    assert worker.last_metrics is not None


def test_watch_holds_lock_while_idle_and_rebuild_cannot_write(tmp_path):
    s, v = tmp_path/"s.db", tmp_path/"v.db"
    source(s, "r", 1)
    calls = []
    first = runner(s, None, v, calls, 1)
    first.poll_interval = 60
    first.start()
    try:
        wait_for_result(first)
        with pytest.raises(RuntimeError, match="already running"):
            runner(s, None, v, [], 1).run_once()
        with pytest.raises(RuntimeError, match="already running"):
            av.rebuild_derived(str(s), None, str(v))
        assert len(calls) == 1
    finally:
        first.stop()
    assert not first._thread.is_alive()
    assert runner(s, None, v, [], 1).run_once()["cache_hit"] == 1


def test_storage_pause_never_creates_vision_or_calls_network(tmp_path, monkeypatch):
    s, v = tmp_path/"s.db", tmp_path/"v.db"
    source(s, "r", 1)
    calls = []
    worker = runner(s, None, v, calls, 1)
    monkeypatch.setattr(av.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    result = worker.run_once()
    assert result["status"] == "paused_storage" and result["attempted"] == 0
    assert not v.exists() and not calls


@pytest.mark.parametrize("kind", ["no_source", "missing_source"])
def test_invalid_watch_start_fails_synchronously(tmp_path, kind):
    s = tmp_path/"missing.db" if kind == "missing_source" else None
    worker = av.ArchiveVisionRunner(str(s) if s else None, None, str(tmp_path/"v.db"), model_caller=model)
    with pytest.raises((ValueError, FileNotFoundError)):
        worker.start()
    assert worker._thread is None
    assert not (tmp_path/"v.db").exists()


def test_multi_image_listing_resumes_without_error_for_unattempted_image(tmp_path):
    s, v = tmp_path/"s.db", tmp_path/"v.db"
    source(s, "r", 1)
    payload = {"listing_id":"r000000","list":{"floor_plan_image_url":"https://img.ljcdn.com/a.jpg"},
               "detail":{"prospect":{"floor_plan_url":"https://img.ljcdn.com/b.jpg"}}}
    with sqlite3.connect(s) as conn:
        conn.execute("UPDATE listing_current SET payload_json=?", (json.dumps(payload),))
    calls = []
    worker = runner(s, None, v, calls, 1)
    one = worker.run_once()
    st = ArchiveVisionStore(str(v))
    assert one["attempted"] == 1 and one["pending"] == 1
    assert st.get_observation("https://img.ljcdn.com/b.jpg") is None
    assert st.get_listing_vision("rent", "r000000")["vision_status"] == "stale"
    two = worker.run_once()
    assert two["attempted"] == 1 and len(set(calls)) == 2
    assert st.get_listing_vision("rent", "r000000")["vision_status"] == "ready"


def test_shared_request_spacing_applies_to_retry(monkeypatch):
    clock, sent = [100.], []
    monkeypatch.setenv("NVIDIA_API_KEY", "offline-dummy")
    monkeypatch.setattr(av, "_MODEL_LAST_REQUEST", 0.)
    monkeypatch.setattr(av.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(av.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0]+seconds))
    def request(*args, **kwargs):
        sent.append(clock[0])
        assert kwargs["timeout"] == 22.
        if len(sent) == 1:
            raise urllib.error.HTTPError("http://127.0.0.1/model", 429, "rate", {"Retry-After":".25"}, None)
        return Reply(model(None)[3])
    monkeypatch.setattr(urllib.request, "urlopen", request)
    assert av.default_call_model(b"\xff\xd8fake", min_request_interval=4.) is not None
    assert sent == [100., 104.]

def test_real_cli_limit_is_whole_invocation_cap(tmp_path, server):
    endpoint, calls = server
    src, target = tmp_path/"source.db", tmp_path/"target.db"
    source(src, "r", 3)
    with sqlite3.connect(src) as conn:
        for i in range(3):
            payload = {"listing_id": f"r{i:06d}", "list": {"floor_plan_image_url": endpoint+f"/p{i}.jpg"}}
            conn.execute("UPDATE listing_current SET payload_json=? WHERE listing_id=?", (json.dumps(payload), f"r{i:06d}"))
    env = os.environ.copy()
    env["NVIDIA_API_KEY"] = "offline-contract-dummy"
    env["NVIDIA_API_BASE"] = endpoint+"/v1/chat/completions"
    p = subprocess.run([sys.executable, "-m", "app.application.archive_vision", "--rental-db", str(src),
                        "--vision-db", str(target), "--backfill", "--limit", "1", "--batch-size", "20",
                        "--min-free-gib", "0"], cwd=Path(__file__).resolve().parents[2],
                       env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 0, p.stderr
    assert len([c for c in calls if c[0] == "GET"]) == 1
    assert len([c for c in calls if c[0] == "POST"]) == 1
    assert json.loads(p.stdout)["attempted"] == 1
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT COUNT(*) FROM image_observation WHERE status='error'").fetchone()[0] == 0


@pytest.mark.parametrize("status", ["paused_storage", "ok"])
def test_main_returns_success_when_signal_requests_shutdown(tmp_path, monkeypatch, status):
    import signal
    import threading
    handlers = {}
    def register(sig, handler):
        previous = handlers.get(sig, signal.SIG_DFL)
        handlers[sig] = handler
        return previous
    monkeypatch.setattr(av.signal, "signal", register)
    class Worker:
        def __init__(self, *args, **kwargs):
            self._stop_event = threading.Event()
        def run(self, **kwargs):
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            return {"status": status, "has_failure": False}
    monkeypatch.setattr(av, "ArchiveVisionRunner", Worker)
    monkeypatch.setattr(sys, "argv", ["archive_vision", "--vision-db", str(tmp_path/"v.db"), "--watch"])
    assert av.main() is None
