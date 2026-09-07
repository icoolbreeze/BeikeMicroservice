from app.application.roughcast_districts import persist_districts
from app.infrastructure.database import Database
from app.infrastructure.district_catalog import parse_district_catalog
from app.infrastructure.roughcast_repository import RoughcastRepository
from tests.roughcast_helpers import seed_community


def test_persist_districts_writes_unique_and_ambiguous(tmp_path) -> None:
    database = Database(tmp_path / "store.sqlite3")
    database.initialize()
    seed_community(database, community_id="RB1", name="建设路小区", bizcircle="建设路")
    seed_community(database, community_id="RB2", name="南湖小区", bizcircle="南湖")
    seed_community(database, community_id="RB3", name="无名小区", bizcircle="没有这个商圈")
    catalog = parse_district_catalog([{
        "key": "districtId",
        "name": "商圈",
        "children": [
            {"name": "成华", "value": "510108", "children": [
                {"name": "建设路", "value": "1", "children": []},
            ]},
            {"name": "双流", "value": "510122", "children": [
                {"name": "南湖", "value": "2", "children": []},
            ]},
            {"name": "天府新区", "value": "23008850", "children": [
                {"name": "南湖", "value": "3", "children": []},
            ]},
        ],
    }])
    repo = RoughcastRepository(database)
    report = persist_districts(repo, catalog, captured_at="2026-08-24T00:00:00+00:00")
    assert report.unique == 1
    assert report.ambiguous == 1
    assert report.unknown == 1
    with database.connect() as db:
        chenghua = db.execute(
            "SELECT district, district_status FROM roughcast_communities WHERE id='RB1'"
        ).fetchone()
        nanhu = db.execute(
            "SELECT district, district_status, districts_json FROM roughcast_communities WHERE id='RB2'"
        ).fetchone()
        rows = db.execute(
            "SELECT community_id, district FROM roughcast_community_district ORDER BY community_id, district"
        ).fetchall()
        catalog_n = db.execute("SELECT COUNT(*) FROM roughcast_bizcircle_district").fetchone()[0]
    assert chenghua["district"] == "成华"
    assert chenghua["district_status"] == "unique"
    assert nanhu["district"] is None
    assert nanhu["district_status"] == "ambiguous"
    assert "双流" in nanhu["districts_json"]
    assert [(row["community_id"], row["district"]) for row in rows] == [
        ("RB1", "成华"),
        ("RB2", "双流"),
        ("RB2", "天府新区"),
    ]
    assert catalog_n == 3
    loaded = repo.load_district_catalog()
    assert loaded.label("建设路") == "成华"
    assert loaded.label("南湖") == "双流/天府新区"
