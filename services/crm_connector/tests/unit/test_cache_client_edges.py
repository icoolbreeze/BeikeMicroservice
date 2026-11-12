import datetime, email.utils, io, json, sqlite3, sys, urllib.request, urllib.error
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parent))
from test_client_rebuild_acceptance import av, ArchiveVisionStore, URL, model, seed, run, obs, Reply

def test_valid_final_ignores_auxiliary_reasoning(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY","offline-contract-dummy")
    data={"choices":[{"finish_reason":"stop","message":{"content":json.dumps(model(None)[3]),"reasoning_content":"ignored auxiliary text"}}]}
    class Response(io.BytesIO):
        status=200
        headers={}
    monkeypatch.setattr(urllib.request,"urlopen",lambda *a,**k:Response(json.dumps(data).encode()))
    monkeypatch.setattr(av.time,"sleep",lambda _:None)
    assert av.default_call_model(b"\xff\xd8fake") is not None

def test_http_error_retry_after_date(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY","offline-contract-dummy");calls=[];waits=[]
    when=datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=10)
    def request(*a,**k):
        calls.append(1)
        if len(calls)==1:raise urllib.error.HTTPError(URL,429,"limit",{"Retry-After":email.utils.format_datetime(when,usegmt=True)},None)
        return Reply(model(None)[3])
    monkeypatch.setattr(urllib.request,"urlopen",request);monkeypatch.setattr(av.time,"sleep",waits.append)
    assert av.default_call_model(b"\xff\xd8fake") is not None
    assert 5<=waits[0]<=11

def test_future_schema_does_not_reuse_model_cache(tmp_path):
    s=tmp_path/"s.db";t=tmp_path/"v.db";seed(s);st=obs(t);st.upsert_observation(URL,schema_version="future-schema")
    calls=[]
    def counted(b):calls.append(1);return model(b)
    av.run_pipeline(str(s),None,str(t),downloader=lambda _:b"\xff\xd8new",model_caller=counted)
    assert calls==[1]

def test_future_schema_not_promoted_by_offline_rebuild(tmp_path):
    s=tmp_path/"s.db";t=tmp_path/"v.db";seed(s);st=obs(t);st.upsert_observation(URL,schema_version="future-schema")
    av.rebuild_derived(str(s),None,str(t))
    assert st.filter_listing_ids("rent",has_balcony=1)==[]
    assert st.get_observation(URL)["schema_version"]=="future-schema"

def test_same_link_set_keeps_ready_result(tmp_path):
    s=tmp_path/"s.db";t=tmp_path/"v.db";seed(s);run(s,t);st=ArchiveVisionStore(str(t))
    st.update_image_links("rent","r1",[(URL,"list_floor_plan")])
    assert st.filter_listing_ids("rent",has_balcony=1)==["r1"]

def test_foreign_provenance_not_stamped_as_current(tmp_path):
    s=tmp_path/"s.db";t=tmp_path/"v.db";seed(s);st=obs(t,"old-model","old-prompt")
    av.rebuild_derived(str(s),None,str(t))
    assert st.get_listing_vision("rent","r1")["model_id"] in (None,"old-model")

def test_positive_balcony_survives_other_image_error():
    good=av.derive_labels("户型图","readable",["阳台"])
    error=av.Derived("unknown","no_text",[],"error")
    pairs=[(av.ImageRef("rent","r1","list_floor_plan",URL),good),
           (av.ImageRef("rent","r1","prospect_floor_plan",URL+"bad"),error)]
    assert av.rollup_listing(pairs,crm=None).has_balcony==1
