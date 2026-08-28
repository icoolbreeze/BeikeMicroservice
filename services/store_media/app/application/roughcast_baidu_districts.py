"""用百度地点检索 / 贝壳 BD-09 地图给小区打行政区,坐标与区划一并落库。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from app.infrastructure.baidu_map_client import BaiduMapClient, BaiduMapError, BaiduPlaceHit
from app.infrastructure.crm_map_geocoder import CrmMapGeocoder
from app.infrastructure.roughcast_repository import RoughcastRepository

logger = logging.getLogger(__name__)
SOURCE_BAIDU = "baidu"
SOURCE_BEIKE_MAP = "beike_map"
LOCKED_SOURCES = frozenset({SOURCE_BAIDU, SOURCE_BEIKE_MAP})


@dataclass(frozen=True)
class BaiduMarkReport:
    marked: int
    missed: int
    skipped: int
    errors: int
    baidu_marked: int
    beike_marked: int


class CommunityGeocoder:
    """百度地点检索优先;连续失败后改走贝壳地图联想(同一套 BD-09)。"""

    def __init__(
        self,
        baidu: BaiduMapClient | None = None,
        crm: CrmMapGeocoder | None = None,
        *,
        baidu_fail_limit: int = 3,
    ) -> None:
        self._baidu = baidu
        self._crm = crm
        self._baidu_fail_limit = baidu_fail_limit
        self._baidu_failures = 0
        self._baidu_disabled = baidu is None

    def locate(
        self, name: str, community_id: str | None = None
    ) -> tuple[BaiduPlaceHit | None, str | None]:
        if not self._baidu_disabled and self._baidu is not None:
            try:
                hit = self._baidu.locate_community(name)
                self._baidu_failures = 0
                if hit is not None:
                    return hit, SOURCE_BAIDU
            except BaiduMapError as exc:
                self._baidu_failures += 1
                logger.warning("baidu geocode failed err=%s", exc)
                if self._baidu_failures >= self._baidu_fail_limit:
                    self._baidu_disabled = True
                    logger.warning("baidu geocode disabled after repeated failures")
        if self._crm is not None:
            hit = self._crm.locate_community(name, community_id)
            if hit is not None:
                return hit, SOURCE_BEIKE_MAP
        return None, None


def mark_communities_with_baidu(
    repository: RoughcastRepository,
    client: CommunityGeocoder | BaiduMapClient,
    *,
    limit: int | None = None,
    sleep_seconds: float = 0.35,
    sleeper: Callable[[float], None] = time.sleep,
    now: str | None = None,
) -> BaiduMarkReport:
    geocoder = client if isinstance(client, CommunityGeocoder) else CommunityGeocoder(baidu=client)
    assigned_at = now or datetime.now(UTC).isoformat()
    pending = repository.list_communities_pending_baidu()
    if limit is not None:
        pending = pending[: max(0, limit)]
    marked = missed = errors = baidu_marked = beike_marked = 0
    skipped = repository.count_baidu_marked_communities()
    for index, row in enumerate(pending, start=1):
        community_id = str(row["id"])
        name = str(row["name"] or "")
        try:
            hit, source = geocoder.locate(name, community_id)
        except Exception:
            logger.exception("community geocode failed community_id=%s", community_id)
            errors += 1
            sleeper(sleep_seconds)
            continue
        if hit is None or source is None:
            missed += 1
        else:
            repository.apply_baidu_community_mark(
                community_id, hit, assigned_at=assigned_at, source=source
            )
            marked += 1
            if source == SOURCE_BAIDU:
                baidu_marked += 1
            else:
                beike_marked += 1
        if index % 50 == 0:
            logger.info(
                "district mark progress %s/%s marked=%s missed=%s",
                index, len(pending), marked, missed,
            )
        sleeper(sleep_seconds)
    return BaiduMarkReport(
        marked=marked, missed=missed, skipped=skipped, errors=errors,
        baidu_marked=baidu_marked, beike_marked=beike_marked,
    )
