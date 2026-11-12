from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.infrastructure.rental_archive_store import ArchiveListingRow, RentalArchiveStore
from app.infrastructure.sale_archive_store import ArchiveListingRow as SaleArchiveListingRow
from app.infrastructure.sale_archive_store import SaleArchiveStore


def test_rental_archive_indexes(tmp_path) -> None:
    db_path = tmp_path / "rental.sqlite3"
    store = RentalArchiveStore(str(db_path))
    row = ArchiveListingRow(
        listing_id="L1",
        district_id="510107",
        price_band=None,
        del_type=None,
        community=None,
        payload={},
        content_hash="abc",
    )
    store.upsert_list_row(row, run_id=None, now=datetime.now(UTC))

    conn = sqlite3.connect(str(db_path))
    indexes = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    conn.close()

    assert "idx_listing_current_detail_status" in indexes
    assert "idx_listing_current_district_detail" in indexes


def test_sale_archive_indexes(tmp_path) -> None:
    db_path = tmp_path / "sale.sqlite3"
    store = SaleArchiveStore(str(db_path))
    row = SaleArchiveListingRow(
        listing_id="L1",
        district_id="510107",
        price_band=None,
        del_type=None,
        community=None,
        payload={},
        content_hash="abc",
    )
    store.upsert_list_row(row, run_id=None, now=datetime.now(UTC))

    conn = sqlite3.connect(str(db_path))
    indexes = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    conn.close()

    assert "idx_listing_current_detail_status" in indexes
    assert "idx_listing_current_district_detail" in indexes
