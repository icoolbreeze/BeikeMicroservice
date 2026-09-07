from app.application.roughcast_baidu_districts import mark_communities_with_baidu
from app.application.roughcast_districts import persist_districts
from app.infrastructure.baidu_map_client import BaiduPlaceHit
from app.infrastructure.database import Database
from app.infrastructure.district_catalog import parse_district_catalog
from app.infrastructure.roughcast_repository import RoughcastRepository
from tests.roughcast_helpers import seed_community


class FakeBaiduClient:
    def __init__(self, hits: dict[str, BaiduPlaceHit | None]) -> None:
        self.hits = hits
        self.calls: list[str] = []

    def locate_community(self, name: str) -> BaiduPlaceHit | None:
        self.calls.append(name)
        return self.hits.get(name)


def test_baidu_mark_writes_coords_and_is_not_overwritten_by_catalog(tmp_path) -> None:
    database = Database(tmp_path / "store.sqlite3")
    database.initialize()
    seed_community(database, community_id="RB1", name="万科知园A区", bizcircle="理工大")
    seed_community(database, community_id="RB2", name="南湖小区", bizcircle="南湖")
    repo = RoughcastRepository(database)
    hit = BaiduPlaceHit(
        name="新希望万科知园", latitude=30.695, longitude=104.134,
        area="成华区", district="成华", address="仙韵四路", uid="uid-1",
    )
    report = mark_communities_with_baidu(
        repo,
        FakeBaiduClient({"万科知园A区": hit, "南湖小区": None}),
        sleep_seconds=0,
        now="2026-08-24T00:00:00+00:00",
    )
    assert report.marked == 1
    assert report.missed == 1
    catalog = parse_district_catalog([{
        "key": "districtId", "name": "商圈", "children": [
            {"name": "成华", "value": "1", "children": [
                {"name": "理工大", "value": "a", "children": []},
            ]},
            {"name": "双流", "value": "2", "children": [
                {"name": "南湖", "value": "b", "children": []},
            ]},
        ],
    }])
    persist_districts(repo, catalog, captured_at="2026-08-24T01:00:00+00:00")
    with database.connect() as db:
        marked = db.execute(
            "SELECT district, district_source, latitude, baidu_poi_name "
            "FROM roughcast_communities WHERE id='RB1'"
        ).fetchone()
        nanhu = db.execute(
            "SELECT district, district_source, district_status "
            "FROM roughcast_communities WHERE id='RB2'"
        ).fetchone()
    assert marked["district"] == "成华"
    assert marked["district_source"] == "baidu"
    assert marked["latitude"] == 30.695
    assert marked["baidu_poi_name"] == "新希望万科知园"
    assert nanhu["district"] == "双流"
    assert nanhu["district_source"] is None
    assert nanhu["district_status"] == "unique"
    assert repo.count_baidu_marked_communities() == 1
    assert repo.list_communities_pending_baidu()[0]["id"] == "RB2"
