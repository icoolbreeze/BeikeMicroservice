"""把小区按 CRM 商圈目录归到行政区,并写入本地库。"""

from __future__ import annotations

from dataclasses import dataclass

from app.infrastructure.district_catalog import (
    STATUS_AMBIGUOUS,
    STATUS_UNIQUE,
    STATUS_UNKNOWN,
    DistrictCatalog,
    classify_bizcircle,
)
from app.infrastructure.roughcast_repository import RoughcastRepository


@dataclass(frozen=True)
class DistrictAssignReport:
    unique: int
    ambiguous: int
    unknown: int
    catalog_pairs: int
    captured_at: str


def persist_districts(
    repository: RoughcastRepository,
    catalog: DistrictCatalog,
    *,
    captured_at: str,
) -> DistrictAssignReport:
    """整表替换商圈目录,并给当前全部小区打行政区。"""
    repository.replace_bizcircle_district_catalog(catalog, captured_at=captured_at)
    communities = [
        row for row in repository.list_communities_for_district()
        if (row["district_source"] if "district_source" in row.keys() else None)
        not in {"baidu", "beike_map"}
    ]
    assignments = [
        _assignment(row["id"], row["bizcircle"], catalog) for row in communities
    ]
    repository.apply_community_district_assignments(assignments, assigned_at=captured_at)
    unique = sum(1 for item in assignments if item[3] == STATUS_UNIQUE)
    ambiguous = sum(1 for item in assignments if item[3] == STATUS_AMBIGUOUS)
    unknown = sum(1 for item in assignments if item[3] == STATUS_UNKNOWN)
    return DistrictAssignReport(
        unique=unique,
        ambiguous=ambiguous,
        unknown=unknown,
        catalog_pairs=len(catalog.pairs()),
        captured_at=captured_at,
    )


def _assignment(
    community_id: str, bizcircle: str | None, catalog: DistrictCatalog
) -> tuple[str, str | None, tuple[str, ...], str]:
    district, names, status = classify_bizcircle(catalog, bizcircle)
    return (community_id, district, names, status)
