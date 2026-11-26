"""Additive dashboard endpoints; legacy archive contracts remain unchanged."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from app.api.schemas import ArchiveStatusResponse, SaleArchiveStatusResponse
from app.application.archive_dashboard import activity, archive_snapshot, issues, vision_status

router = APIRouter(prefix="/api/v1/archive", tags=["archive-dashboard"])
Business = Literal["rental", "sale"]


def runner_for(request: Request, biz: Business):
    runner = getattr(request.app.state, "crm_" + biz + "_archive_runner", None)
    if runner is None:
        raise HTTPException(503, detail={"code": "ARCHIVE_NOT_CONFIGURED"})
    return runner


@router.get("/{biz}/dashboard")
def dashboard(request: Request, response: Response, biz: Business) -> dict:
    runner = runner_for(request, biz)
    schema = ArchiveStatusResponse if biz == "rental" else SaleArchiveStatusResponse
    response.headers["Cache-Control"] = "no-store"
    try:
        status = schema.from_domain(runner.status()).model_dump(mode="json")
        snapshot = archive_snapshot(runner.store.db_path, status["detail_ttl_days"])
    except sqlite3.Error:
        raise HTTPException(503, detail={"code": "ARCHIVE_DASHBOARD_UNAVAILABLE"}) from None
    snapshot = dict(snapshot)
    snapshot["activity"] = activity(
        status, snapshot, getattr(runner._settings, biz + "_archive_pause_seconds_max", 360)
    )
    current = (status.get("cursor") or {}).get("district_id")
    by_id = {row["district_id"]: row for row in snapshot["districts"]}
    zeros = {key: 0 for key in (snapshot.get("totals") or {})}
    snapshot["districts"] = [
        {
            **district,
            **by_id.get(district["district_id"], zeros),
            "scan_state": "scanning"
            if district["district_id"] == current
            and status.get("running")
            and status.get("within_window")
            and status.get("enabled")
            else "unknown",
        }
        for district in status.get("districts", [])
    ]
    known_ids = {row["district_id"] for row in snapshot["districts"]}
    snapshot["districts"].extend(
        {
            **row,
            "district_name": "未配置分区 " + str(row["district_id"] or "未知"),
            "scan_state": "unknown",
        }
        for row in by_id.values()
        if row["district_id"] not in known_ids
    )
    return {**status, "dashboard": snapshot}


@router.get("/{biz}/issues")
def archive_issues(
    request: Request,
    response: Response,
    biz: Business,
    status: Literal["failed", "skipped_unsupported"] = "failed",
    district_id: str | None = Query(None, max_length=32),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return issues(
            runner_for(request, biz).store.db_path,
            status=status,
            district_id=district_id,
            page=page,
            page_size=page_size,
        )
    except sqlite3.Error:
        raise HTTPException(503, detail={"code": "ARCHIVE_ISSUES_UNAVAILABLE"}) from None


@router.get("/vision/status")
def archive_vision_status(request: Request, response: Response) -> dict:
    rental = runner_for(request, "rental").store.db_path
    sale = runner_for(request, "sale").store.db_path
    path = os.getenv("CC_ARCHIVE_VISION_DB_PATH", "").strip() or str(
        Path(rental).with_name("archive_vision.sqlite3")
    )
    response.headers["Cache-Control"] = "no-store"
    try:
        return vision_status(path, {"rental": rental, "sale": sale})
    except sqlite3.Error:
        raise HTTPException(503, detail={"code": "VISION_DASHBOARD_UNAVAILABLE"}) from None
