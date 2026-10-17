from __future__ import annotations
from contextlib import asynccontextmanager
from collections import deque
from pathlib import Path
import csv, io, json, logging, os, platform, secrets, sqlite3, threading, time, uuid
from datetime import datetime, timezone
import numpy as np
import psutil
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from engine import Detector, FEATURES, IncidentTracker, SCENARIOS, evaluate, normal_workload, replay

ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('ZWATCH_DATA_DIR', ROOT/'data'))
DATA.mkdir(exist_ok=True,parents=True)
KEYFILE=Path(os.environ.get('ZWATCH_KEY_FILE', ROOT/'.access-token'))
if not KEYFILE.exists():
    fd=os.open(KEYFILE,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f: f.write(secrets.token_urlsafe(36))
API_KEY=KEYFILE.read_text().strip()
if len(API_KEY)<24: raise RuntimeError('Access token must contain at least 24 characters')
log=logging.getLogger('zwatch')
lock=threading.RLock()
stop=threading.Event()
db=sqlite3.connect(DATA/'zwatch.sqlite',check_same_thread=False)
db.execute('PRAGMA journal_mode=WAL')
db.execute('CREATE TABLE IF NOT EXISTS samples(id INTEGER PRIMARY KEY, source TEXT, timestamp TEXT, payload TEXT)')
db.execute('CREATE TABLE IF NOT EXISTS audits(id INTEGER PRIMARY KEY, timestamp TEXT, action TEXT, payload TEXT)')
db.execute('CREATE TABLE IF NOT EXISTS incidents(source TEXT, run_id TEXT, incident_id INTEGER, payload TEXT, PRIMARY KEY(source,run_id,incident_id))')
db.commit()
live=deque(maxlen=240)
demo=deque(maxlen=240)
live_baseline=[]
live_detector=None
live_tracker=IncidentTracker()
demo_tracker=IncidentTracker()
demo_status={'running':False,'scenario':'compound','step':0,'total':180,'run_id':None}
live_state={'ready':False,'samples':0,'baseline_samples':0,'last_error':None,'interval_s':1}
demo_detector=Detector().fit(normal_workload(800,17),normal_workload(256,31),'synthetic normal workload; replay use only')
evidence=None
started=time.time()

# Restore local evidence and the live baseline without loading executable pickle files.
for (payload,) in reversed(db.execute("SELECT payload FROM samples WHERE source='live' ORDER BY id DESC LIMIT 240").fetchall()):
    live.append(json.loads(payload))
if live: live_state['samples']=int(live[-1]['step'])+1
for (payload,) in db.execute("SELECT payload FROM incidents WHERE source='live' AND run_id='live' ORDER BY incident_id").fetchall():
    live_tracker.incidents.append(json.loads(payload))
live_tracker.active=next((i for i in reversed(live_tracker.incidents) if i['status']=='open'),None)
baseline_path=DATA/'live-baseline.json'
if baseline_path.exists():
    live_baseline=json.loads(baseline_path.read_text())[:120]
    live_state['baseline_samples']=len(live_baseline)
    if len(live_baseline)==120:
        live_detector=Detector().fit(live_baseline[:80],live_baseline[80:120],'120 real LinuxONE VM samples; short prototype baseline')
        live_state['ready']=True
last_replay=db.execute("SELECT payload FROM audits WHERE action='replay_started' ORDER BY id DESC LIMIT 1").fetchone()
if last_replay:
    recorded=json.loads(last_replay[0])
    saved_run=recorded['run_id']
    demo_status.update(scenario=recorded['scenario'],run_id=saved_run)
    for (payload,) in reversed(db.execute("SELECT payload FROM samples WHERE source='simulation' ORDER BY id DESC LIMIT 240").fetchall()):
        item=json.loads(payload)
        if item.get('run_id')==saved_run: demo.append(item)
    if demo: demo_status['step']=demo[-1]['step']+1
    for (payload,) in db.execute("SELECT payload FROM incidents WHERE source='simulation' AND run_id=? ORDER BY incident_id",(saved_run,)).fetchall():
        demo_tracker.incidents.append(json.loads(payload))
    # An interrupted replay does not resume automatically after a service restart.
    demo_tracker.active=next((i for i in reversed(demo_tracker.incidents) if i['status']=='open'),None)

def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds')

def audit(action,payload):
    with lock:
        db.execute('INSERT INTO audits(timestamp,action,payload) VALUES(?,?,?)',(now(),action,json.dumps(payload)))
        db.commit()

def persist(item,tracker,run_id):
    with lock:
        db.execute('INSERT INTO samples(source,timestamp,payload) VALUES(?,?,?)',(item['source'],item['timestamp'],json.dumps(item)))
        for incident in tracker.incidents[-5:]:
            db.execute('INSERT OR REPLACE INTO incidents VALUES(?,?,?,?)',(item['source'],run_id,incident['id'],json.dumps(incident)))
        # A bounded local retention window; the most recent 20,000 samples remain.
        if item.get('step',0)%120==0:
            db.execute('DELETE FROM samples WHERE id < (SELECT COALESCE(MAX(id),0)-20000 FROM samples)')
        db.commit()

def collector():
    global live_detector
    prev=psutil.net_io_counters()
    previous=time.monotonic()
    psutil.cpu_percent(None)
    while not stop.wait(1.):
        try:
            current=time.monotonic(); net=psutil.net_io_counters()
            cpu=psutil.cpu_times_percent(None)
            row={'timestamp':now(),'source':'live','step':live_state['samples'],
                 'cpu_pct':float(psutil.cpu_percent(None)), 'memory_pct':float(psutil.virtual_memory().percent),
                 'disk_pct':float(psutil.disk_usage('/').percent),
                 'network_kbps':max(0.,(net.bytes_recv+net.bytes_sent-prev.bytes_recv-prev.bytes_sent)/1024/(current-previous)),
                 'load_per_cpu':os.getloadavg()[0]/max(1,psutil.cpu_count()),'io_wait_pct':float(getattr(cpu,'iowait',0.))}
            prev,previous=net,current
            if live_detector is None:
                live_baseline.append({k:row[k] for k in FEATURES})
                live_state['baseline_samples']=len(live_baseline)
                if len(live_baseline)%20==0:
                    temp=DATA/'live-baseline.tmp'
                    temp.write_text(json.dumps(live_baseline))
                    temp.replace(baseline_path)
                if len(live_baseline)>=120:
                    live_detector=Detector().fit(live_baseline[:80],live_baseline[80:120],'120 real LinuxONE VM samples; short prototype baseline')
                    live_state['ready']=True
                    audit('live_baseline_fitted',live_detector.info)
            if live_detector:
                row.update(live_detector.score(row))
                live_tracker.update(row)
            else:
                row.update(score=0,threshold=0,candidate=False,confirmed=False,guardrails=[],drivers=[],
                           action='Collecting live baseline',guidance='120 observations are needed before live ML scoring starts.',inference_ms=0)
            with lock:
                live.append(row)
                live_state['samples']+=1
                live_state['last_error']=None
                persist(row,live_tracker,'live')
        except Exception as exc:
            log.exception('collector_error')
            live_state['last_error']=str(exc)

def run_replay(scenario,run_id):
    try:
        rows=replay(scenario)
        for row in rows:
            if stop.is_set(): break
            item={**row,**demo_detector.score(row),'timestamp':now(),'source':'simulation','run_id':run_id}
            with lock:
                demo_tracker.update(item)
                demo.append(item)
                demo_status['step']=row['step']+1
                persist(item,demo_tracker,run_id)
            time.sleep(.16)
    except Exception:
        log.exception('replay_error')
    finally:
        with lock: demo_status['running']=False
        audit('replay_completed',{'scenario':scenario,'run_id':run_id})

def evidence_worker():
    global evidence
    try:
        measured=evaluate(demo_detector)
        measured['runtime']={'architecture':platform.machine(),'python':platform.python_version(),
                             'logical_cpus':psutil.cpu_count(),'memory_gib':round(psutil.virtual_memory().total/1024**3,2),
                             'measured_at_utc':now(),'measurement_scope':'Python inference inside constrained ZWatch LinuxONE container'}
        (DATA/'evaluation.json').write_text(json.dumps(measured,indent=2))
        with lock: evidence=measured
        audit('evaluation_completed',{'scenarios':len(measured['results'])})
    except Exception:
        log.exception('evaluation_error')

@asynccontextmanager
async def lifespan(app):
    stop.clear()
    threading.Thread(target=collector,daemon=True,name='telemetry').start()
    threading.Thread(target=evidence_worker,daemon=True,name='evidence').start()
    audit('service_started',{'architecture':platform.machine()})
    yield
    stop.set()

app=FastAPI(title='ZWatch | Team V',version='1.0.0',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)

@app.middleware('http')
async def headers(request:Request,call_next):
    if request.method=='POST':
        origin=request.headers.get('origin')
        if origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
            return Response('Origin rejected',status_code=403)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
    return response

def authorized(authorization:str|None=Header(default=None)):
    given=(authorization or '').removeprefix('Bearer ')
    if not secrets.compare_digest(given.encode(),API_KEY.encode()): raise HTTPException(status_code=401,detail='Access token required')

@app.get('/')
def home(): return FileResponse(ROOT/'static'/'index.html')

@app.get('/healthz')
def health(): return {'status':'ok','service':'zwatch'}

@app.get('/api/state',dependencies=[Depends(authorized)])
def state():
    with lock:
        return {'live':list(live),'simulation':list(demo), 'live_state':live_state.copy(),
                'replay':demo_status.copy(), 'incidents':{'live':[i.copy() for i in live_tracker.incidents[-12:]],
                'simulation':[i.copy() for i in demo_tracker.incidents[-12:]]},
                'model':{'live':live_detector.info if live_detector else None,'simulation':demo_detector.info},
                'evidence':evidence,'scenarios':SCENARIOS,
                'runtime':{'architecture':platform.machine(),'python':platform.python_version(),
                           'cpus':psutil.cpu_count(),'memory_gib':round(psutil.virtual_memory().total/1024**3,2),
                           'uptime_s':round(time.time()-started),'scope':'VM system counters read inside LinuxONE container; root disk is the container filesystem'}}

class ReplayRequest(BaseModel):
    scenario:str=Field(default='compound',max_length=24)

@app.post('/api/replay',dependencies=[Depends(authorized)])
def start_replay(body:ReplayRequest):
    global demo_tracker
    if body.scenario not in SCENARIOS: raise HTTPException(422,'Unknown scenario')
    with lock:
        if demo_status['running']: raise HTTPException(409,'A replay is already running')
        demo.clear()
        demo_tracker=IncidentTracker()
        run_id=uuid.uuid4().hex[:12]
        demo_status.update(running=True,scenario=body.scenario,step=0,total=180,run_id=run_id)
        audit('replay_started',{'scenario':body.scenario,'run_id':run_id})
        threading.Thread(target=run_replay,args=(body.scenario,run_id),daemon=True).start()
    return {'run_id':run_id}

@app.post('/api/incidents/{source}/{incident_id}/ack',dependencies=[Depends(authorized)])
def acknowledge(source:str,incident_id:int):
    if source not in ('live','simulation'): raise HTTPException(422,'Unknown source')
    with lock:
        tracker=live_tracker if source=='live' else demo_tracker
        target=next((i for i in tracker.incidents if i['id']==incident_id),None)
        if not target: raise HTTPException(404,'Incident not found')
        target['acknowledged']=True
        run_id='live' if source=='live' else demo_status['run_id']
        db.execute('INSERT OR REPLACE INTO incidents VALUES(?,?,?,?)',(source,run_id,incident_id,json.dumps(target)))
        db.commit()
        audit('incident_acknowledged',{'source':source,'incident_id':incident_id})
    return {'acknowledged':True}

@app.get('/api/export/{source}',dependencies=[Depends(authorized)])
def export(source:str):
    if source not in ('live','simulation'): raise HTTPException(422,'Unknown source')
    with lock: items=list(live if source=='live' else demo)
    buffer=io.StringIO()
    fields=['timestamp','source','step',*FEATURES,'score','threshold','candidate','confirmed','expected','inference_ms']
    writer=csv.DictWriter(buffer,fieldnames=fields,extrasaction='ignore')
    writer.writeheader();writer.writerows(items)
    audit('metrics_exported',{'source':source,'rows':len(items)})
    return Response(buffer.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="zwatch-{source}.csv"'})

@app.get('/api/evidence',dependencies=[Depends(authorized)])
def export_evidence():
    if evidence is None: raise HTTPException(503,'Evaluation is still running')
    return Response(json.dumps(evidence,indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="zwatch-evidence.json"'})

@app.get('/api/audit',dependencies=[Depends(authorized)])
def audit_history():
    with lock:
        rows=db.execute('SELECT timestamp,action,payload FROM audits ORDER BY id DESC LIMIT 100').fetchall()
    return [{'timestamp':t,'action':a,'details':json.loads(p)} for t,a,p in rows]
