# ruff: noqa: E402
from pathlib import Path
import sys
import os
import json
import threading
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(root / "services/crm_connector"))
from app.main import create_app
from app.infrastructure.settings import Settings
from fastapi.testclient import TestClient
import tempfile
import sqlite3
import importlib

qa = Path(tempfile.mkdtemp(prefix="archive-dashboard-qa-"))
for biz in ["rental", "sale"]:
    module = importlib.import_module("app.infrastructure." + biz + "_archive_store")
    from datetime import datetime, UTC, timedelta

    now = datetime.now(UTC)
    with sqlite3.connect(qa / (biz + "_archive.sqlite3")) as conn:
        conn.executescript(module._SCHEMA)
        for i, (status, age, active) in enumerate(
            [
                ("complete", 1, 1),
                ("complete", 20, 1),
                ("failed", 1, 1),
                ("none", None, 1),
                ("skipped_unsupported", 1, 1),
                ("complete", 1, 0),
            ]
        ):
            captured = (now - timedelta(days=age)).isoformat() if age is not None else None
            conn.execute(
                "INSERT INTO listing_current(listing_id,district_id,payload_json,content_hash,detail_status,detail_captured_at,first_seen_at,last_seen_at,is_active) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    biz + "-" + str(i),
                    "510104" if i % 2 == 0 else "510108",
                    "{}",
                    "qa-" + str(i),
                    status,
                    captured,
                    now.isoformat(),
                    now.isoformat(),
                    active,
                ),
            )
os.environ["CC_ARCHIVE_VISION_DB_PATH"] = str(qa / "missing-vision.db")
app = create_app(
    Settings(
        upstream_profile="unconfigured",
        rental_archive_enabled=False,
        sale_archive_enabled=False,
        session_watchdog_enabled=False,
        rental_archive_db_path=str(qa / "rental_archive.sqlite3"),
        sale_archive_db_path=str(qa / "sale_archive.sqlite3"),
    )
)
payloads = {}
with TestClient(app) as client:
    for biz in ["rental", "sale"]:
        r = client.get("/api/v1/archive/" + biz + "/dashboard")
        assert r.status_code == 200, r.text
        payloads[biz] = r.json()
    payloads["vision"] = client.get("/api/v1/archive/vision/status").json()
payloads["vision"].update(
    worker_state="idle",
    available=True,
    heartbeat={
        "state": "idle",
        "updated_at": payloads["vision"]["computed_at"],
        "disk_free_gib": 15.5,
    },
    metrics={
        "computed_at": payloads["vision"]["computed_at"],
        "model_calls": 12,
        "url_cache_hit": 53,
        "sha_cache_hit": 7,
        "average_model_seconds": 1.25,
        "total_tokens": None,
        "success": 12,
        "miss": 2,
        "error": 1,
        "cooldown": 3,
        "http_attempts": 13,
        "usage_responses": 0,
    },
)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (root / "services/crm_connector/app/static/archive.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html;charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
from playwright.sync_api import sync_playwright

modes = {"hold_rental": False}
seen = []
errors = []
held = []


def route_api(route):
    path = urlparse(route.request.url).path
    seen.append(path)
    if path.endswith("/issues"):
        q = parse_qs(urlparse(route.request.url).query)
        page = int(q.get("page", ["1"])[0])
        allrows = [
            {
                "listing_id": ("<img src=x onerror=window.__xss=1>" if i == 0 else "qa-" + str(i)),
                "district_id": "510104",
                "attempted_at": "2026-11-13T00:00:00+00:00",
                "error_code": "DETAIL_FAILED",
                "summary": "安全摘要 <script>window.__xss=1</script>",
                "log_id": i + 1,
            }
            for i in range(23)
        ]
        route.fulfill(
            json={
                "available": True,
                "items": allrows[(page - 1) * 20 : page * 20],
                "total": 23,
                "page": page,
                "page_size": 20,
            }
        )
        return
    biz = "vision" if "/vision/" in path else "sale" if "/sale/" in path else "rental"
    if biz == "rental" and modes["hold_rental"]:
        held.append(route)
        return
    route.fulfill(json=payloads[biz])


with sync_playwright() as p:
    browser = p.chromium.launch(
        **(
            {"channel": "msedge", "headless": True}
            if sys.platform == "win32"
            else {"headless": True}
        )
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1050})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(
        "const originalTimeout=window.setTimeout;window.setTimeout=(fn,ms,...args)=>originalTimeout(fn,ms===12000&&window.__shortTimeout?150:ms,...args);"
    )
    page.route("**/api/v1/archive/**", route_api)
    page.goto("http://127.0.0.1:" + str(server.server_port) + "/archive", wait_until="networkidle")
    assert page.locator("#content").inner_text().find("业务归档记录数") >= 0
    assert page.evaluate("ArchiveDashboard.percentage(99999,100000)") == "99.99%"
    assert page.evaluate("ArchiveDashboard.percentage(0,0)") == "—"
    page.screenshot(path=str(qa / "overview.png"), full_page=True)
    page.click("#tab-rental")
    page.wait_for_selector('[data-filter="rental"]')
    assert page.locator("#content tbody tr").count() == 14
    page.locator('[data-filter="rental"]').fill("锦江")
    assert page.locator("#content tbody tr").count() == 1
    assert "12" not in page.locator("#content tfoot").inner_text()  # filtered subset is six records
    page.locator('[data-filter="rental"]').fill("")
    page.click('[data-sort="failed"]')
    assert page.locator('th[aria-sort="descending"]').count() == 1
    page.locator('[data-issues="rental"][data-status="failed"]').click()
    page.wait_for_selector("#issues-content tbody tr")
    assert page.locator("#issues-content tbody tr").count() == 20
    assert (
        page.locator("#issues-content img").count() == 0 and page.evaluate("window.__xss") is None
    )
    page.click('[data-issue-page="1"]')
    page.wait_for_function('document.querySelectorAll("#issues-content tbody tr").length===3')
    page.keyboard.press("Escape")
    assert not page.locator("#issues").evaluate("(e)=>e.open")
    page.locator("#tab-rental").focus()
    page.keyboard.press("ArrowRight")
    assert page.locator("#tab-sale").get_attribute("aria-selected") == "true"
    page.click("#tab-rental")
    page.wait_for_function('!document.getElementById("refresh").disabled')
    before = page.locator("#content .number").all_text_contents()
    modes["hold_rental"] = True
    page.evaluate("window.__shortTimeout=true")
    page.click("#refresh")
    page.wait_for_function(
        'document.querySelector("#content .notice.bad")?.textContent.includes("请求超时")'
    )
    assert page.locator("#content .number").all_text_contents() == before
    modes["hold_rental"] = False
    page.wait_for_function('!document.getElementById("refresh").disabled')
    page.click("#refresh")
    page.wait_for_function('!document.querySelector("#content .notice.bad")')
    page.screenshot(path=str(qa / "rental.png"), full_page=True)
    page.click("#tab-vision")
    assert page.locator("#content").inner_text().find("SHA256 内容复用") >= 0
    page.screenshot(path=str(qa / "vision.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.click("#tab-rental")
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
    )
    page.screenshot(path=str(qa / "mobile.png"), full_page=True)
    assert not errors, errors
    for pending in held:
        try:
            pending.abort()
        except Exception:
            pass
    page.unroute_all(behavior="ignoreErrors")
    browser.close()
server.shutdown()
for name in ["overview", "rental", "vision", "mobile"]:
    image = qa / (name + ".png")
    (qa / (name + ".b64")).write_text(
        base64.b64encode(image.read_bytes()).decode("ascii"), encoding="utf-8"
    )
report = {
    "result": "passed",
    "checks": [
        "new API-backed overview",
        "strict percentage boundary",
        "district filter/sort/weighted total",
        "issue pagination and HTML escaping",
        "keyboard tabs",
        "timeout retains previous data",
        "poll recovery",
        "vision tab",
        "mobile no document overflow",
    ],
    "page_errors": errors,
    "api_requests": len(seen),
    "artifacts": str(qa),
}
(qa / "browser-report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=True))
