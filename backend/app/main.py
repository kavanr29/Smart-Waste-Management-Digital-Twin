from datetime import datetime, timezone, timedelta
from pathlib import Path
import inspect
from typing import Optional
from fastapi import FastAPI, Depends, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, inspect as sa_inspect, text
from sqlalchemy.exc import IntegrityError
from .core import Base, engine, get_db, settings
from .models import *
from .seed import seed
from .services.digital_twin.engine import status, priority, overflow, fill_percentage
from .services.simulation.engine import run_simulation
from .services.computer_vision.detector import detect_image
from .services.computer_vision.model import get_model
from .services.genai.service import ask
from .services.bin_recommender import recommend_bins, is_compatible

app=FastAPI(title='WasteWise AI API',version='1.2.0')
app.add_middleware(CORSMiddleware,allow_origins=settings.cors_origins.split(','),allow_methods=['*'],allow_headers=['*'])
WINDOW_DAYS = 7
# Documented floating-point tolerance for mass balance validation: 1e-4 kg (0.1 gram)
FLOAT_TOLERANCE = 1e-4
TWIN_MODEL_VERSION = '1.0'
TWIN_FACILITIES = (
    {'id': 'collection', 'name': 'Collection', 'kind': 'collection', 'position': [0, 0, 0]},
    {'id': 'sorting', 'name': 'Sorting', 'kind': 'sorting', 'position': [3.7, 0, -1]},
    {'id': 'recycling', 'name': 'Recycling', 'kind': 'recycling', 'position': [6, 0, -3]},
    {'id': 'recovery', 'name': 'Recovery', 'kind': 'recovery', 'position': [4, 0, 3]},
    {'id': 'disposal', 'name': 'Disposal', 'kind': 'disposal', 'position': [-3, 0, 4]},
)
def migrate_schema():
    # Safe, additive migration: legacy WasteEvent rows receive NULL keys.
    if engine.dialect.name == 'sqlite':
        columns = {item['name'] for item in sa_inspect(engine).get_columns('waste_events')}
        with engine.begin() as connection:
            if 'idempotency_key' not in columns:
                connection.execute(text('ALTER TABLE waste_events ADD COLUMN idempotency_key VARCHAR(128)'))
            connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_waste_events_idempotency_key ON waste_events (idempotency_key)'))

@app.on_event('startup')
def startup():
    Base.metadata.create_all(engine); migrate_schema(); db=next(get_db()); seed(db); db.close()
class SimRequest(BaseModel): collection_frequency:float=Field(ge=.5,le=7); segregation_improvement:float=Field(ge=0,le=50); recovery_improvement:float=Field(default=0,ge=0,le=30)
class ChatRequest(BaseModel): message:str=Field(min_length=1,max_length=1000)
class EventRequest(BaseModel): object_name:str=Field(min_length=1); category:str=Field(min_length=1); confidence:float=Field(ge=0,le=1); weight_kg:float=Field(gt=0,le=100); bin_id:int; source:str='CV / DEMO'; idempotency_key:Optional[str]=Field(default=None,min_length=1,max_length=128)
class RecommendationRequest(BaseModel): category:str=Field(min_length=1); weight_kg:float=Field(gt=0,le=100); current_zone:str='Central Plaza'
class BinRequest(BaseModel): location_id:int; name:str=Field(min_length=1); capacity_kg:float=Field(gt=0,le=10000); target_stream:str='recyclable'
class CollectionRequest(BaseModel): bin_id:int; collected_by:str=Field(min_length=1); notes:Optional[str]=None
class SortingRequest(BaseModel): collection_id:Optional[int]=None; category:str='mixed'; input_weight_kg:float=Field(gt=0); sorted_weight_kg:float=Field(ge=0); rejected_weight_kg:Optional[float]=None
class RecyclingRequest(BaseModel): category:str; input_weight_kg:float=Field(gt=0); recovered_weight_kg:float=Field(ge=0); destination:Optional[str]=None; contamination:float=Field(default=0,ge=0)
RECYCLABLE={'Plastic','Paper','Glass','Metal','Cardboard','E-waste'}
def window():
    end=datetime.now(timezone.utc).replace(tzinfo=None); return end-timedelta(days=WINDOW_DAYS),end
def in_window(q):
    start,end=window(); return q.filter(WasteEvent.created_at>=start,WasteEvent.created_at<=end)
def bin_composition(db, bin_id: int, since=None):
    if since is None:
        latest_col = (
            db.query(Collection)
            .filter(Collection.bin_id == bin_id, Collection.status == 'COMPLETED')
            .order_by(Collection.created_at.desc(), Collection.id.desc())
            .first()
        )
        if latest_col:
            since = to_naive_utc(latest_col.created_at)
    else:
        since = to_naive_utc(since)
    if since:
        rows = db.query(WasteEvent.category, WasteEvent.weight_kg).filter(WasteEvent.bin_id == bin_id, WasteEvent.created_at > since).all()
    else:
        rows = db.query(WasteEvent.category, WasteEvent.weight_kg).filter(WasteEvent.bin_id == bin_id).all()
    total = sum(w for _, w in rows)
    return {c: round(sum(w for x, w in rows if x == c) / total * 100, 1) for c in sorted({x for x, _ in rows})} if total else {}

def target_mismatch(stream, category):
    return not is_compatible(stream, category)

def current_bin_fill(db, b, latest_col=None):
    if latest_col is None:
        latest_col = (
            db.query(Collection)
            .filter(Collection.bin_id == b.id, Collection.status == 'COMPLETED')
            .order_by(Collection.created_at.desc(), Collection.id.desc())
            .first()
        )
    if latest_col is not None:
        col_dt = to_naive_utc(latest_col.created_at)
        total = db.query(func.sum(WasteEvent.weight_kg)).filter(
            WasteEvent.bin_id == b.id,
            WasteEvent.created_at > col_dt
        ).scalar()
        fill = float(total or 0.0)
    else:
        total = db.query(func.sum(WasteEvent.weight_kg)).filter(
            WasteEvent.bin_id == b.id
        ).scalar()
        fill = float(total) if total is not None and total > 0 else float(b.fill_kg or 0.0)
    return min(float(b.capacity_kg), max(0.0, fill))

def current_contamination(db, b, since=None):
    fill = current_bin_fill(db, b)
    if fill <= 0.0:
        return 0.0
    if since is None:
        latest_col = (
            db.query(Collection)
            .filter(Collection.bin_id == b.id, Collection.status == 'COMPLETED')
            .order_by(Collection.created_at.desc(), Collection.id.desc())
            .first()
        )
        if latest_col:
            since = to_naive_utc(latest_col.created_at)
    else:
        since = to_naive_utc(since)
    if since:
        rows = db.query(WasteEvent.category, WasteEvent.weight_kg).filter(WasteEvent.bin_id == b.id, WasteEvent.created_at > since).all()
    else:
        rows = db.query(WasteEvent.category, WasteEvent.weight_kg).filter(WasteEvent.bin_id == b.id).all()
    total = sum(w for _, w in rows)
    if not total:
        return 0.0
    bad = sum(w for c, w in rows if target_mismatch(b.target_stream, c))
    return round(bad / total * 100.0, 1)

def accumulation(db, b, latest_col=None, now_dt=None):
    if now_dt is None:
        now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        now_dt = to_naive_utc(now_dt)
    if latest_col is None:
        latest_col = (
            db.query(Collection)
            .filter(Collection.bin_id == b.id, Collection.status == 'COMPLETED')
            .order_by(Collection.created_at.desc(), Collection.id.desc())
            .first()
        )
    fill = current_bin_fill(db, b, latest_col)
    if fill <= 0.0:
        return None

    seven_day_cutoff = now_dt - timedelta(days=WINDOW_DAYS)

    if latest_col is not None:
        col_dt = to_naive_utc(latest_col.created_at)
        if col_dt > seven_day_cutoff:
            # Collection occurred within the last 7 days:
            # Start at the latest completed collection
            elapsed_seconds = (now_dt - col_dt).total_seconds()
            elapsed_hours = elapsed_seconds / 3600.0
            if elapsed_hours < 1.0:
                # Insufficient measurement period (< 1 hour)
                return None
            elapsed_days = elapsed_hours / 24.0
            window_weight = db.query(func.sum(WasteEvent.weight_kg)).filter(
                WasteEvent.bin_id == b.id,
                WasteEvent.created_at > col_dt,
                WasteEvent.created_at <= now_dt
            ).scalar()
        else:
            # Collection occurred more than 7 days ago:
            # Start at the seven-day cutoff
            elapsed_days = float(WINDOW_DAYS)
            window_weight = db.query(func.sum(WasteEvent.weight_kg)).filter(
                WasteEvent.bin_id == b.id,
                WasteEvent.created_at >= seven_day_cutoff,
                WasteEvent.created_at <= now_dt
            ).scalar()
    else:
        # Bin has never had a completed collection:
        # Start at the seven-day cutoff
        earliest_event = (
            db.query(WasteEvent.created_at)
            .filter(WasteEvent.bin_id == b.id)
            .order_by(WasteEvent.created_at.asc())
            .first()
        )
        if not earliest_event:
            return None
        earliest_dt = to_naive_utc(earliest_event[0])
        effective_start = max(seven_day_cutoff, earliest_dt)
        elapsed_seconds = (now_dt - effective_start).total_seconds()
        if elapsed_seconds / 3600.0 < 1.0:
            # Insufficient measurement period (< 1 hour)
            return None
        elapsed_days = elapsed_seconds / 86400.0
        window_weight = db.query(func.sum(WasteEvent.weight_kg)).filter(
            WasteEvent.bin_id == b.id,
            WasteEvent.created_at >= seven_day_cutoff,
            WasteEvent.created_at <= now_dt
        ).scalar()

    if not window_weight or float(window_weight) <= 0.0:
        return None
    if elapsed_days <= 0.0:
        return None
    return round(float(window_weight) / elapsed_days, 2)

def recycling_stats(db):
    rows = db.query(RecyclingEvent).filter(RecyclingEvent.created_at >= window()[0]).all()
    i = sum(r.input_kg for r in rows)
    rec = sum(r.recovered_kg for r in rows)
    rej = sum(r.rejected_kg for r in rows)
    return {
        'events': len(rows),
        'recyclable_input_kg': round(i, 1),
        'recovered_kg': round(rec, 1),
        'rejected_kg': round(rej, 1),
        'contamination_rate': round(sum(r.contamination for r in rows) / len(rows), 1) if rows else None,
        'recycling_efficiency': round(rec / i * 100, 1) if i else None,
    }

def collection_frequency(db):
    return round(db.query(Collection).filter(Collection.created_at >= window()[0]).count() / WINDOW_DAYS, 2)

def twin(db):
    result = []
    now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    for b in db.query(Bin).order_by(Bin.id).all():
        latest_col = (
            db.query(Collection)
            .filter(Collection.bin_id == b.id, Collection.status == 'COMPLETED')
            .order_by(Collection.created_at.desc(), Collection.id.desc())
            .first()
        )
        last_dt = to_naive_utc(latest_col.created_at if latest_col else b.last_collection)

        # Always synchronize current fill from persisted collections and waste events
        b.fill_kg = current_bin_fill(db, b, latest_col)

        if float(b.fill_kg or 0.0) <= 0.0:
            b.fill_kg = 0.0
            b.status = 'EMPTY'
            b.contamination = 0.0
            b.accumulation_rate = None
            comp = {}
        else:
            b.contamination = current_contamination(db, b, since=last_dt if latest_col else None)
            comp = bin_composition(db, b.id, since=last_dt if latest_col else None)
            b.accumulation_rate = accumulation(db, b, latest_col=latest_col, now_dt=now_dt)

        fill_pct = fill_percentage(b)
        b.status = status(fill_pct)
        o = overflow(b)
        p = priority(b, overflow_forecast=o)

        since_hours = round((now_dt - last_dt).total_seconds() / 3600.0, 1) if last_dt else None
        is_req = p['is_required'] and (b.fill_kg > 0)

        result.append({
            'id': b.id,
            'code': b.code,
            'name': b.name,
            'zone': b.location.zone if b.location else 'Unassigned',
            'location': b.location.name if b.location else 'Unassigned',
            'target_stream': b.target_stream,
            'fill_pct': round(fill_pct, 1),
            'fill_kg': round(b.fill_kg, 3),
            'capacity_kg': round(b.capacity_kg, 3),
            'status': b.status,
            'accumulation_rate': b.accumulation_rate,
            'contamination': b.contamination,
            'composition': comp,
            'collection_status': 'REQUIRED' if is_req else 'CURRENT',
            'last_collection': last_dt.isoformat() if last_dt else None,
            'hours_since_collection': since_hours,
            'overflow_hours': o['hours'],
            'overflow_risk': o['risk'],
            'overflow_status': o['label'],
            'priority_score': p['score'],
            'priority_label': p['label'],
            'priority_reasons': p['reasons'],
        })
    return result

def context(db):
    bins = twin(db)
    events = in_window(db.query(WasteEvent)).all()
    total = sum(event.weight_kg for event in events)
    by_category = {}
    by_location = {}
    daily = {}
    for event in events:
        by_category[event.category] = round(by_category.get(event.category, 0) + event.weight_kg, 1)
        location = event.bin.location.name if event.bin and event.bin.location else 'Unassigned'
        by_location[location] = round(by_location.get(location, 0) + event.weight_kg, 1)
        day = event.created_at.strftime('%a') if event.created_at else 'Unknown'
        daily[day] = round(daily.get(day, 0) + event.weight_kg, 1)

    start, end = window()
    collections = db.query(Collection).filter(Collection.created_at >= start, Collection.created_at <= end).all()
    sorting_events = db.query(SortingEvent).filter(SortingEvent.created_at >= start, SortingEvent.created_at <= end).all()
    collection_weight = sum(item.weight_kg for item in collections)
    collection_stats = {
        'collection_events': len(collections),
        'collected_weight_kg': round(collection_weight, 1),
        'frequency_per_day': round(len(collections) / WINDOW_DAYS, 2),
    }
    waste_stats = {
        'total_waste_kg': round(total, 1),
        'recyclable_waste_kg': round(sum(event.weight_kg for event in events if event.category in RECYCLABLE), 1),
        'by_category': by_category,
        'by_location': by_location,
        'daily_waste': daily,
    }
    recycling = recycling_stats(db)
    sorting = {
        'events': len(sorting_events),
        'input_kg': round(sum(item.input_kg for item in sorting_events), 1),
        'sorted_kg': round(sum(item.sorted_kg for item in sorting_events), 1),
        'rejected_kg': round(sum(item.rejected_kg for item in sorting_events), 1),
    }
    return {
        'window_start': start.isoformat(),
        'window_end': end.isoformat(),
        'bins': bins,
        'waste_statistics': waste_stats,
        'recycling_statistics': recycling,
        'collection_statistics': collection_stats,
        'sorting_statistics': sorting,
        'contamination': {'rate': round(sum(event.weight_kg for event in events if event.bin and target_mismatch(event.bin.target_stream, event.category)) / total * 100, 1) if total else None},
        'priority_bins': [item for item in sorted(bins, key=lambda row: row['priority_score'], reverse=True) if item['collection_status'] == 'REQUIRED'],
        'affected_zones': sorted({item['zone'] for item in bins if (item['contamination'] > 0 or item['collection_status'] == 'REQUIRED') and item['zone'] != 'Unassigned'}),
        'collection_efficiency_pct': round(collection_weight / total * 100, 1) if total else None,
        'overflow_predictions': [{'code': item['code'], 'hours': item['overflow_hours'], 'risk': item['overflow_risk']} for item in bins],
    }

def twin_snapshot(db):
    """Return the canonical, render-ready state consumed by a digital-twin client."""
    start, end = window()
    c = context(db)
    bins = c['bins']
    locations = {location.id: location for location in db.query(Location).all()}
    bin_rows = {bin_row.id: bin_row for bin_row in db.query(Bin).all()}
    bin_assets = []
    for item in bins:
        source = bin_rows.get(item['id'])
        location_id = source.location_id if source else None
        location = locations.get(location_id)
        bin_assets.append({
            'id': f"bin:{item['id']}",
            'asset_type': 'bin',
            'entity_id': item['id'],
            'code': item['code'],
            'name': item['name'],
            'zone': item['zone'],
            'location': item['location'],
            'position': [0, 0, 0],
            'state': item,
            'data_source': 'persisted waste and collection events',
            'location_id': location.id if location else None,
        })

    stages = ['detection', 'segregation', 'collection', 'sorting', 'recycling', 'recovery', 'disposal']
    flow_rows = db.query(
        WasteFlowEvent.stage,
        func.sum(WasteFlowEvent.quantity_kg),
        func.count(WasteFlowEvent.id),
    ).filter(
        WasteFlowEvent.created_at >= start,
        WasteFlowEvent.created_at <= end,
    ).group_by(WasteFlowEvent.stage).all()
    flow_by_stage = {
        stage: {'quantity_kg': round(float(quantity or 0), 3), 'event_count': count}
        for stage, quantity, count in flow_rows
    }
    flow = [
        {
            'stage': stage,
            'quantity_kg': flow_by_stage.get(stage, {}).get('quantity_kg', 0.0),
            'event_count': flow_by_stage.get(stage, {}).get('event_count', 0),
            'available': stage in flow_by_stage,
        }
        for stage in stages
    ]
    total_events = db.query(WasteEvent).filter(
        WasteEvent.created_at >= start,
        WasteEvent.created_at <= end,
    ).count()
    measured_bins = sum(1 for item in bins if item['accumulation_rate'] is not None)

    return {
        'model': {'name': 'WasteWise operational twin', 'version': TWIN_MODEL_VERSION},
        'generated_at': end.isoformat(),
        'window_start': start.isoformat(),
        'window_end': end.isoformat(),
        'assets': {
            'facilities': list(TWIN_FACILITIES),
            'bins': bin_assets,
        },
        'metrics': {
            'active_bins': len(bins),
            'collection_required': sum(1 for item in bins if item['collection_status'] == 'REQUIRED'),
            'overflow_risks': sum(
                1 for item in bins
                if item['overflow_risk'] is not None and item['overflow_risk'] >= 50 and item['fill_pct'] > 0
            ),
            'total_waste_kg': c['waste_statistics']['total_waste_kg'],
            'collection_efficiency_pct': c['collection_efficiency_pct'],
        },
        'flow': flow,
        'data_quality': {
            'source': 'SQLite event store',
            'waste_events': total_events,
            'bins_with_measured_accumulation_rate': measured_bins,
            'estimated_fields': ['fill_pct', 'overflow_hours', 'overflow_risk', 'priority_score'],
            'limitations': [
                'Facility positions are configured visualization coordinates, not GPS coordinates.',
                'A missing accumulation rate means there is insufficient history for overflow forecasting.',
            ],
        },
    }

@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'WasteWise AI', 'demo_mode': not bool(settings.genai_api_key)}

@app.post('/api/v1/demo/seed')
def reseed(db: Session = Depends(get_db)):
    seed(db)
    return {'message': 'Existing data preserved; idempotent demo initialization is ready', 'bin_count': db.query(Bin).count(), 'source': 'persisted software-generated events'}

@app.get('/api/v1/dashboard')
def dashboard(db: Session = Depends(get_db)):
    c = context(db)
    bs = c['bins']
    s = c['waste_statistics']
    r = c['recycling_statistics']
    return {
        'window_start': c['window_start'],
        'window_end': c['window_end'],
        'kpis': {
            'total_waste_kg': s['total_waste_kg'],
            'recyclable_waste_kg': s['recyclable_waste_kg'],
            'recycling_efficiency': r['recycling_efficiency'],
            'active_bins': len(bs),
            'collection_required': sum(1 for x in bs if x['collection_status'] == 'REQUIRED'),
            'overflow_risks': sum(1 for x in bs if (x['overflow_risk'] is not None and x['overflow_risk'] >= 50 and x['fill_pct'] > 0)),
            'contamination_rate': c['contamination']['rate'],
            'collection_efficiency': c['collection_efficiency_pct'],
        },
        'composition': s['by_category'],
        'by_location': s['by_location'],
        'sorting_statistics': c['sorting_statistics'],
        'collection_statistics': c['collection_statistics'],
        'priority_bins': c['priority_bins'],
        'affected_zones': c['affected_zones'],
        'bins': bs,
        'recent_waste_events': s['total_waste_kg'],
        'recent_collection_events': c['collection_statistics']['collection_events'],
        'alerts': [
            {
                'severity': 'critical' if (b['overflow_risk'] is not None and b['overflow_risk'] >= 50) or b['priority_label'] == 'CRITICAL' else 'warning',
                'title': f"{b['code']} collection required",
                'detail': f"{b['fill_pct']:.0f}% full ({b['fill_kg']:.1f} kg) · {b['overflow_hours'] if b['overflow_hours'] is not None else 'No imminent'}h predicted overflow",
            }
            for b in bs if b['collection_status'] == 'REQUIRED'
        ],
        'source': 'DATABASE-DERIVED · LAST 7 DAYS · event provenance retained',
    }
@app.get('/api/v1/bins')
def bins(db:Session=Depends(get_db)):return twin(db)
@app.post('/api/v1/bins')
def create_bin(payload:BinRequest,db:Session=Depends(get_db)):
    loc=db.get(Location,payload.location_id)
    if not loc:raise HTTPException(404,'Location not found')
    b=Bin(code=f'BIN-{(db.query(Bin).count()+1):02d}',name=payload.name,capacity_kg=payload.capacity_kg,target_stream=payload.target_stream,location_id=loc.id,status='EMPTY');db.add(b);db.commit();db.refresh(b);return next(x for x in twin(db) if x['id']==b.id)
@app.post('/api/v1/bins/recommend')
def recommend_existing_bins(payload:RecommendationRequest, db:Session=Depends(get_db)):
    twin(db)
    candidates=recommend_bins(db.query(Bin).all(), payload.category, payload.weight_kg, payload.current_zone)
    return {'category':payload.category,'weight_kg':payload.weight_kg,'current_zone':payload.current_zone,'source':'persisted bins and state; deterministic campus-distance estimate','recommended':candidates[0] if candidates else None,'alternatives':candidates[1:6],'candidates':candidates[:6],'message':None if candidates else 'No compatible existing bin has enough available capacity.'}
@app.get('/api/v1/bins/{bin_id}')
def bin_detail(bin_id:int,db:Session=Depends(get_db)):
    if not db.get(Bin,bin_id):raise HTTPException(404,'Bin not found')
    return next(x for x in twin(db) if x['id']==bin_id)
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB

@app.post('/api/v1/waste/detect')
def detect(file:UploadFile=File(...), confidence:Optional[float]=Query(default=None,ge=0,le=1), iou:Optional[float]=Query(default=None,ge=0,le=1), image_size:Optional[int]=Query(default=None,ge=32,le=2048)):
    try:
        data = file.file.read()
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(413, 'Image file exceeds maximum permitted size (10 MB)')
        args=(data,file.filename or '',settings.model_path)
        if len(inspect.signature(detect_image).parameters)>=6: args+=(confidence if confidence is not None else settings.cv_confidence,iou if iou is not None else settings.cv_iou,image_size if image_size is not None else settings.cv_image_size)
        return detect_image(*args)
    except ValueError as e:raise HTTPException(400,str(e))
@app.get('/api/v1/cv/status')
def cv_status():
    from .services.computer_vision.model import ComputerVisionModel
    model=get_model(settings.model_path,settings.cv_confidence,settings.cv_iou,settings.cv_image_size)
    return {'mode':'LIVE CV' if model.available else 'DEMO CV MODE','model_configured':bool(settings.model_path.strip()),'model_loaded':model.available,'model_path_status':'loaded' if model.available else ('configured path unavailable' if settings.model_path.strip() else 'not configured'),'classes':model.classes,'confidence_threshold':settings.cv_confidence,'iou_threshold':settings.cv_iou,'image_size':settings.cv_image_size,'inference_ready':model.available,'model_name':Path(settings.model_path).name if settings.model_path else None,'message':'YOLO model loaded' if model.available else ('Configured YOLO model could not be loaded' if settings.model_path.strip() else 'YOLO model not configured'),'error':model.error or None}
@app.post('/api/v1/waste/events')
def event(payload:EventRequest,db:Session=Depends(get_db)):
    b=db.get(Bin,payload.bin_id)
    if not b:raise HTTPException(404,'Bin not found')

    # Idempotency validation first: duplicate retry returns original event without incrementing fill
    # Reusing key with different payload returns 409
    if payload.idempotency_key:
        existing=db.query(WasteEvent).filter(WasteEvent.idempotency_key==payload.idempotency_key).first()
        if existing:
            same=(existing.object_name==payload.object_name and existing.category==payload.category and existing.bin_id==payload.bin_id and abs(existing.weight_kg-payload.weight_kg)<1e-6 and abs(existing.confidence-payload.confidence)<1e-6)
            if not same: raise HTTPException(409,'This confirmation key was already used for a different waste event.')
            return {'id':existing.id,'duplicate':True,'message':'Already confirmed; the original waste event was returned.','bin':next(x for x in twin(db) if x['id']==existing.bin_id)}

    # Operational validation for new requests
    if (b.status or '').upper() in {'OUT_OF_SERVICE', 'MAINTENANCE', 'INACTIVE'}:
        raise HTTPException(409, 'Selected bin is currently out of service.')
    if not is_compatible(b.target_stream,payload.category):
        raise HTTPException(422,'Selected bin does not accept this waste category.')
    b.fill_kg = current_bin_fill(db, b)
    if b.fill_kg+payload.weight_kg>b.capacity_kg:
        raise HTTPException(409,'Selected bin no longer has enough capacity. Refresh recommendations.')

    values=payload.model_dump(); e=WasteEvent(**values);db.add(e)
    try:
        db.flush(); b.fill_kg=min(b.capacity_kg,b.fill_kg+payload.weight_kg)
        b.status=status(fill_percentage(b))
        db.add(WasteFlowEvent(stage='detection',quantity_kg=payload.weight_kg,source_event_id=e.id))
        db.add(WasteFlowEvent(stage='segregation',quantity_kg=payload.weight_kg,source_event_id=e.id));db.commit()
    except IntegrityError:
        db.rollback()
        if payload.idempotency_key:
            existing=db.query(WasteEvent).filter(WasteEvent.idempotency_key==payload.idempotency_key).first()
            if existing:
                same=(existing.object_name==payload.object_name and existing.category==payload.category and existing.bin_id==payload.bin_id and abs(existing.weight_kg-payload.weight_kg)<1e-6 and abs(existing.confidence-payload.confidence)<1e-6)
                if not same: raise HTTPException(409,'This confirmation key was already used for a different waste event.')
                return {'id':existing.id,'duplicate':True,'message':'Already confirmed; the original waste event was returned.','bin':next(x for x in twin(db) if x['id']==existing.bin_id)}
        raise HTTPException(409,'Database conflict while recording waste event.')
    except Exception:
        db.rollback()
        raise
    return {'id':e.id,'duplicate':False,'message':'Waste event persisted and Digital Twin updated','bin':next(x for x in twin(db) if x['id']==b.id)}
@app.post('/api/v1/collections')
def collect(payload:CollectionRequest,db:Session=Depends(get_db)):
    b=db.get(Bin,payload.bin_id)
    if not b:raise HTTPException(404,'Bin not found')
    b.fill_kg = current_bin_fill(db, b)
    if b.fill_kg<=0:raise HTTPException(400,'Cannot collect an empty bin')
    amount=b.fill_kg; c=Collection(bin_id=b.id,weight_kg=amount,collected_by=payload.collected_by,notes=payload.notes,status='COMPLETED');db.add(c);db.flush();db.add(WasteFlowEvent(stage='collection',quantity_kg=amount,source_event_id=c.id));b.fill_kg=0.0;b.status='EMPTY';b.contamination=0.0;b.last_collection=now();db.commit();return {'collection_id':c.id,'collected_weight_kg':amount,'bin':next(x for x in twin(db) if x['id']==b.id)}
@app.get('/api/v1/collections/available')
def available_collections(db:Session=Depends(get_db)):
    rows=[]
    for c in db.query(Collection).order_by(Collection.created_at.desc()).all():
        used=sum(x.input_kg for x in db.query(SortingEvent).filter(SortingEvent.collection_id==c.id).all()); available=max(0,c.weight_kg-used)
        if available>0: rows.append({'collection_id':c.id,'bin_id':c.bin_id,'collected_weight_kg':c.weight_kg,'available_kg':round(available,3),'created_at':c.created_at.isoformat()})
    return {'window':'LAST_7_DAYS','collections':rows}
@app.get('/api/v1/sorting/available')
def available_sorted(db:Session=Depends(get_db)):
    rows=[]; total_sorted=sum(x.sorted_kg for x in db.query(SortingEvent).all())
    processed=sum(x.input_kg for x in db.query(RecyclingEvent).all()); available=max(0,total_sorted-processed)
    if available>0: rows.append({'available_kg':round(available,3),'source':'persisted SortingEvent records'})
    return {'window':'LAST_7_DAYS','batches':rows}
@app.post('/api/v1/sorting')
def sort_waste(payload:SortingRequest,db:Session=Depends(get_db)):
    if payload.input_weight_kg<=0: raise HTTPException(400,'Input weight must be greater than zero')
    if payload.sorted_weight_kg<0: raise HTTPException(400,'Sorted weight cannot be negative')
    if payload.sorted_weight_kg>payload.input_weight_kg+FLOAT_TOLERANCE: raise HTTPException(400,'Sorted weight cannot exceed input weight')

    # Mass balance: input_weight_kg = sorted_weight_kg + rejected_weight_kg
    if payload.rejected_weight_kg is None:
        rejected=round(payload.input_weight_kg-payload.sorted_weight_kg,4)
        if rejected<0: raise HTTPException(400,'Sorted weight exceeds input weight')
    else:
        if payload.rejected_weight_kg<0: raise HTTPException(400,'Rejected weight cannot be negative')
        if abs(payload.input_weight_kg-(payload.sorted_weight_kg+payload.rejected_weight_kg))>FLOAT_TOLERANCE:
            raise HTTPException(400,f'Mass balance mismatch: input weight ({payload.input_weight_kg:.3f} kg) must equal sorted weight ({payload.sorted_weight_kg:.3f} kg) + rejected weight ({payload.rejected_weight_kg:.3f} kg)')
        rejected=payload.rejected_weight_kg

    if not payload.collection_id: raise HTTPException(400,'collection_id is required to sort actual collected material')
    collection=db.get(Collection,payload.collection_id)
    if not collection: raise HTTPException(404,'Collection not found')
    used=sum(x.input_kg for x in db.query(SortingEvent).filter(SortingEvent.collection_id==collection.id).all())
    available=max(0.0,collection.weight_kg-used)
    if payload.input_weight_kg>available+FLOAT_TOLERANCE: raise HTTPException(400,f'Sorting input exceeds available collected material ({available:.3f} kg)')
    s=SortingEvent(collection_id=collection.id,input_kg=payload.input_weight_kg,sorted_kg=payload.sorted_weight_kg,rejected_kg=rejected,category=payload.category); db.add(s); db.flush(); db.add(WasteFlowEvent(stage='sorting',quantity_kg=payload.sorted_weight_kg,source_event_id=s.id)); db.commit(); return {'sorting_id':s.id,'sorted_weight_kg':payload.sorted_weight_kg,'rejected_weight_kg':rejected,'available_before_kg':available}
@app.post('/api/v1/recycling')
def recycle(payload:RecyclingRequest,db:Session=Depends(get_db)):
    if payload.recovered_weight_kg>payload.input_weight_kg: raise HTTPException(400,'Recovered weight cannot exceed input weight')
    sorted_total=sum(x.sorted_kg for x in db.query(SortingEvent).all()); processed=sum(x.input_kg for x in db.query(RecyclingEvent).all()); available=max(0,sorted_total-processed)
    if payload.input_weight_kg>available: raise HTTPException(400,f'Recycling input exceeds available sorted material ({available:.3f} kg)')
    rejected=payload.input_weight_kg-payload.recovered_weight_kg; r=RecyclingEvent(category=payload.category,input_kg=payload.input_weight_kg,recovered_kg=payload.recovered_weight_kg,rejected_kg=rejected,contamination=payload.contamination,destination=payload.destination); db.add(r); db.flush(); db.add(WasteFlowEvent(stage='recycling',quantity_kg=payload.input_weight_kg,source_event_id=r.id)); db.add(WasteFlowEvent(stage='recovery',quantity_kg=payload.recovered_weight_kg,source_event_id=r.id)); db.add(WasteFlowEvent(stage='disposal',quantity_kg=rejected,source_event_id=r.id)); db.commit(); return {'recycling_id':r.id,'efficiency':round(payload.recovered_weight_kg/payload.input_weight_kg*100,1),'available_before_kg':available}
@app.get('/api/v1/digital-twin/state')
def state(db:Session=Depends(get_db)):return {'window_start':window()[0].isoformat(),'window_end':window()[1].isoformat(),'zones':[{'id':z.id,'name':z.name,'zone':z.zone} for z in db.query(Location).all()],'bins':twin(db),'source':'SIMULATED DEMO DATA · persisted events'}

@app.get('/api/v1/digital-twin/snapshot')
def digital_twin_snapshot(db:Session=Depends(get_db)):
    return twin_snapshot(db)
@app.get('/api/v1/digital-twin/flow')
def flow(category:Optional[str]=Query(default=None),db:Session=Depends(get_db)):
    if category:
        normalized=category.strip().lower()
        events=in_window(db.query(WasteEvent).filter(func.lower(WasteEvent.category)==normalized)).all()
        event_ids=[item.id for item in events]
        detected=round(sum(item.weight_kg for item in events),3)
        stages=['collection','recovery'] if normalized=='organic' else (['collection','sorting','disposal'] if not is_compatible('recyclable',category) and normalized not in {'paper','cardboard'} else ['collection','sorting','recycling'])
        flow_rows=[]
        for stage in ['detection','segregation']:
            q=db.query(func.sum(WasteFlowEvent.quantity_kg),func.count(WasteFlowEvent.id)).filter(WasteFlowEvent.stage==stage)
            if event_ids:q=q.filter(WasteFlowEvent.source_event_id.in_(event_ids))
            else:q=q.filter(False)
            amount,count=q.first(); flow_rows.append({'stage':stage,'name':stage.title(),'quantity_kg':round(float(amount or 0),3),'event_count':count or 0,'available':bool(event_ids)})
        for stage in stages:
            flow_rows.append({'stage':stage,'name':stage.title(),'quantity_kg':None,'event_count':None,'available':False,'note':'INSUFFICIENT DATA: downstream events are not linked to this category in persisted records.'})
        return {'category':category,'pipeline':[x['stage'] for x in flow_rows],'stages':flow_rows,'category_waste_kg':detected if events else None,'source':'category-specific persisted WasteEvents; unattributed downstream quantities are not inferred'}
    start,end=window(); rows=db.query(WasteFlowEvent.stage,func.sum(WasteFlowEvent.quantity_kg),func.count(WasteFlowEvent.id)).filter(WasteFlowEvent.created_at>=start).group_by(WasteFlowEvent.stage).all(); values={stage:(round(float(weight),1),count) for stage,weight,count in rows}; total=values.get('detection',(0,0))[0]
    return {'window':'LAST_7_DAYS','window_start':start.isoformat(),'window_end':end.isoformat(),'stages':[{'stage':stage,'name':stage.title(),'quantity_kg':values.get(stage,(0,0))[0],'value':values.get(stage,(0,0))[0],'event_count':values.get(stage,(0,0))[1],'percentage':round(values.get(stage,(0,0))[0]/total*100,1) if total else 0,'available':stage in values} for stage in ['detection','segregation','collection','sorting','recycling','recovery','disposal']],'source':'persisted WasteFlowEvent records'}
@app.post('/api/v1/simulation/run')
def simulation(payload:SimRequest,db:Session=Depends(get_db)):
    twin(db)
    result=run_simulation(db.query(Bin).all(),payload.collection_frequency,payload.segregation_improvement,payload.recovery_improvement,current_recycling=recycling_stats(db)['recycling_efficiency'] or 0.0,current_frequency=collection_frequency(db));s=Simulation(collection_frequency=payload.collection_frequency,segregation_improvement=payload.segregation_improvement);db.add(s);db.commit();result['id']=s.id;result['label']='SIMULATED WHAT-IF RESULT';return result
@app.get('/api/v1/ai/context')
def ai_context(db:Session=Depends(get_db)):return context(db)
@app.post('/api/v1/ai/chat')
def chat(payload:ChatRequest,db:Session=Depends(get_db)):return ask(payload.message,context(db))
@app.post('/api/v1/ai/analyze')
def analyze(db:Session=Depends(get_db)):return ask('Analyze the current campus and provide evidence-backed recommendations for segregation, recycling, and collection. Use the required observation, evidence, recommendation, and expected operational effect sections.',context(db))
@app.post('/api/v1/ai/explain-simulation')
def explain_simulation(payload:SimRequest,db:Session=Depends(get_db)):
    twin(db)
    result=run_simulation(db.query(Bin).all(),payload.collection_frequency,payload.segregation_improvement,payload.recovery_improvement,current_recycling=recycling_stats(db)['recycling_efficiency'] or 0.0,current_frequency=collection_frequency(db))
    result['label']='SIMULATED WHAT-IF RESULT'
    ai_context=context(db); ai_context['simulation_result']=result
    return {**ask('Explain the supplied deterministic simulation result. Do not recompute or invent values; distinguish measured baseline context from simulated after-values and recommend an operational action.',ai_context),'simulation_result':result}
@app.post('/api/v1/ai/report')
def report(db:Session=Depends(get_db)):
    r=ask('Generate a full campus waste operations report with Campus Overview, Segregation, Collection, Recycling, Action Plan, and Data Quality.',context(db));db.add(AiReport(title='Sustainability report',content=r['message'],mode=r['mode']));db.commit();return {'title':'GENAI REPORT' if r['mode']=='LIVE GENAI' else 'GROUNDED RULES REPORT',**r}
@app.get('/api/v1/analytics/overview')
def analytics(db:Session=Depends(get_db)):return context(db)
@app.get('/api/v1/analytics/contamination')
def contamination_analytics(db:Session=Depends(get_db)):
    rows=[]
    for stream in sorted({b.target_stream or 'recyclable' for b in db.query(Bin).all()}):
        bins_for_stream=[b for b in db.query(Bin).all() if (b.target_stream or 'recyclable')==stream]
        events=[e for b in bins_for_stream for e in in_window(db.query(WasteEvent).filter(WasteEvent.bin_id==b.id)).all()]
        total=sum(e.weight_kg for e in events); bad=sum(e.weight_kg for e in events if target_mismatch(stream,e.category)); affected=sorted({b.code for b in bins_for_stream if current_contamination(db,b)>0})
        rows.append({'stream':stream,'contamination_pct':round(bad/total*100,1) if total else None,'event_count':len(events),'affected_bins':affected,'recommended_action':'Review source segregation and move incompatible material' if bad else 'No action indicated from persisted events'})
    return {'window':'LAST_7_DAYS','streams':rows,'trend':'INSUFFICIENT DATA','source':'persisted WasteEvent records'}
