# WasteWise AI

**Smart Waste Management Digital Twin** — a completely software-based hackathon solution for PS01. WasteWise connects a local computer-vision pipeline, persisted waste events, a stateful digital twin, grounded analytics, deterministic what-if simulation, and optional GenAI recommendations.

## PS01 mapping
- **Computer Vision:** `/api/v1/waste/detect` accepts an uploaded image, decodes it with OpenCV, maps a configured model-ready result to a waste category, and exposes confidence. The demo path is explicitly labeled and never claims sensor data.
- **Digital Twin:** SQLite-backed virtual locations, bins, events, collections, recycling events, alerts, and simulations. `/digital-twin` is interactive and bin selection shows state.
- **Segregation:** category composition and contamination signals are computed from persisted events.
- **Recycling:** recovered/input efficiency is calculated from recycling events.
- **Collection:** transparent priority score uses 60% fill + 8× accumulation + contamination.
- **Prediction:** overflow hours are derived from remaining capacity and accumulation rate.
- **Simulation:** deterministic before/after model for collection frequency and segregation improvement.
- **GenAI:** dedicated service sends structured Digital Twin context to a configured provider; without a key it uses a clearly labeled grounded rules fallback.

## Architecture
```
frontend (Next.js + TypeScript + React + CSS)
    ↓ HTTP / JSON
backend (FastAPI + Pydantic)
    ↓
services: CV · Digital Twin · Simulation · GenAI
    ↓
SQLite via SQLAlchemy
```

## Structure
- `backend/app/main.py` — FastAPI routes and composition of services
- `backend/app/models.py` — SQLAlchemy models
- `backend/app/seed.py` — deterministic simulated demo data
- `backend/app/services/computer_vision/` — image validation and model seam
- `backend/app/services/digital_twin/` — fill, priority, and overflow formulas
- `backend/app/services/simulation/` — documented deterministic counterfactual engine
- `backend/app/services/genai/` — optional provider + safe grounded fallback
- `frontend/src/app/page.tsx` — responsive dashboard pages and interactions

## Run locally
### Backend
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app.main:app --reload --port 8000
```
Open Swagger at `http://127.0.0.1:8000/docs`.

### Frontend
```bash
cd frontend
npm install
npm run dev
```
Open `http://localhost:3000`.

## Demo mode
The database is initialized and seeded on first backend startup. Seeded detection, collection, sorting, recycling, and flow events are software-generated and labeled as SIMULATED DEMO DATA; all dashboard, flow, recycling, contamination, and collection metrics are calculated from those records. To explicitly request seed initialization:
```bash
curl -X POST http://127.0.0.1:8000/api/v1/demo/seed
```

## Computer vision setup
The CV pipeline is split into OpenCV preprocessing, a `ComputerVisionModel` adapter, YOLO inference, and explicit category mapping. When `MODEL_PATH` points to an available Ultralytics YOLO weights file, `/api/v1/waste/detect` runs real local inference and returns `LIVE CV` detections. When no model is configured, it only validates/decodes the image and returns `DEMO CV MODE — MODEL NOT CONFIGURED` with no filename-based classification and no false learned result. Invalid images return a useful 400 response.

## GenAI setup
Set `GENAI_API_KEY`, `GENAI_BASE_URL`, and `GENAI_MODEL` in `backend/.env`. The service sends only structured application context and instructs the model not to invent bin values, quantities, times, percentages, or predictions. Missing or unavailable providers do not crash the application; the UI displays `DEMO / GROUNDED RULES` or `DEMO / FALLBACK`.

## Environment variables
`DATABASE_URL`, `GENAI_API_KEY`, `GENAI_BASE_URL`, `GENAI_MODEL`, `MODEL_PATH`, `CV_CONFIDENCE`, `CV_IOU`, `CV_IMAGE_SIZE`, `CORS_ORIGINS`, and `DEMO_MODE`. No keys are hardcoded.

## Tests
```bash
cd backend
source .venv/bin/activate
pytest -q
cd ../frontend
npm run build
```

## API highlights
`GET /health`, `GET /api/v1/dashboard`, `GET /api/v1/bins`, `GET /api/v1/bins/{id}`, `POST /api/v1/waste/detect`, `POST /api/v1/waste/events`, `GET /api/v1/digital-twin/state`, `GET /api/v1/digital-twin/flow`, `POST /api/v1/simulation/run`, `POST /api/v1/ai/analyze`, `POST /api/v1/ai/chat`, `POST /api/v1/ai/report`, and `POST /api/v1/demo/seed`.

## Known limitations
This plain-local build intentionally ships with deterministic simulated data and a model-ready OpenCV image seam so it is reliable offline. A YOLO weights file and GenAI key are optional additions; the application clearly distinguishes learned/live services from demo fallbacks. Authentication is intentionally omitted for the hackathon MVP.

## Exact model setup

1. Download or train a YOLO weights file suitable for waste classes (for example, a custom `waste_yolo.pt`) and place it outside the repository.
2. Set `MODEL_PATH=C:\models\waste_yolo.pt` on Windows or `MODEL_PATH=/absolute/path/waste_yolo.pt` on Linux/macOS in `backend/.env`.
3. Restart FastAPI. The scanner status changes from `DEMO CV MODE — MODEL NOT CONFIGURED` to `LIVE CV` only after the adapter loads the file.
4. The model class labels are mapped in `backend/app/services/computer_vision/category_mapper.py`.

The repository intentionally does not bundle large weights.

## Windows commands
```powershell
cd wastewise-ai\backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
In a second PowerShell window:
```powershell
cd wastewise-ai\frontend
npm install
npm run dev
```

## GenAI provider setup
Set `GENAI_API_KEY`, `GENAI_BASE_URL`, and `GENAI_MODEL` in `backend/.env`. The service sends structured current Digital Twin context under `bins`, `waste_statistics`, `recycling_statistics`, `collection_statistics`, `contamination`, and `overflow_predictions`. Missing keys use `DEMO / GROUNDED RULES`; provider failures use `DEMO / FALLBACK`.

## Dynamic operations

The current project supports database-backed operations:

- `POST /api/v1/bins` creates a virtual bin for an existing location.
- `POST /api/v1/collections` records the current bin fill, resets the bin, updates status, and writes a collection flow event.
- `POST /api/v1/sorting` records sorted and rejected quantities and writes a sorting flow event.
- `POST /api/v1/recycling` validates input/recovered quantities, calculates efficiency, and writes recycling/recovery/disposal flow events.

Dashboard analytics use a labeled **LAST 7 DAYS** reporting window. All seeded values are labeled **SIMULATED DEMO DATA** and are calculated from persisted events rather than stored dashboard totals. Empty or insufficient history is reported as `INSUFFICIENT DATA` for overflow prediction.

## Final PS01 completion notes

- The Waste Flow API returns `window: LAST_7_DAYS`, `stage`, `quantity_kg`, `event_count`, and `percentage` from persisted flow events.
- The seeded demo is mass-balanced: detection 173 kg, segregation 153.9 kg, collection 80 kg, sorting 70 kg, recycling 60 kg, recovery 50 kg, and disposal 10 kg. These are software-generated **SIMULATED DEMO DATA**, not sensor readings.
- Sorting requires a selected persisted collection batch and rejects input above its remaining available quantity.
- Recycling requires available persisted sorted material and rejects input above the remaining sorted quantity. Recovered material cannot exceed processed input.
- The scanner displays every YOLO detection, supports multi-selection, shows bounding boxes and `AI ESTIMATED WEIGHT`, allows weight edits, assigns a bin per selected detection, and creates one WasteEvent per confirmed selection.
- The weekly dashboard chart is derived from persisted daily WasteEvents; no static chart values are used.
- What-if results are labeled `SIMULATED WHAT-IF RESULT` and provide `before`, `after`, and `delta` values.
- Final acceptance suite: **32 passed**. Frontend: `npm install && npm run build` succeeded.
- BIN-01's seeded 80 kg collection is backed by timestamped pre-collection WasteEvents; current fill is derived from post-collection events. The source event IDs are recorded in the existing `Collection.notes` field, so no schema migration is required.

## AI Scanner camera workflow

The existing AI Scanner now supports both modes:

- **LIVE CAMERA** — browser webcam through `navigator.mediaDevices.getUserMedia({ video: true, audio: false })`
- **UPLOAD IMAGE** — existing multipart upload fallback using `/api/v1/waste/detect`

Camera controls:

- Start Camera
- Capture & Scan
- Stop Camera

Camera errors such as denied permission, missing camera, camera-in-use, unsupported browser, invalid capture, and backend failures are shown without crashing the page. All stream tracks are stopped when the scanner unmounts.

Model health is available at:

```text
GET /api/v1/cv/status
```

## YOLO setup

Set the backend model path to a real Ultralytics-compatible weights file. On Windows:

```text
MODEL_PATH=C:\models\waste_yolo.pt
```

On Linux/macOS, use an absolute path such as:

```text
MODEL_PATH=/models/waste_yolo.pt
```

Without a valid model, the backend and UI remain explicitly labeled:

```text
DEMO CV MODE — MODEL NOT CONFIGURED
```

No fallback detections are fabricated.

## Exact validation commands

From the project root:

```bash
python -m pytest backend/tests -v
cd frontend
npm install
npm run build
```

## PS01 hackathon implementation notes

### Problem and solution
WasteWise AI turns campus waste operations into a software-only, stateful operating model. Computer Vision sees waste items, the application persists confirmed WasteEvents, the Digital Twin estimates occupancy from those events, and the flow tracks collection, sorting, recycling, recovery, and disposal.

### Why it matters
Managers can see which virtual bin needs attention, why it is prioritized, how contamination affects streams, what material remains available upstream, and which deterministic what-if scenario could improve operations.

### 3D Digital Twin
The Digital Twin page uses procedural React Three Fiber geometry: campus ground, Academic Block, Cafeteria, Library, Hostel/Residential Area, Administration, virtual bins, collection, sorting, recycling, recovery, and disposal facilities, plus directional flow paths. Orbit, zoom, pan, reset-camera, selectable bins, hover-style labels, label/building/path toggles, status legend, and a selected-bin side panel are included. Bin geometry uses the current SQLite-backed fill percentage and status.

The scene is explicitly labeled **DIGITAL TWIN · ESTIMATED**. It represents software events and estimated occupancy; it is not a physical sensor or real-time physical fill measurement.

### Computer Vision diagnostics
The YOLO adapter uses configurable environment variables:

```text
MODEL_PATH=backend/models/best.pt
CV_CONFIDENCE=0.10
CV_IOU=0.45
CV_IMAGE_SIZE=640
```

`GET /api/v1/cv/status` reports model configuration, load status, path status, raw model classes, confidence threshold, IoU, image size, inference readiness, model name, and load errors. Each detection includes a detection ID, class name, confidence, actual box coordinates, mapped application category, recyclable flag, deterministic AI estimated weight, and model source.

The supplied model taxonomy is supported through the existing mapper: `banana-peel` and `orange-peel` map to Organic; glass, metal, paper, and plastic map to their corresponding categories; styrofoam maps to Other. No filename classification or fallback detections are used.

### Contamination
The Analytics page includes persisted contamination-by-stream results, affected bins, recommended action, and an honest `INSUFFICIENT DATA` trend state when there is not enough history.

### What-if simulation
The deterministic simulator compares **CURRENT STATE** with **SIMULATED WHAT-IF RESULT** for collection frequency, segregation improvement, and recycling/recovery improvement. It is a counterfactual model, not a real-world guarantee.

### Demo labels
- **LIVE CV** — actual configured YOLO inference.
- **DEMO CV MODE** — no trained model is configured; no learned detection is claimed.
- **DIGITAL TWIN · ESTIMATED** — persisted software state and estimated occupancy.
- **SIMULATED DEMO DATA** — seeded demonstration records.
- **SIMULATED WHAT-IF RESULT** — deterministic counterfactual output.
- **DEMO / GROUNDED RULES** or **DEMO / FALLBACK** — GenAI unavailable or fallback mode.

### Recommended 3–5 minute demo
1. Open the Manager Dashboard and show the 3D Digital Twin.
2. Open AI Scanner and start the webcam.
3. Capture a frame or upload an image.
4. Show actual YOLO class, confidence, bounding box, threshold, processing time, and estimated weight.
5. Select one or more detections, assign virtual bins, and add them to the Digital Twin.
6. Return to Digital Twin and show the selected bin's estimated occupancy, contamination, priority, and overflow prediction.
7. Record collection, then show the updated flow.
8. Record sorting and recycling/recovery.
9. Run a simulation comparing current and simulated state.
10. Ask the grounded assistant which action the manager should take now.

### Software-only limitation and future deployment
This MVP has no Arduino, ESP32, Raspberry Pi, ultrasonic sensor, RFID, MQTT hardware, or physical bin integration. The webcam is only the Computer Vision input. Future fixed-camera or sensor deployments are architectural possibilities, not current measurements.

### Known limitations
A real YOLO weights file is required for LIVE CV. The bundled source does not expose API keys or large model weights. Browser webcam permission depends on the user's browser/device. The Digital Twin is intentionally lightweight and procedural for ordinary hackathon laptops.
