import dataclasses, http.server, io, json, os, sqlite3, subprocess, sys, threading, urllib.request, urllib.error
from pathlib import Path
import pytest
SERVICE=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(SERVICE))
import app.application.archive_vision as av
from app.infrastructure.archive_vision_store import ArchiveVisionStore
URL="https://img.ljcdn.com/contract.jpg"
LABELS=["阳台","卧室","客厅","厨房"]

def seed(p,url=URL):
    with sqlite3.connect(p) as c:
        c.execute("CREATE TABLE listing_current(listing_id TEXT PRIMARY KEY,payload_json TEXT,content_hash TEXT,is_active INTEGER DEFAULT 1)")
        c.execute("INSERT INTO listing_current VALUES(?,?,?,1)",("r1",json.dumps({"listing_id":"r1","list":{"floor_plan_image_url":url},"detail":{}}),"unchanged"))

def model(_):
    return "户型图","readable",LABELS,{"image_kind":"户型图","readability":"readable","labels":LABELS}

def run(s,t):
    av.run_pipeline(str(s),None,str(t),downloader=lambda _:b"\xff\xd8same",model_caller=model)

def obs(t,model_id=None,prompt=None):
    st=ArchiveVisionStore(str(t))
    st.upsert_observation(URL,status="ok",image_sha256="stable-sha",image_kind="户型图",readability="readable",labels_json=json.dumps(LABELS),derived_json=json.dumps(dataclasses.asdict(av.derive_labels("户型图","readable",LABELS))),model_id=model_id or av.MODEL_ID,prompt_version=prompt or av.PROMPT_VERSION,labeled_at="original-time")
    return st

@pytest.fixture
def server():
    from PIL import Image
    out=io.BytesIO();Image.new("RGB",(16,16),"white").save(out,format="JPEG");blob=out.getvalue();calls=[]
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(("GET",self.path,None));self.send_response(200);self.send_header("Content-Type","image/jpeg");self.end_headers();self.wfile.write(blob)
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers["Content-Length"])));calls.append(("POST",self.path,body))
            self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers()
            self.wfile.write(json.dumps({"choices":[{"finish_reason":"stop","message":{"content":json.dumps(model(None)[3])}}]}).encode())
        def log_message(self,*a):pass
    srv=http.server.ThreadingHTTPServer(("127.0.0.1",0),Handler);threading.Thread(target=srv.serve_forever,daemon=True).start()
    try:yield f"http://127.0.0.1:{srv.server_port}",calls
    finally:srv.shutdown();srv.server_close()

def cli(tmp_path,server,key):
    endpoint,calls=server;s=tmp_path/"s.db";t=tmp_path/"v.db";seed(s,endpoint+"/plan.jpg")
    env=os.environ.copy();env.pop("NVIDIA_API_KEY",None);env.pop("NIM_API_KEY",None)
    if key:env["NVIDIA_API_KEY"]="offline-contract-dummy"
    env["NVIDIA_API_BASE"]=endpoint+"/v1/chat/completions";env["PYTHONDONTWRITEBYTECODE"]="1"
    p=subprocess.run([sys.executable,"-m","app.application.archive_vision","--rental-db",str(s),"--vision-db",str(t)],cwd=SERVICE,env=env,capture_output=True,text=True,timeout=15)
    return p,t,calls

def test_real_cli_uses_default_client(tmp_path,server):
    p,t,calls=cli(tmp_path,server,True)
    assert p.returncode==0,p.stderr
    bodies=[b for verb,_,b in calls if verb=="POST"];assert len(bodies)==1
    assert bodies[0]["model"]==av.MODEL_ID
    assert bodies[0]["chat_template_kwargs"]["enable_thinking"] is False
    assert ArchiveVisionStore(str(t)).get_listing_vision("rent","r1")["has_balcony"]==1

def test_cli_missing_key_fails_before_download(tmp_path,server):
    p,t,calls=cli(tmp_path,server,False)
    assert p.returncode!=0
    assert calls==[]

class Reply(io.BytesIO):
    status=200;headers={}
    def __init__(self,payload,reason="stop",final=True):
        message={"content":json.dumps(payload)} if final else {"reasoning_content":json.dumps(payload)}
        super().__init__(json.dumps({"choices":[{"finish_reason":reason,"message":message}]}).encode())

def invoke(mp,payload,reason="stop",final=True):
    mp.setenv("NVIDIA_API_KEY","offline-contract-dummy")
    mp.setattr(urllib.request,"urlopen",lambda *a,**k:Reply(payload,reason,final))
    mp.setattr(av.time,"sleep",lambda _:None)
    return av.default_call_model(b"\xff\xd8fake")

@pytest.mark.parametrize("change",[{"labels":[123]},{"confidence":0.99}])
def test_strict_types_and_keys(monkeypatch,change):
    p=model(None)[3];p.update(change);assert invoke(monkeypatch,p) is None

def test_finish_reason_length_rejected(monkeypatch):
    assert invoke(monkeypatch,model(None)[3],"length") is None

def test_reasoning_not_used(monkeypatch):
    assert invoke(monkeypatch,model(None)[3],final=False) is None

def test_fractional_retry_after(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY","offline-contract-dummy");waits=[];calls=[]
    def request(*a,**k):
        calls.append(1)
        if len(calls)==1:raise urllib.error.HTTPError(URL,429,"limit",{"Retry-After":"0.25"},None)
        return Reply(model(None)[3])
    monkeypatch.setattr(urllib.request,"urlopen",request);monkeypatch.setattr(av.time,"sleep",waits.append)
    assert av.default_call_model(b"\xff\xd8fake") is not None
    assert waits[0]==pytest.approx(.25)

def test_links_invalidation_is_atomic(tmp_path):
    s=tmp_path/"s.db";t=tmp_path/"v.db";seed(s);run(s,t)
    st=ArchiveVisionStore(str(t));st.update_image_links("rent","r1",[("https://img.ljcdn.com/new.jpg","list_floor_plan")])
    assert st.filter_listing_ids("rent",has_balcony=1)==[]

@pytest.mark.parametrize("mutation",["inactive","delete"])
def test_rebuild_reconciles_inactive(tmp_path,mutation):
    s=tmp_path/"s.db";t=tmp_path/"v.db";seed(s);run(s,t)
    with sqlite3.connect(s) as c:c.execute("UPDATE listing_current SET is_active=0" if mutation=="inactive" else "DELETE FROM listing_current")
    av.rebuild_derived(str(s),None,str(t))
    assert ArchiveVisionStore(str(t)).filter_listing_ids("rent",has_balcony=1)==[]

def test_rebuild_preflight_precedes_writes(tmp_path):
    bad=tmp_path/"bad.db";t=tmp_path/"v.db";sqlite3.connect(bad).close()
    st=obs(t);st.upsert_observation(URL,derived_json='{"old":true}');before=st.get_observation(URL)
    with pytest.raises((RuntimeError,sqlite3.DatabaseError)):av.rebuild_derived(str(bad),None,str(t))
    assert st.get_observation(URL)==before

def test_rebuild_foreign_cache_stays_stale(tmp_path):
    s=tmp_path/"s.db";t=tmp_path/"v.db";seed(s);st=obs(t,"old-model","old-prompt")
    av.rebuild_derived(str(s),None,str(t))
    assert st.get_observation(URL)["model_id"]=="old-model"
    assert st.filter_listing_ids("rent",has_balcony=1)==[]
    assert st.get_listing_vision("rent","r1")["vision_status"]=="stale"

def test_unknown_model_cache_not_reused(tmp_path):
    s=tmp_path/"s.db";t=tmp_path/"v.db";seed(s);st=obs(t);st.upsert_observation(URL,model_id=None);calls=[]
    def counted(b):calls.append(1);return model(b)
    av.run_pipeline(str(s),None,str(t),downloader=lambda _:b"\xff\xd8same",model_caller=counted)
    assert calls==[1]

@pytest.mark.parametrize("kind,labels,field",[("户型图",["车位"],"is_parking"),("实拍",[],"is_photo_not_plan")])
def test_other_error_keeps_special_positive(kind,labels,field):
    pairs=[(av.ImageRef("rent","r1","list_floor_plan",URL),av.derive_labels(kind,"readable",labels)),
           (av.ImageRef("rent","r1","prospect_floor_plan",URL+"bad"),av.Derived("unknown","no_text",[],"error"))]
    assert getattr(av.rollup_listing(pairs,crm=None),field)==1

def test_schema_and_rule_versions_persist(tmp_path):
    s=tmp_path/"s.db";t=tmp_path/"v.db";seed(s);run(s,t)
    o=ArchiveVisionStore(str(t)).get_observation(URL)
    assert o.get("schema_version")
    assert o.get("rules_version")

def test_rebuild_network_free_idempotent(tmp_path,monkeypatch):
    s=tmp_path/"s.db";t=tmp_path/"v.db";seed(s);st=obs(t);before=st.get_observation(URL)
    def forbidden(*a,**k):pytest.fail("Offline rebuild tried network")
    monkeypatch.setattr(av,"default_download_image",forbidden);monkeypatch.setattr(av,"default_call_model",forbidden)
    monkeypatch.delenv("NVIDIA_API_KEY",raising=False);monkeypatch.delenv("NIM_API_KEY",raising=False)
    av.rebuild_derived(str(s),None,str(t));first=st.get_listing_vision("rent","r1")
    av.rebuild_derived(str(s),None,str(t));second=st.get_listing_vision("rent","r1")
    ignore={"labeled_at","derived_at","updated_at","rebuilt_at"}
    assert {k:v for k,v in first.items() if k not in ignore}=={k:v for k,v in second.items() if k not in ignore}
    assert second["has_balcony"]==1
    assert st.get_observation(URL)["labels_json"]==before["labels_json"]
    assert st.get_observation(URL)["image_sha256"]==before["image_sha256"]
