import json, sqlite3, sys, dataclasses
from pathlib import Path
import pytest
SERVICE=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(SERVICE))
import app.application.archive_vision as av
from app.infrastructure.archive_vision_store import ArchiveVisionStore

def test_imports_current_project():
    assert Path(av.__file__).resolve().is_relative_to(SERVICE.resolve())


def seed(path,url="https://img.ljcdn.com/a.jpg"):
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE listing_current(listing_id TEXT PRIMARY KEY,payload_json TEXT,content_hash TEXT,is_active INTEGER DEFAULT 1)")
        p={"listing_id":"r1","list":{"floor_plan_image_url":url},"detail":{}}
        c.execute("INSERT INTO listing_current VALUES(?,?,?,1)",("r1",json.dumps(p),"unchanged"))


def model(_):
    labels=["阳台","卧室","客厅","厨房"]
    return "户型图","readable",labels,{"image_kind":"户型图","readability":"readable","labels":labels}


def run(s,t,downloader=None):
    av.run_pipeline(str(s),None,str(t),downloader=downloader or (lambda _:b"\xff\xd8same"),model_caller=model)


def roll(labels):
    ds=[av.derive_labels("户型图","readable",x) for x in labels]
    pairs=[(av.ImageRef("rent","r1","list_floor_plan",f"https://img.ljcdn.com/{i}.jpg"),d) for i,d in enumerate(ds)]
    return av.rollup_listing(pairs,crm=None)


def test_feature_conflict_has_conflict_status():
    lv=roll([["阳台"],["无阳台"]])
    assert lv.has_balcony is None
    assert lv.floor_plan_status=="conflict"


def test_negative_plus_download_error_stays_unknown():
    ds=[av.derive_labels("户型图","readable",["无阳台"]),av.Derived("unknown","no_text",[],"error")]
    pairs=[(av.ImageRef("rent","r1","list_floor_plan",f"https://img.ljcdn.com/{i}.jpg"),d) for i,d in enumerate(ds)]
    assert av.rollup_listing(pairs,crm=None).has_balcony is None


def test_shop_with_toilet_is_shop():
    assert av.derive_labels("户型图","readable",["商铺","卫生间"]).use_kind=="商铺"


def test_unreadable_image_cannot_supply_positive_features():
    assert av.derive_labels("户型图","unreadable",["阳台"]).has_balcony is None


def test_multi_level_shop_not_ground():
    assert av.derive_labels("户型图","readable",["商铺","一层","二层"]).is_ground_shop is None


def test_garden_order_cannot_change_position():
    a=roll([["入户花园"],["屋顶花园"]]);b=roll([["屋顶花园"],["入户花园"]])
    assert a.garden_position==b.garden_position


@pytest.mark.parametrize("p",[
    {"list":{"property_purpose":"住宅"}},
    {"detail":{"head":{"house_usage":"住宅"}}}
])
def test_all_declared_crm_use_sources_checked(p):
    d=av.derive_labels("户型图","readable",["商铺","首层"])
    pair=(av.ImageRef("rent","r1","list_floor_plan","https://img.ljcdn.com/a.jpg"),d)
    assert av.rollup_listing([pair],crm=av.extract_crm_hints(p,"rent")).use_conflict==1


@pytest.mark.parametrize("mutation",["inactive","delete"])
def test_inactive_or_deleted_listing_cannot_remain_filterable(tmp_path,mutation):
    s=tmp_path/"r.db";t=tmp_path/"v.db";seed(s);run(s,t)
    with sqlite3.connect(s) as c:
        c.execute("UPDATE listing_current SET is_active=0" if mutation=="inactive" else "DELETE FROM listing_current")
    run(s,t)
    assert ArchiveVisionStore(str(t)).filter_listing_ids("rent",has_balcony=1)==[]


def test_replaced_url_hidden_before_downloading(tmp_path):
    s=tmp_path/"r.db";t=tmp_path/"v.db";seed(s);run(s,t)
    with sqlite3.connect(s) as c:
        p={"listing_id":"r1","list":{"floor_plan_image_url":"https://img.ljcdn.com/new.jpg"},"detail":{}}
        c.execute("UPDATE listing_current SET payload_json=?",(json.dumps(p),))
    observed=[]
    def download(_):
        observed.append(ArchiveVisionStore(str(t)).filter_listing_ids("rent",has_balcony=1))
        return b"\xff\xd8new"
    run(s,t,download)
    assert observed==[[]]


def test_existing_cached_labels_are_rederived_after_rule_fix(tmp_path):
    s=tmp_path/"r.db";t=tmp_path/"v.db";seed(s)
    labels=["阳台","无阳台"]
    outdated=dataclasses.asdict(av.derive_labels("户型图","readable",labels))
    outdated["has_balcony"]=1
    ArchiveVisionStore(str(t)).upsert_observation("https://img.ljcdn.com/a.jpg",status="ok",image_kind="户型图",readability="readable",labels_json=json.dumps(labels),derived_json=json.dumps(outdated),model_id=av.MODEL_ID,prompt_version=av.PROMPT_VERSION)
    run(s,t,lambda _:pytest.fail("Rule repair must not call downloader"))
    assert ArchiveVisionStore(str(t)).get_listing_vision("rent","r1")["has_balcony"] is None


def test_bad_source_schema_is_reported(tmp_path):
    s=tmp_path/"bad.db";sqlite3.connect(s).close()
    with pytest.raises((RuntimeError,sqlite3.DatabaseError)):
        av.run_pipeline(str(s),None,str(tmp_path/"v.db"))


def test_read_api_does_not_create_missing_db(tmp_path):
    p=tmp_path/"missing.db"
    try:ArchiveVisionStore(str(p)).get_listing_vision("rent","r1")
    except (FileNotFoundError,sqlite3.OperationalError):pass
    assert not p.exists()

