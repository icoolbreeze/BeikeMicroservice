import json,pathlib,sqlite3,sys
import pytest
SERVICE=pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0,str(SERVICE))
import app.application.archive_vision as av
from app.infrastructure.archive_vision_store import ArchiveVisionStore

def source(path,prefix,rows=5):
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE listing_current(listing_id TEXT PRIMARY KEY,payload_json TEXT,content_hash TEXT,is_active INTEGER,last_seen_at TEXT,detail_captured_at TEXT)")
        for i in range(rows):
            lid=f"{prefix}{i:06d}"
            p={"listing_id":lid,"list":{"floor_plan_image_url":f"https://img.ljcdn.com/{lid}.jpg"},"detail":{}}
            c.execute("INSERT INTO listing_current VALUES(?,?,?,1,?,?)",(lid,json.dumps(p),"unchanged","2026-11-10T00:00:00+00:00","2026-11-10T00:00:00+00:00"))

def model(_):
    labels=["阳台","卧室","客厅","厨房"]
    return "户型图","readable",labels,{"image_kind":"户型图","readability":"readable","labels":labels}

def runner(rent,sale,target,downloads,batch_size=2):
    def download(url):
        downloads.append(url)
        return b"\xff\xd8"+url.encode()
    return av.ArchiveVisionRunner(str(rent) if rent else None,str(sale) if sale else None,str(target),batch_size=batch_size,poll_interval=30,downloader=download,model_caller=model)

def test_scan_does_not_load_full_source_payloads(tmp_path,monkeypatch):
    s=tmp_path/"bounded_source.sqlite3";v=tmp_path/"v.sqlite3";source(s,"r",2500)
    actual=sqlite3.connect;payload_rows=[]
    class Cursor:
        def __init__(self,c,sql=""):self.c=c;self.sql=sql
        def __getattr__(self,n):return getattr(self.c,n)
        def execute(self,sql,*args):self.sql=sql;self.c.execute(sql,*args);return self
        def executemany(self,*a):return self.c.executemany(*a)
        def _record(self,rows):
            if "payload_json" in self.sql.lower() and "listing_current" in self.sql.lower():payload_rows.extend([1]*len(rows))
            return rows
        def fetchall(self):return self._record(self.c.fetchall())
        def fetchmany(self,*a):return self._record(self.c.fetchmany(*a))
        def fetchone(self):
            row=self.c.fetchone()
            if row is not None:self._record([row])
            return row
        def __iter__(self):return self
        def __next__(self):
            row=next(self.c);self._record([row]);return row
    class Connection:
        def __init__(self,c):object.__setattr__(self,"c",c)
        def __getattr__(self,n):return getattr(self.c,n)
        def __setattr__(self,n,v):
            if n=="c":object.__setattr__(self,n,v)
            else:setattr(self.c,n,v)
        def __enter__(self):self.c.__enter__();return self
        def __exit__(self,*a):return self.c.__exit__(*a)
        def execute(self,sql,*a):return Cursor(self.c.execute(sql,*a),sql)
        def cursor(self,*a,**k):return Cursor(self.c.cursor(*a,**k))
    def connect(database,*args,**kwargs):
        c=actual(database,*args,**kwargs)
        return Connection(c) if "bounded_source.sqlite3" in str(database) else c
    monkeypatch.setattr(sqlite3,"connect",connect)
    calls=[];runner(s,None,v,calls).run_once()
    assert len(calls)<=2
    assert len(payload_rows)<=1100, f"Read {len(payload_rows)} full payload rows for a two-image budget"

def test_checkpoint_advances_without_invalidating_previous_page(tmp_path):
    s=tmp_path/"s.db";v=tmp_path/"v.db";source(s,"r",5)
    calls=[];r=runner(s,None,v,calls,1);r.run_once();first=list(calls)
    assert len(first)==1
    r.run_once()
    assert len(calls)==2 and calls[0]!=calls[1]
    st=ArchiveVisionStore(str(v))
    assert "r000000" in st.filter_listing_ids("rent",has_balcony=1)

def test_two_businesses_make_progress_with_small_budget(tmp_path):
    rent=tmp_path/"r.db";sale=tmp_path/"s.db";v=tmp_path/"v.db";source(rent,"rent",3);source(sale,"sale",3)
    calls=[];r=runner(rent,sale,v,calls,1)
    for _ in range(4):r.run_once()
    assert any("/rent" in u for u in calls)
    assert any("/sale" in u for u in calls)
    assert len(calls)<=4

def test_url_changed_during_inference_cannot_publish_old_ready(tmp_path):
    s=tmp_path/"s.db";v=tmp_path/"v.db";source(s,"r",1)
    def changed(blob):
        with sqlite3.connect(s) as c:
            p={"listing_id":"r000000","list":{"floor_plan_image_url":"https://img.ljcdn.com/replaced.jpg"},"detail":{}}
            c.execute("UPDATE listing_current SET payload_json=?",(json.dumps(p),))
        return model(blob)
    r=av.ArchiveVisionRunner(str(s),None,str(v),batch_size=1,downloader=lambda _:b"\xff\xd8same",model_caller=changed)
    r.run_once()
    st=ArchiveVisionStore(str(v))
    lv=st.get_listing_vision("rent","r000000")
    assert lv is not None and lv["vision_status"] in ("stale","inactive")
    assert st.filter_listing_ids("rent",has_balcony=1)==[]
