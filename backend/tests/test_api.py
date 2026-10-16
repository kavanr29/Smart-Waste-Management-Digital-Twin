import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# Isolated temporary SQLite database for tests. Never touch the user's database.
_TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='wastewise-pytest-')
_TEST_DATABASE = Path(_TEST_DIRECTORY.name) / 'test.db'
os.environ['DATABASE_URL'] = f"sqlite:///{_TEST_DATABASE}"

from app.main import app, accumulation, twin
from app.core import engine, Base, SessionLocal
from app.models import Bin, Collection, Location, WasteEvent, SortingEvent, RecyclingEvent, WasteFlowEvent
from app.services.digital_twin.engine import fill_percentage, status, priority, overflow
from app.services.bin_recommender import is_compatible, recommend_bins
from app.services.computer_vision.category_mapper import map_category, is_recyclable, estimated_weight_kg
from app.services.computer_vision.model import ComputerVisionModel, get_model
from app.services.simulation.engine import run_simulation
from app.services.genai.service import ask, fallback

Base.metadata.create_all(engine)


@pytest.fixture(scope="session", autouse=True)
def cleanup_engine():
    yield
    engine.dispose()
    try:
        _TEST_DIRECTORY.cleanup()
    except Exception:
        pass


def client():
    return TestClient(app)


# ========================================================
# 1. CORE API & HEALTH & DASHBOARD TESTS
# ========================================================

def test_health_seed_and_dashboard():
    with client() as c:
        assert c.get('/health').json()['status'] == 'ok'
        d = c.get('/api/v1/dashboard').json()
        assert d['kpis']['active_bins'] == 10
        assert d['window_start']
        assert 'LAST 7 DAYS' in d['source']


def test_seed_endpoint():
    with client() as c:
        response = c.post('/api/v1/demo/seed')
        assert response.status_code == 200
        assert response.json()['bin_count'] >= 10


def test_dashboard_dynamic_metrics():
    with client() as c:
        d = c.get('/api/v1/dashboard').json()
        k = d['kpis']
        assert all(metric in k for metric in ['total_waste_kg', 'recyclable_waste_kg', 'collection_efficiency', 'contamination_rate', 'overflow_risks'])
        assert k['active_bins'] == 10
        # Empty bins must not be in collection_required
        for b in d['bins']:
            if b['fill_kg'] == 0:
                assert b['collection_status'] == 'CURRENT'
                assert b['priority_label'] == 'LOW'


# ========================================================
# 2. DIGITAL TWIN ENGINE & STATE INVARIANT TESTS
# ========================================================

def test_digital_twin_fill_percentage():
    class DummyBin:
        def __init__(self, fill_kg, capacity_kg):
            self.fill_kg = fill_kg
            self.capacity_kg = capacity_kg

    assert fill_percentage(DummyBin(0, 100)) == 0.0
    assert fill_percentage(DummyBin(25, 100)) == 25.0
    assert fill_percentage(DummyBin(150, 100)) == 100.0  # bounded at 100
    assert fill_percentage(DummyBin(-5, 100)) == 0.0   # negative fill guarded
    assert fill_percentage(DummyBin(10, 0)) == 0.0     # 0 capacity guarded
    assert fill_percentage(DummyBin(10, -50)) == 0.0   # negative capacity guarded


def test_digital_twin_status_thresholds():
    assert status(0.0) == "EMPTY"
    assert status(-5.0) == "EMPTY"
    assert status(10.0) == "LOW"
    assert status(24.9) == "LOW"
    assert status(25.0) == "MEDIUM"
    assert status(49.9) == "MEDIUM"
    assert status(50.0) == "HIGH"
    assert status(74.9) == "HIGH"
    assert status(75.0) == "VERY HIGH"
    assert status(89.9) == "VERY HIGH"
    assert status(90.0) == "CRITICAL"
    assert status(100.0) == "CRITICAL"


def test_priority_score_boundaries_and_empty_bin_invariant():
    class TestBin:
        def __init__(self, fill_kg=0.0, capacity_kg=100.0, accumulation_rate=0.0, contamination=0.0):
            self.fill_kg = fill_kg
            self.capacity_kg = capacity_kg
            self.accumulation_rate = accumulation_rate
            self.contamination = contamination

    # Invariant: Empty bin is NEVER CRITICAL or HIGH regardless of accumulation or contamination
    empty_with_stale_data = TestBin(fill_kg=0.0, capacity_kg=50.0, accumulation_rate=25.0, contamination=100.0)
    p = priority(empty_with_stale_data)
    assert p['score'] == 0.0
    assert p['label'] == "LOW"
    assert p['is_required'] is False
    assert any("empty" in r.lower() for r in p['reasons'])

    # Non-empty high fill bin
    critical_bin = TestBin(fill_kg=95.0, capacity_kg=100.0, accumulation_rate=15.0, contamination=10.0)
    p_crit = priority(critical_bin)
    assert 0.0 <= p_crit['score'] <= 100.0
    assert p_crit['label'] == "CRITICAL"
    assert p_crit['is_required'] is True

    # Low fill bin with moderate accumulation
    low_bin = TestBin(fill_kg=10.0, capacity_kg=100.0, accumulation_rate=2.0, contamination=0.0)
    p_low = priority(low_bin)
    assert 0.0 <= p_low['score'] <= 100.0
    assert p_low['label'] in ["LOW", "MEDIUM"]


def test_overflow_prediction_valid_and_insufficient():
    class TestBin:
        def __init__(self, fill_kg=0.0, capacity_kg=100.0, accumulation_rate=0.0):
            self.fill_kg = fill_kg
            self.capacity_kg = capacity_kg
            self.accumulation_rate = accumulation_rate

    # Zero accumulation -> INSUFFICIENT DATA
    b_no_rate = TestBin(fill_kg=30.0, capacity_kg=100.0, accumulation_rate=0.0)
    o_no_rate = overflow(b_no_rate)
    assert o_no_rate['hours'] is None
    assert o_no_rate['risk'] is None
    assert o_no_rate['label'] == "INSUFFICIENT DATA"

    # Empty bin -> EMPTY BIN label, risk 0
    b_empty = TestBin(fill_kg=0.0, capacity_kg=100.0, accumulation_rate=10.0)
    o_empty = overflow(b_empty)
    assert o_empty['hours'] is None
    assert o_empty['risk'] == 0.0
    assert "EMPTY" in o_empty['label']

    # At capacity
    b_full = TestBin(fill_kg=100.0, capacity_kg=100.0, accumulation_rate=10.0)
    o_full = overflow(b_full)
    assert o_full['hours'] == 0.0
    assert o_full['risk'] == 100.0
    assert o_full['label'] == "AT CAPACITY"

    # Valid prediction: remaining = 50 kg, rate = 25 kg/day -> 2 days = 48 hours
    b_valid = TestBin(fill_kg=50.0, capacity_kg=100.0, accumulation_rate=25.0)
    o_valid = overflow(b_valid)
    assert o_valid['hours'] == 48.0
    assert o_valid['risk'] == 0.0
    assert o_valid['label'] == "PREDICTED"


def test_historical_waste_before_collection_does_not_inflate_current_bin_fill():
    with client() as c:
        # Collect bin 1 to empty state
        b1 = c.get('/api/v1/bins/1').json()
        if b1['fill_kg'] > 0:
            c.post('/api/v1/collections', json={'bin_id': 1, 'collected_by': 'tester', 'notes': 'reset'})
        
        # After collection, fill must be 0 and contamination 0
        refreshed = c.get('/api/v1/bins/1').json()
        assert refreshed['fill_kg'] == 0.0
        assert refreshed['status'] == "EMPTY"
        assert refreshed['contamination'] == 0.0
        assert refreshed['priority_label'] == "LOW"
        assert refreshed['priority_score'] == 0.0
        assert refreshed['collection_status'] == "CURRENT"
        assert refreshed['accumulation_rate'] is None
        assert refreshed['overflow_status'] == "EMPTY BIN"


def test_new_waste_after_collection_updates_state():
    with client() as c:
        # Ensure bin 1 is empty
        b1 = c.get('/api/v1/bins/1').json()
        if b1['fill_kg'] > 0:
            c.post('/api/v1/collections', json={'bin_id': 1, 'collected_by': 'tester', 'notes': 'reset'})
        
        # Deposit compatible waste into bin 1
        cat = 'Organic' if b1['target_stream'] == 'organic' else 'Plastic'
        obj_name = 'banana peel' if cat == 'Organic' else 'plastic bottle'
        res = c.post('/api/v1/waste/events', json={
            'object_name': obj_name,
            'category': cat,
            'confidence': 0.95,
            'weight_kg': 0.05,
            'bin_id': 1,
            'source': 'LIVE CV'
        })
        assert res.status_code == 200
        bin_data = res.json()['bin']
        assert bin_data['fill_kg'] == 0.05
        assert bin_data['status'] in ["LOW", "MEDIUM"]
        assert bin_data['composition'].get(cat) == 100.0
        # Post-collection data has insufficient measurement window (< 1h)
        assert bin_data['accumulation_rate'] is None
        assert bin_data['overflow_hours'] is None
        assert bin_data['overflow_risk'] is None
        assert bin_data['overflow_status'] == "INSUFFICIENT DATA"


def test_historical_waste_before_collection_does_not_inflate_accumulation():
    with client() as c:
        db = SessionLocal()
        try:
            b6 = db.get(Bin, 6)
            assert b6 is not None

            # 1. Massive historical event 5 hours ago
            historical_dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5)
            cat = "Plastic" if is_compatible(b6.target_stream, "Plastic") else "Organic"
            h_event = WasteEvent(
                object_name="historical massive waste",
                category=cat,
                confidence=0.99,
                weight_kg=40.0,
                bin_id=b6.id,
                created_at=historical_dt
            )
            db.add(h_event)
            db.commit()

            # 2. Completed collection occurred 3 hours ago
            col_dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
            col = Collection(
                bin_id=b6.id,
                weight_kg=40.0,
                collected_by="historical-cleaner",
                status="COMPLETED",
                created_at=col_dt
            )
            db.add(col)
            db.commit()

            # 3. Post-collection event: 6 kg deposited 2 hours ago
            post_dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
            p_event = WasteEvent(
                object_name="post-col waste",
                category=cat,
                confidence=0.95,
                weight_kg=6.0,
                bin_id=b6.id,
                created_at=post_dt
            )
            db.add(p_event)
            db.commit()
        finally:
            db.close()

        # Query bin 6 via API
        res = c.get('/api/v1/bins/6').json()
        assert res['fill_kg'] == 6.0
        # Measurement window: 3 hours elapsed since collection -> 3 / 24 = 0.125 days
        # Rate: 6.0 / 0.125 = 48.0 kg/day.
        # Historical 40 kg is strictly excluded!
        assert res['accumulation_rate'] is not None
        assert abs(res['accumulation_rate'] - 48.0) < 1.0


def test_empty_bin_cannot_become_critical_from_historical_data():
    with client() as c:
        db = SessionLocal()
        try:
            b4 = db.get(Bin, 4)
            # Inject contaminated historical waste into bin 4
            h_dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=6)
            db.add(WasteEvent(
                object_name="bad historical item",
                category="Hazardous",
                confidence=0.9,
                weight_kg=80.0,
                bin_id=b4.id,
                created_at=h_dt
            ))
            b4.fill_kg = 80.0
            db.commit()
        finally:
            db.close()

        # Perform completed collection on bin 4, emptying it
        col_res = c.post('/api/v1/collections', json={'bin_id': 4, 'collected_by': 'cleaner', 'notes': 'emptied bin 4'})
        assert col_res.status_code == 200

        # Verify that despite 80 kg of hazardous waste in the last 7 days, the empty bin
        # cannot receive CRITICAL priority or display stale contamination
        res = c.get('/api/v1/bins/4').json()
        assert res['fill_kg'] == 0.0
        assert res['status'] == "EMPTY"
        assert res['priority_label'] == "LOW"
        assert res['priority_score'] == 0.0
        assert res['collection_status'] == "CURRENT"
        assert res['contamination'] == 0.0
        assert res['overflow_status'] == "EMPTY BIN"
        assert res['overflow_hours'] is None
        assert res['overflow_risk'] == 0.0
        assert res['accumulation_rate'] is None


def test_insufficient_post_collection_data_is_represented_honestly():
    with client() as c:
        # Collect bin 5
        b5 = c.get('/api/v1/bins/5').json()
        if b5['fill_kg'] > 0:
            c.post('/api/v1/collections', json={'bin_id': 5, 'collected_by': 'tester'})
        
        # When bin is empty post collection, accumulation_rate is explicitly None
        empty_bin = c.get('/api/v1/bins/5').json()
        assert empty_bin['fill_kg'] == 0.0
        assert empty_bin['accumulation_rate'] is None
        assert empty_bin['overflow_status'] == "EMPTY BIN"

        # Deposit a tiny amount immediately (< 1 minute elapsed)
        cat = 'Plastic' if is_compatible(b5['target_stream'], 'Plastic') else 'Organic'
        res = c.post('/api/v1/waste/events', json={
            'object_name': 'tiny paper wrapper',
            'category': cat,
            'confidence': 0.95,
            'weight_kg': 0.02,
            'bin_id': 5,
        })
        assert res.status_code == 200
        refreshed = res.json()['bin']
        assert refreshed['fill_kg'] == 0.02
        # Data is insufficient to extrapolate a defensible daily rate: represented honestly as None
        assert refreshed['accumulation_rate'] is None
        assert refreshed['overflow_hours'] is None
        assert refreshed['overflow_risk'] is None
        assert refreshed['overflow_status'] == "INSUFFICIENT DATA"


def test_dashboard_and_digital_twin_responses_agree():
    with client() as c:
        dash = c.get('/api/v1/dashboard').json()
        twin_state = c.get('/api/v1/digital-twin/state').json()
        bins_direct = c.get('/api/v1/bins').json()

        d_bins = dash['bins']
        t_bins = twin_state['bins']
        b_bins = bins_direct

        assert len(d_bins) == len(t_bins) == len(b_bins) == 10

        for d, t, b in zip(d_bins, t_bins, b_bins):
            assert d['id'] == t['id'] == b['id']
            assert d['fill_kg'] == t['fill_kg'] == b['fill_kg']
            assert d['fill_pct'] == t['fill_pct'] == b['fill_pct']
            assert d['status'] == t['status'] == b['status']
            assert d['contamination'] == t['contamination'] == b['contamination']
            assert d['composition'] == t['composition'] == b['composition']
            assert d['accumulation_rate'] == t['accumulation_rate'] == b['accumulation_rate']
            assert d['priority_score'] == t['priority_score'] == b['priority_score']
            assert d['priority_label'] == t['priority_label'] == b['priority_label']
            assert d['collection_status'] == t['collection_status'] == b['collection_status']
            assert d['overflow_hours'] == t['overflow_hours'] == b['overflow_hours']
            assert d['overflow_risk'] == t['overflow_risk'] == b['overflow_risk']
            assert d['overflow_status'] == t['overflow_status'] == b['overflow_status']

        # KPI consistency
        kpis = dash['kpis']
        assert kpis['active_bins'] == len(d_bins)
        assert kpis['collection_required'] == sum(1 for x in d_bins if x['collection_status'] == 'REQUIRED')
        assert kpis['overflow_risks'] == sum(1 for x in d_bins if x['overflow_risk'] is not None and x['overflow_risk'] >= 50 and x['fill_pct'] > 0)


def test_digital_twin_snapshot_is_render_ready_and_honest_about_data():
    with client() as c:
        response = c.get('/api/v1/digital-twin/snapshot')
        assert response.status_code == 200
        snapshot = response.json()

        assert snapshot['model']['name'] == 'WasteWise operational twin'
        assert snapshot['model']['version']
        assert snapshot['generated_at']
        assert len(snapshot['assets']['bins']) == 10
        assert len(snapshot['assets']['facilities']) == 5
        assert snapshot['metrics']['active_bins'] == 10
        assert snapshot['data_quality']['source'] == 'SQLite event store'
        assert 'overflow_risk' in snapshot['data_quality']['estimated_fields']

        stages = {row['stage']: row for row in snapshot['flow']}
        assert set(stages) == {
            'detection', 'segregation', 'collection', 'sorting',
            'recycling', 'recovery', 'disposal',
        }
        assert all(row['quantity_kg'] >= 0 for row in snapshot['flow'])
        assert all('state' in asset for asset in snapshot['assets']['bins'])


# ========================================================
# 3. COMPUTER VISION & SCANNING WORKFLOW TESTS
# ========================================================

def test_category_mapping_classes():
    # Classes from best.pt
    assert map_category('banana-peel') == 'Organic'
    assert map_category('glass') == 'Glass'
    assert map_category('metal') == 'Metal'
    assert map_category('orange-peel') == 'Organic'
    assert map_category('paper') == 'Paper'
    assert map_category('plastic') == 'Plastic'
    assert map_category('styrofoam') == 'Other'
    
    assert is_recyclable('Plastic') is True
    assert is_recyclable('Glass') is True
    assert is_recyclable('Organic') is False
    assert is_recyclable('Other') is False
    assert estimated_weight_kg('Plastic') > 0


def test_cv_detection_and_validation(monkeypatch):
    with client() as c:
        # Invalid image payload
        assert c.post('/api/v1/waste/detect', files={'file': ('bad.txt', b'not an image', 'text/plain')}).status_code == 400

        # Mock detection with multiple objects
        monkeypatch.setattr('app.main.detect_image', lambda data, name, path: {
            'mode': 'LIVE CV',
            'image_id': 'img-test-1',
            'image_width': 640,
            'image_height': 480,
            'reliable_detections': 2,
            'detections': [
                {'detection_id': 'd1', 'object': 'plastic', 'class_name': 'plastic', 'category': 'Plastic', 'confidence': 0.94, 'box': [10, 20, 100, 150], 'x1': 10, 'y1': 20, 'x2': 100, 'y2': 150, 'recyclable': True, 'estimated_weight_kg': 0.025, 'reliable': True},
                {'detection_id': 'd2', 'object': 'glass', 'class_name': 'glass', 'category': 'Glass', 'confidence': 0.88, 'box': [200, 150, 320, 400], 'x1': 200, 'y1': 150, 'x2': 320, 'y2': 400, 'recyclable': True, 'estimated_weight_kg': 0.200, 'reliable': True}
            ]
        })
        res = c.post('/api/v1/waste/detect', files={'file': ('img.png', b'fake-data', 'image/png')})
        assert res.status_code == 200
        body = res.json()
        assert len(body['detections']) == 2
        assert body['detections'][0]['category'] == 'Plastic'
        assert body['detections'][1]['category'] == 'Glass'


def test_cv_status_and_real_model_inference():
    with client() as c:
        status_res = c.get('/api/v1/cv/status').json()
        assert status_res['model_configured'] is True
        assert status_res['model_loaded'] is True
        assert status_res['inference_ready'] is True
        assert {'banana-peel', 'glass', 'metal', 'orange-peel', 'paper', 'plastic', 'styrofoam'}.issubset(set(status_res['classes']))

        # Test real inference with Ultralytics YOLO model on a blank image
        import cv2, numpy as np
        ok, encoded = cv2.imencode('.png', np.zeros((128, 128, 3), dtype=np.uint8))
        assert ok
        detect_res = c.post('/api/v1/waste/detect', files={'file': ('test.png', encoded.tobytes(), 'image/png')})
        assert detect_res.status_code == 200
        data = detect_res.json()
        assert data['mode'] == 'LIVE CV'
        assert data['model_loaded'] is True


def test_event_idempotency_prevents_duplicate():
    with client() as c:
        rec = c.post('/api/v1/bins/recommend', json={'category': 'Plastic', 'weight_kg': 0.025}).json()['recommended']
        assert rec is not None
        payload = {
            'object_name': 'plastic bottle',
            'category': 'Plastic',
            'confidence': 0.92,
            'weight_kg': 0.025,
            'bin_id': rec['id'],
            'source': 'LIVE CV',
            'idempotency_key': 'scan-unique-key-12345'
        }
        res1 = c.post('/api/v1/waste/events', json=payload)
        res2 = c.post('/api/v1/waste/events', json=payload)
        assert res1.status_code == res2.status_code == 200
        assert res1.json()['duplicate'] is False
        assert res2.json()['duplicate'] is True
        assert res1.json()['id'] == res2.json()['id']

        # Conflict if same idempotency key is reused with different payload
        conflict_payload = dict(payload)
        conflict_payload['weight_kg'] = 1.50
        res_conflict = c.post('/api/v1/waste/events', json=conflict_payload)
        assert res_conflict.status_code == 409


def test_event_validation_without_idempotency_key():
    with client() as c:
        # 1. Non-existent bin
        res_nobin = c.post('/api/v1/waste/events', json={
            'object_name': 'metal can',
            'category': 'Metal',
            'confidence': 0.9,
            'weight_kg': 0.1,
            'bin_id': 99999
        })
        assert res_nobin.status_code == 404

        # 2. Incompatible category (WITHOUT idempotency key)
        # Find an organic bin
        bins = c.get('/api/v1/bins').json()
        organic_bin = next(b for b in bins if b['target_stream'] == 'organic')
        res_incompat = c.post('/api/v1/waste/events', json={
            'object_name': 'plastic bottle',
            'category': 'Plastic',
            'confidence': 0.9,
            'weight_kg': 0.1,
            'bin_id': organic_bin['id']
        })
        assert res_incompat.status_code == 422

        # 3. Capacity exceeded (WITHOUT idempotency key)
        available = organic_bin['capacity_kg'] - organic_bin['fill_kg']
        over_weight = min(100.0, available + 5.0)
        res_overcap = c.post('/api/v1/waste/events', json={
            'object_name': 'massive organic load',
            'category': 'Organic',
            'confidence': 0.9,
            'weight_kg': over_weight,
            'bin_id': organic_bin['id']
        })
        assert res_overcap.status_code == 409
        assert 'Selected bin no longer has enough capacity' in res_overcap.json()['detail']

        # 4. Negative weight rejected
        res_neg = c.post('/api/v1/waste/events', json={
            'object_name': 'negative weight item',
            'category': 'Organic',
            'confidence': 0.9,
            'weight_kg': -2.0,
            'bin_id': organic_bin['id']
        })
        assert res_neg.status_code == 422


def test_event_validation_with_idempotency_key():
    with client() as c:
        bins = c.get('/api/v1/bins').json()
        organic_bin = next(b for b in bins if b['target_stream'] == 'organic')

        # 1. Incompatible category WITH idempotency key must still be rejected (422)
        res_incompat = c.post('/api/v1/waste/events', json={
            'object_name': 'plastic item',
            'category': 'Plastic',
            'confidence': 0.9,
            'weight_kg': 0.1,
            'bin_id': organic_bin['id'],
            'idempotency_key': 'key-incompat-test-1'
        })
        assert res_incompat.status_code == 422
        assert 'does not accept this waste category' in res_incompat.json()['detail']

        # 2. Capacity exceeded WITH idempotency key must still be rejected (409)
        available = organic_bin['capacity_kg'] - organic_bin['fill_kg']
        over_weight = min(100.0, available + 5.0)
        res_overcap = c.post('/api/v1/waste/events', json={
            'object_name': 'excessive organic',
            'category': 'Organic',
            'confidence': 0.9,
            'weight_kg': over_weight,
            'bin_id': organic_bin['id'],
            'idempotency_key': 'key-overcap-test-2'
        })
        assert res_overcap.status_code == 409
        assert 'Selected bin no longer has enough capacity' in res_overcap.json()['detail']

        # 3. Valid event WITH idempotency key persists successfully
        res_valid = c.post('/api/v1/waste/events', json={
            'object_name': 'valid organic leaf',
            'category': 'Organic',
            'confidence': 0.95,
            'weight_kg': 0.05,
            'bin_id': organic_bin['id'],
            'idempotency_key': 'key-valid-test-3'
        })
        assert res_valid.status_code == 200
        assert res_valid.json()['duplicate'] is False

        # 4. Same key repeated with same payload returns original (duplicate=True)
        res_dup = c.post('/api/v1/waste/events', json={
            'object_name': 'valid organic leaf',
            'category': 'Organic',
            'confidence': 0.95,
            'weight_kg': 0.05,
            'bin_id': organic_bin['id'],
            'idempotency_key': 'key-valid-test-3'
        })
        assert res_dup.status_code == 200
        assert res_dup.json()['duplicate'] is True
        assert res_dup.json()['id'] == res_valid.json()['id']


def test_cv_diagnostic_when_model_cannot_be_loaded():
    # If the model cannot be loaded, ComputerVisionModel returns clear diagnostic
    model = ComputerVisionModel(model_path="non_existent_weights_path.pt")
    assert model.available is False
    assert len(model.error) > 0
    assert "not found" in model.error.lower() or "failed" in model.error.lower()
    assert model.classes == []

    import numpy as np
    blank = np.zeros((128, 128, 3), dtype=np.uint8)
    # Never claim or fabricate successful inference
    assert model.predict(blank) == []


def test_waste_detect_image_size_limit():
    with client() as c:
        # Create a payload larger than 10MB
        oversized = b'0' * (10 * 1024 * 1024 + 1024)
        res = c.post('/api/v1/waste/detect', files={'file': ('huge.png', oversized, 'image/png')})
        assert res.status_code == 413
        assert 'exceeds maximum permitted size' in res.json()['detail']


# ========================================================
# 4. BIN RECOMMENDER TESTS
# ========================================================

def test_stream_compatibility():
    assert is_compatible('recyclable', 'Plastic') is True
    assert is_compatible('recyclable', 'Glass') is True
    assert is_compatible('recyclable', 'Organic') is False
    assert is_compatible('organic', 'Organic') is True
    assert is_compatible('organic', 'Plastic') is False
    assert is_compatible('metal', 'Metal') is True
    assert is_compatible('metal', 'Paper') is False


def test_recommend_bins_ranking():
    with client() as c:
        # Recommend bin for Plastic
        res = c.post('/api/v1/bins/recommend', json={'category': 'Plastic', 'weight_kg': 0.05, 'current_zone': 'Central Plaza'})
        assert res.status_code == 200
        data = res.json()
        assert data['recommended'] is not None
        assert is_compatible(data['recommended']['target_stream'], 'Plastic')
        assert data['recommended']['available_kg'] >= 0.05
        # All alternatives must also be compatible
        for alt in data['alternatives']:
            assert is_compatible(alt['target_stream'], 'Plastic')


def test_recommend_bins_no_capacity_or_impossible_category():
    with client() as c:
        # Category not accepted by any existing campus bin
        res = c.post('/api/v1/bins/recommend', json={'category': 'Hazardous', 'weight_kg': 1.0, 'current_zone': 'Central Plaza'})
        assert res.status_code == 200
        assert res.json()['recommended'] is None
        assert 'No compatible existing bin' in res.json()['message']


# ========================================================
# 5. COLLECTION, SORTING, AND RECYCLING INTEGRITY TESTS
# ========================================================

def test_empty_bin_collection_rejected():
    with client() as c:
        # Collect bin 3 to empty state first if needed
        b = c.get('/api/v1/bins/3').json()
        if b['fill_kg'] > 0:
            c.post('/api/v1/collections', json={'bin_id': 3, 'collected_by': 'tester'})
        
        # Attempt to collect already empty bin
        res = c.post('/api/v1/collections', json={'bin_id': 3, 'collected_by': 'tester'})
        assert res.status_code == 400
        assert 'Cannot collect an empty bin' in res.json()['detail']


def test_collection_flow_and_mass_balance():
    with client() as c:
        # Find a compatible bin for Glass
        rec = c.post('/api/v1/bins/recommend', json={'category': 'Glass', 'weight_kg': 2.5}).json()['recommended']
        assert rec is not None
        target_bin_id = rec['id']

        # Deposit into compatible bin
        deposit_res = c.post('/api/v1/waste/events', json={
            'object_name': 'glass bottle',
            'category': 'Glass',
            'confidence': 0.95,
            'weight_kg': 2.5,
            'bin_id': target_bin_id
        })
        assert deposit_res.status_code == 200
        b_before = c.get(f'/api/v1/bins/{target_bin_id}').json()
        assert b_before['fill_kg'] >= 2.5

        # Collect target bin
        col_res = c.post('/api/v1/collections', json={'bin_id': target_bin_id, 'collected_by': 'ops-team', 'notes': 'valid collection'})
        assert col_res.status_code == 200
        col_id = col_res.json()['collection_id']
        collected_kg = col_res.json()['collected_weight_kg']
        assert collected_kg >= 2.5

        # Sorting: Cannot sort more than collected amount
        assert c.post('/api/v1/sorting', json={'collection_id': col_id, 'input_weight_kg': collected_kg + 100, 'sorted_weight_kg': 2.0}).status_code == 400
        # Valid sorting
        sort_res = c.post('/api/v1/sorting', json={'collection_id': col_id, 'input_weight_kg': 2.0, 'sorted_weight_kg': 1.8, 'rejected_weight_kg': 0.2})
        assert sort_res.status_code == 200

        # Recycling: Recovered cannot exceed input
        assert c.post('/api/v1/recycling', json={'category': 'Glass', 'input_weight_kg': 1.0, 'recovered_weight_kg': 1.5}).status_code == 400
        rec_res = c.post('/api/v1/recycling', json={'category': 'Glass', 'input_weight_kg': 1.0, 'recovered_weight_kg': 0.9})
        assert rec_res.status_code == 200
        assert rec_res.json()['efficiency'] == 90.0


# ========================================================
# 6. DETERMINISTIC SIMULATION TESTS
# ========================================================

def test_deterministic_simulation_scenarios():
    class MockBin:
        def __init__(self, fill_kg, capacity_kg, contamination=0.0, rate=5.0):
            self.fill_kg = fill_kg
            self.capacity_kg = capacity_kg
            self.contamination = contamination
            self.accumulation_rate = rate
            self.overflow_risk = 40.0

    bins = [MockBin(40, 100, contamination=15.0), MockBin(60, 100, contamination=20.0)]
    
    # Increased frequency & segregation
    result = run_simulation(bins, frequency=3.0, segregation=20.0, recovery=5.0, current_recycling=60.0, current_frequency=1.0)
    assert result['label'] == 'SIMULATED WHAT-IF RESULT'
    assert result['after']['overflow_risk'] < result['before']['overflow_risk']
    assert result['after']['contamination'] < result['before']['contamination']
    assert result['after']['recycling_efficiency'] > result['before']['recycling_efficiency']
    assert 'workload_trips_week' in result['after']
    assert len(result['assumptions']) >= 3
    assert len(result['limitations']) >= 2


def test_simulation_api_endpoints():
    with client() as c:
        res = c.post('/api/v1/simulation/run', json={'collection_frequency': 2.5, 'segregation_improvement': 15.0, 'recovery_improvement': 5.0})
        assert res.status_code == 200
        body = res.json()
        assert all(k in body for k in ['before', 'after', 'delta', 'assumptions', 'limitations'])


# ========================================================
# 7. GENAI OPERATIONS COPILOT & REPORTING TESTS
# ========================================================

def test_genai_operations_fallback_and_grounding():
    with client() as c:
        ctx = c.get('/api/v1/ai/context').json()
        # Chat queries
        ans_action = ask('What is the daily action plan?', ctx)
        assert ans_action['mode'] in ['LIVE GENAI', 'DEMO / GROUNDED RULES']
        assert 'CAMPUS OVERVIEW' in ans_action['message'] or 'OBSERVATION' in ans_action['message']

        ans_bin = ask('Explain BIN-01 priority rationale', ctx)
        assert 'BIN-01' in ans_bin['message']

        ans_missing = ask('What is the fill of unlisted sensor #9999?', {})
        assert 'INSUFFICIENT DATA' in ans_missing['message']


def test_sustainability_report_endpoint():
    with client() as c:
        rep = c.post('/api/v1/ai/report')
        assert rep.status_code == 200
        data = rep.json()
        assert 'title' in data
        assert any(header in data['message'] for header in ['CAMPUS OVERVIEW', 'SEGREGATION', 'COLLECTION', 'RECYCLING'])


def test_explain_simulation_endpoint():
    with client() as c:
        res = c.post('/api/v1/ai/explain-simulation', json={'collection_frequency': 3.0, 'segregation_improvement': 15.0, 'recovery_improvement': 5.0})
        assert res.status_code == 200
        assert 'simulation_result' in res.json()
        assert res.json()['message']


# ========================================================
# 8. REGRESSION TESTS: BIN INVARIANTS & ENDPOINT CONSISTENCY
# ========================================================

def test_regression_empty_bin_invariants():
    """Verify empty bin invariant:
    - accumulation_rate = None
    - overflow_status = EMPTY BIN
    - overflow_risk = 0
    - priority_score = 0
    - priority_label = LOW
    - contamination = 0
    - collection_status = CURRENT
    Across /api/v1/bins/{id}, /api/v1/bins, /api/v1/dashboard, and /api/v1/digital-twin/state.
    """
    with client() as c:
        # Collect bin 5 if it has any fill
        b5 = c.get('/api/v1/bins/5').json()
        if b5['fill_kg'] > 0:
            c.post('/api/v1/collections', json={'bin_id': 5, 'collected_by': 'regression-cleaner'})

        # Verify via /api/v1/bins/5
        detail = c.get('/api/v1/bins/5').json()
        assert detail['fill_kg'] == 0.0
        assert detail['status'] == "EMPTY"
        assert detail['accumulation_rate'] is None
        assert detail['overflow_status'] == "EMPTY BIN"
        assert detail['overflow_risk'] == 0
        assert detail['priority_score'] == 0
        assert detail['priority_label'] == "LOW"
        assert detail['contamination'] == 0
        assert detail['collection_status'] == "CURRENT"

        # Verify via /api/v1/bins
        all_bins = c.get('/api/v1/bins').json()
        bin5_list = next(b for b in all_bins if b['id'] == 5)
        assert bin5_list['fill_kg'] == 0.0
        assert bin5_list['accumulation_rate'] is None
        assert bin5_list['overflow_status'] == "EMPTY BIN"
        assert bin5_list['overflow_risk'] == 0
        assert bin5_list['priority_score'] == 0
        assert bin5_list['priority_label'] == "LOW"
        assert bin5_list['contamination'] == 0
        assert bin5_list['collection_status'] == "CURRENT"

        # Verify via /api/v1/digital-twin/state
        twin_bins = c.get('/api/v1/digital-twin/state').json()['bins']
        bin5_twin = next(b for b in twin_bins if b['id'] == 5)
        assert bin5_twin['fill_kg'] == 0.0
        assert bin5_twin['accumulation_rate'] is None
        assert bin5_twin['overflow_status'] == "EMPTY BIN"
        assert bin5_twin['overflow_risk'] == 0
        assert bin5_twin['priority_score'] == 0
        assert bin5_twin['priority_label'] == "LOW"
        assert bin5_twin['contamination'] == 0
        assert bin5_twin['collection_status'] == "CURRENT"

        # Verify via /api/v1/dashboard
        dash_bins = c.get('/api/v1/dashboard').json()['bins']
        bin5_dash = next(b for b in dash_bins if b['id'] == 5)
        assert bin5_dash['fill_kg'] == 0.0
        assert bin5_dash['accumulation_rate'] is None
        assert bin5_dash['overflow_status'] == "EMPTY BIN"
        assert bin5_dash['overflow_risk'] == 0
        assert bin5_dash['priority_score'] == 0
        assert bin5_dash['priority_label'] == "LOW"
        assert bin5_dash['contamination'] == 0
        assert bin5_dash['collection_status'] == "CURRENT"


def test_regression_insufficient_window_returns_none():
    """Verify that if post-collection elapsed time is < 1 hour, accumulation_rate is None."""
    with client() as c:
        db = SessionLocal()
        try:
            b7 = db.get(Bin, 7)
            assert b7 is not None
            # Collection 20 minutes ago
            col_dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=20)
            col = Collection(
                bin_id=b7.id,
                weight_kg=10.0,
                collected_by="cleaner-7",
                status="COMPLETED",
                created_at=col_dt
            )
            db.add(col)
            # Post-collection event 10 minutes ago
            post_dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
            cat = "Plastic" if is_compatible(b7.target_stream, "Plastic") else "Organic"
            event = WasteEvent(
                object_name="recent deposit",
                category=cat,
                confidence=0.9,
                weight_kg=1.5,
                bin_id=b7.id,
                created_at=post_dt
            )
            db.add(event)
            db.commit()
        finally:
            db.close()

        res = c.get('/api/v1/bins/7').json()
        assert res['fill_kg'] == 1.5
        # Insufficient measurement window (< 1h) must return None, not an inflated daily rate
        assert res['accumulation_rate'] is None
        assert res['overflow_status'] == "INSUFFICIENT DATA"
        assert res['overflow_hours'] is None
        assert res['overflow_risk'] is None


def test_regression_transactional_fill_maintenance():
    """Verify deposits increase current fill exactly once and collections empty the bin."""
    with client() as c:
        # Pick bin 8
        b8 = c.get('/api/v1/bins/8').json()
        if b8['fill_kg'] > 0:
            c.post('/api/v1/collections', json={'bin_id': 8, 'collected_by': 'tester'})
        
        # Verify 0 fill
        assert c.get('/api/v1/bins/8').json()['fill_kg'] == 0.0

        # Deposit 1 - use compatible category for bin 8
        from app.services.bin_recommender import compatible_categories
        compat = compatible_categories(b8['target_stream'])
        cat = list(compat)[0].capitalize()
        res1 = c.post('/api/v1/waste/events', json={
            'object_name': 'item 1',
            'category': cat,
            'confidence': 0.9,
            'weight_kg': 2.0,
            'bin_id': 8,
            'idempotency_key': 'txn-test-event-1'
        })
        assert res1.status_code == 200
        assert c.get('/api/v1/bins/8').json()['fill_kg'] == 2.0

        # Duplicate deposit (same idempotency key) must NOT increase fill
        c.post('/api/v1/waste/events', json={
            'object_name': 'item 1',
            'category': cat,
            'confidence': 0.9,
            'weight_kg': 2.0,
            'bin_id': 8,
            'idempotency_key': 'txn-test-event-1'
        })
        assert c.get('/api/v1/bins/8').json()['fill_kg'] == 2.0

        # Deposit 2 (different key) increases fill exactly once
        c.post('/api/v1/waste/events', json={
            'object_name': 'item 2',
            'category': cat,
            'confidence': 0.9,
            'weight_kg': 3.0,
            'bin_id': 8,
            'idempotency_key': 'txn-test-event-2'
        })
        assert c.get('/api/v1/bins/8').json()['fill_kg'] == 5.0

        # Collection empties the bin
        col_res = c.post('/api/v1/collections', json={'bin_id': 8, 'collected_by': 'cleaner'})
        assert col_res.status_code == 200
        assert col_res.json()['collected_weight_kg'] == 5.0
        assert c.get('/api/v1/bins/8').json()['fill_kg'] == 0.0


def test_regression_accumulation_window_collection_older_than_seven_days():
    """ISSUE 1: Verify collection > 7 days ago uses 7-day cutoff window,
    excluding events older than 7 days from accumulation rate while keeping
    current fill representing all uncollected waste.
    """
    with client() as c:
        db = SessionLocal()
        try:
            b9 = db.get(Bin, 9)
            assert b9 is not None
            b9_id = b9.id
            now_dt = datetime.now(timezone.utc).replace(tzinfo=None)

            # 1. Collection occurred 10 days ago (older than 7 days)
            col_dt = now_dt - timedelta(days=10)
            col = Collection(
                bin_id=b9_id,
                weight_kg=30.0,
                collected_by="cleaner-old",
                status="COMPLETED",
                created_at=col_dt
            )
            db.add(col)

            # 2. Waste event outside 7-day window (8 days ago, 12 kg)
            cat = "Plastic" if is_compatible(b9.target_stream, "Plastic") else "Organic"
            event_outside = WasteEvent(
                object_name="older deposit",
                category=cat,
                confidence=0.95,
                weight_kg=12.0,
                bin_id=b9_id,
                created_at=now_dt - timedelta(days=8)
            )
            db.add(event_outside)

            # 3. Waste event inside 7-day window (2 days ago, 7 kg)
            event_inside = WasteEvent(
                object_name="recent deposit",
                category=cat,
                confidence=0.95,
                weight_kg=7.0,
                bin_id=b9_id,
                created_at=now_dt - timedelta(days=2)
            )
            db.add(event_inside)
            db.commit()
        finally:
            db.close()

        res = c.get(f'/api/v1/bins/{b9_id}').json()
        # Current fill must represent all uncollected waste: 12.0 + 7.0 = 19.0 kg
        assert res['fill_kg'] == 19.0

        # Accumulation rate must only sum events inside the 7-day window (7.0 kg)
        # over the 7-day elapsed measurement window (7.0 days): 7.0 / 7.0 = 1.0 kg/day
        assert res['accumulation_rate'] is not None
        assert abs(res['accumulation_rate'] - 1.0) < 0.05


def test_regression_current_state_consistency_never_collected_bin():
    """ISSUE 2: Verify a bin that has never had a completed collection
    consistently includes older (>7d) and newer (<=7d) events across fill,
    composition, and contamination on dashboard, bin, and twin endpoints.
    """
    with client() as c:
        db = SessionLocal()
        try:
            # Create a dedicated virtual bin with no completed collections
            loc = db.query(Location).first()
            test_bin = Bin(
                code="BIN-UNCOLLECTED",
                name="Uncollected Research Bin",
                capacity_kg=100.0,
                fill_kg=0.0,
                target_stream="recyclable",
                status="EMPTY",
                location_id=loc.id if loc else 1
            )
            db.add(test_bin)
            db.flush()

            now_dt = datetime.now(timezone.utc).replace(tzinfo=None)

            # Older event: 10 days ago (4.0 kg Cardboard - compatible with recyclable stream)
            e_older = WasteEvent(
                object_name="old cardboard",
                category="Cardboard",
                confidence=0.95,
                weight_kg=4.0,
                bin_id=test_bin.id,
                created_at=now_dt - timedelta(days=10)
            )
            db.add(e_older)

            # Newer event: 2 days ago (6.0 kg Organic - incompatible with recyclable stream)
            e_newer = WasteEvent(
                object_name="new organic food",
                category="Organic",
                confidence=0.90,
                weight_kg=6.0,
                bin_id=test_bin.id,
                created_at=now_dt - timedelta(days=2)
            )
            db.add(e_newer)
            db.commit()
            bin_id = test_bin.id
        finally:
            db.close()

        # 1. Bin detail endpoint
        res_detail = c.get(f'/api/v1/bins/{bin_id}').json()
        assert res_detail['fill_kg'] == 10.0
        assert res_detail['composition'] == {'Cardboard': 40.0, 'Organic': 60.0}
        assert res_detail['contamination'] == 60.0

        # 2. Bins list endpoint
        bins_list = c.get('/api/v1/bins').json()
        bin_in_list = next(b for b in bins_list if b['id'] == bin_id)
        assert bin_in_list['fill_kg'] == 10.0
        assert bin_in_list['composition'] == {'Cardboard': 40.0, 'Organic': 60.0}
        assert bin_in_list['contamination'] == 60.0

        # 3. Digital Twin state endpoint
        twin_data = c.get('/api/v1/digital-twin/state').json()
        bin_in_twin = next(b for b in twin_data['bins'] if b['id'] == bin_id)
        assert bin_in_twin['fill_kg'] == 10.0
        assert bin_in_twin['composition'] == {'Cardboard': 40.0, 'Organic': 60.0}
        assert bin_in_twin['contamination'] == 60.0

        # 4. Dashboard endpoint
        dash_data = c.get('/api/v1/dashboard').json()
        bin_in_dash = next(b for b in dash_data['bins'] if b['id'] == bin_id)
        assert bin_in_dash['fill_kg'] == 10.0
        assert bin_in_dash['composition'] == {'Cardboard': 40.0, 'Organic': 60.0}
        assert bin_in_dash['contamination'] == 60.0


def test_regression_sorting_mass_balance_and_tolerance():
    """ISSUE 3: Validate sorting mass balance: input = sorted + rejected.
    Test valid balanced, omitted rejected, sorted > input, rejected > remaining,
    unbalanced, disappeared material, and ensure no database records on failure.
    """
    with client() as c:
        # Create a collection with 30 kg available material
        db = SessionLocal()
        try:
            b1 = db.get(Bin, 1)
            assert b1 is not None
            col = Collection(
                bin_id=b1.id,
                weight_kg=30.0,
                collected_by="sort-tester",
                status="COMPLETED"
            )
            db.add(col)
            db.commit()
            col_id = col.id
            init_sort_count = db.query(SortingEvent).count()
            init_flow_count = db.query(WasteFlowEvent).count()
        finally:
            db.close()

        # 1. Valid balanced sorting request
        res1 = c.post('/api/v1/sorting', json={
            'collection_id': col_id,
            'category': 'mixed',
            'input_weight_kg': 5.0,
            'sorted_weight_kg': 3.5,
            'rejected_weight_kg': 1.5
        })
        assert res1.status_code == 200
        assert res1.json()['sorted_weight_kg'] == 3.5

        # 2. Rejected weight omitted -> automatically calculated
        res2 = c.post('/api/v1/sorting', json={
            'collection_id': col_id,
            'category': 'mixed',
            'input_weight_kg': 4.0,
            'sorted_weight_kg': 3.0
        })
        assert res2.status_code == 200
        assert res2.json()['rejected_weight_kg'] == 1.0

        # 3. Sorted weight greater than input -> HTTP 400
        res3 = c.post('/api/v1/sorting', json={
            'collection_id': col_id,
            'category': 'mixed',
            'input_weight_kg': 4.0,
            'sorted_weight_kg': 5.0,
            'rejected_weight_kg': 0.0
        })
        assert res3.status_code == 400

        # 4. Rejected weight greater than remaining amount -> HTTP 400
        res4 = c.post('/api/v1/sorting', json={
            'collection_id': col_id,
            'category': 'mixed',
            'input_weight_kg': 4.0,
            'sorted_weight_kg': 3.0,
            'rejected_weight_kg': 2.0  # remaining was 1.0; 2.0 > 1.0
        })
        assert res4.status_code == 400

        # 5. An unbalanced request (sum != input) -> HTTP 400
        res5 = c.post('/api/v1/sorting', json={
            'collection_id': col_id,
            'category': 'mixed',
            'input_weight_kg': 5.0,
            'sorted_weight_kg': 3.0,
            'rejected_weight_kg': 1.0  # sum is 4.0 != 5.0
        })
        assert res5.status_code == 400

        # 6. A request that would previously make material disappear -> HTTP 400
        res6 = c.post('/api/v1/sorting', json={
            'collection_id': col_id,
            'category': 'mixed',
            'input_weight_kg': 6.0,
            'sorted_weight_kg': 3.0,
            'rejected_weight_kg': 1.5  # 1.5 kg disappeared!
        })
        assert res6.status_code == 400

        # Ensure invalid requests created NO database records
        db = SessionLocal()
        try:
            final_sort_count = db.query(SortingEvent).count()
            final_flow_count = db.query(WasteFlowEvent).count()
            # Exactly 2 successful requests were made (res1 and res2)
            assert final_sort_count == init_sort_count + 2
            assert final_flow_count == init_flow_count + 2
        finally:
            db.close()


def test_regression_idempotency_duplicate_retry_when_bin_full():
    """ISSUE 4: An identical retry using an existing idempotency key must return
    original event even if bin has subsequently become full.
    Reusing key with different payload returns 409.
    New requests on full bin return 409.
    """
    with client() as c:
        db = SessionLocal()
        try:
            # Pick a bin
            b = db.get(Bin, 10)
            assert b is not None
            # Collect to empty first
            col = Collection(
                bin_id=b.id,
                weight_kg=float(b.capacity_kg),
                collected_by="idem-tester",
                status="COMPLETED"
            )
            db.add(col)
            db.commit()
            bin_id = b.id
            capacity = float(b.capacity_kg)
        finally:
            db.close()

        cat = "Organic" if is_compatible(b.target_stream, "Organic") else "Plastic"
        original_payload = {
            'object_name': 'initial deposit item',
            'category': cat,
            'confidence': 0.95,
            'weight_kg': 2.0,
            'bin_id': bin_id,
            'idempotency_key': 'idem-key-full-test-999'
        }

        # 1. Initial request succeeds
        res1 = c.post('/api/v1/waste/events', json=original_payload)
        assert res1.status_code == 200
        assert res1.json()['duplicate'] is False
        orig_id = res1.json()['id']

        # 2. Fill the bin completely to capacity
        remaining = capacity - 2.0
        fill_res = c.post('/api/v1/waste/events', json={
            'object_name': 'capacity filler load',
            'category': cat,
            'confidence': 0.95,
            'weight_kg': remaining,
            'bin_id': bin_id,
            'idempotency_key': 'idem-filler-key-888'
        })
        assert fill_res.status_code == 200
        # Verify bin is 100% full
        bin_state = c.get(f'/api/v1/bins/{bin_id}').json()
        assert bin_state['fill_kg'] >= capacity

        # 3. Duplicate retry of original event on now-FULL bin must SUCCEED (idempotent)
        res_dup = c.post('/api/v1/waste/events', json=original_payload)
        assert res_dup.status_code == 200
        assert res_dup.json()['duplicate'] is True
        assert res_dup.json()['id'] == orig_id
        # Verify fill did not exceed capacity
        bin_state_after = c.get(f'/api/v1/bins/{bin_id}').json()
        assert bin_state_after['fill_kg'] == capacity

        # 4. Reusing the key with a different payload on full bin returns 409 (different payload)
        different_payload = dict(original_payload)
        different_payload['weight_kg'] = 3.0
        res_conflict = c.post('/api/v1/waste/events', json=different_payload)
        assert res_conflict.status_code == 409
        assert 'different waste event' in res_conflict.json()['detail']

        # 5. New request on full bin returns 409 (capacity exceeded)
        res_overcap = c.post('/api/v1/waste/events', json={
            'object_name': 'extra item',
            'category': cat,
            'confidence': 0.95,
            'weight_kg': 1.0,
            'bin_id': bin_id,
            'idempotency_key': 'brand-new-key-777'
        })
        assert res_overcap.status_code == 409
        assert 'Selected bin no longer has enough capacity' in res_overcap.json()['detail']

