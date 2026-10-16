# WasteWise AI — PS01 Smart Waste Management Digital Twin

An enhancement of the supplied project—not a rebuild. WasteWise connects the bundled YOLO model, user-confirmed waste events, existing campus bins, SQLite-backed Twin state, deterministic what-if simulation, and a grounded operations copilot. It is software-only: the campus assets and bin readings are virtual/software-derived; there is no physical sensor or hardware integration.

## Integrated flow

```text
Camera or upload → OpenCV → bundled YOLO inference → show all detections
→ user selects exactly one item → backend ranks compatible existing bins
→ user confirms one bin → idempotent WasteEvent → SQLite/Twin/dashboard refresh
→ deterministic simulation → grounded GenAI explanation and suggested action
```

A detection or selection alone never creates an event. The scanner uses a stable idempotency key on confirmation; the backend validates stream compatibility and available capacity. The frontend displays the recommended bin, suitable alternatives, and why each candidate ranked. No new bin is created during scanning.

## Existing architecture retained

- **Frontend:** existing Next.js, React, TypeScript, CSS, React Three Fiber / Three.js.
- **Backend:** existing FastAPI, Pydantic, SQLAlchemy services.
- **Database:** existing project-root `wastewise.db`; SQLite. Relative legacy `sqlite:///./wastewise.db` configuration is resolved to this project database when the documented backend working directory is used.
- **CV:** existing OpenCV preprocessing and `backend/models/best.pt` Ultralytics YOLO weights, with real inference, model diagnostics, boxes, confidence, category mapping, recyclability, and estimated weight.
- **Twin:** existing database-backed APIs and procedural, lightweight campus scene.
- **Simulation/AI:** existing deterministic simulation engine and optional provider integration with a grounded rules fallback.

## Data preservation and initialization

Startup uses `create_all` and a safe additive SQLite migration: it adds a nullable unique idempotency key to `waste_events` and a unique index. Existing rows remain valid. The idempotent seed keeps existing locations, bins, events, collections, and other records; it adds missing semantic zones and campus bins until the existing campus register contains 30–40 virtual bins (36 in the supplied four-bin baseline). It seeds the original software-generated demonstration history only when the waste-event store is empty. Repeating startup or `POST /api/v1/demo/seed` does not duplicate seeded rows.

Seeded/demo records are not sensor measurements. Dashboard, analytics, recommendations, and AI context are derived from database rows and carry explicit software/demo or estimated labels. Missing measurements are reported as `INSUFFICIENT DATA` rather than invented.

## Run locally

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# Keep the supplied backend/.env and DATABASE_URL if already configured.
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

API documentation: <http://127.0.0.1:8000/docs>

### Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:3000>. The frontend API base defaults to `http://localhost:8000`; override with `NEXT_PUBLIC_API_URL` only when needed.

### Tests and build

```bash
cd backend
pytest -q
cd ../frontend
npx tsc --noEmit
npm run build
```

The pytest module always creates a private temporary SQLite database; it never drops, unlinks, or resets the supplied database.

## Computer Vision

The backend selects `backend/models/best.pt` automatically when `MODEL_PATH` is blank and the bundled file exists. The supplied YOLO classes are `banana-peel`, `glass`, `metal`, `orange-peel`, `paper`, `plastic`, and `styrofoam`. `/api/v1/cv/status` reports actual model load state, classes, thresholds, image size, and errors. The scanner keeps every real model detection visible, preselects none, and requires one explicit radio selection. If the configured model cannot load, image decoding remains available but the response clearly states that no learned classification was made; no fallback detections are fabricated.

Camera access is browser-provided (`getUserMedia`, video only); image upload remains a fallback. Browser permissions and HTTPS requirements apply outside localhost.

## Digital Twin and category flow

The existing `ThreeTwin.tsx` is a lightweight procedural campus overview inspired by DSU Harohalli—not an architectural replica. It renders all persisted bins, positioned across semantic zones. Labels are minimal by default; hover reveals bin ID/state, click selects a bin, and the side panel shows its database-derived fill, contamination, accumulation, collection, overflow, and priority details. Recommended/high-priority bins receive distinct visual emphasis. Capped DPR and demand-driven rendering keep the scene lightweight.

Waste flow is off by default and fetched from `/api/v1/digital-twin/flow?category=…` when enabled. Plastic/paper/glass/metal follow Collection → Sorting → Recycling; Organic follows Collection → Recovery; Other/non-recyclable follows Collection → Sorting → Disposal. Category-specific downstream quantities remain unavailable unless the stored records can support attribution; the UI does not infer them.

## Operations copilot and simulation

The **Waste Management Operations Copilot** offers Analyze Campus, Improve Segregation, Improve Recycling, Improve Collection, Generate Daily Action Plan, and Explain Simulation. Its context includes database-derived waste by category/location, bin fill/priority/overflow state, contamination, collections, sorting, recycling, and affected zones. Recommendations are organized around Observation, Evidence, Recommendation, and Expected Operational Effect. Live GenAI uses the configured provider; without a key or when it is unavailable, the clearly labeled grounded fallback uses supplied context and marks missing data.

The simulation engine—not GenAI—calculates before/after scenario values. The copilot explanation receives those values and distinguishes model estimates from stored measurements. Simulation values are deterministic counterfactuals, not guarantees or physical measurements.

## API highlights

Existing routes are preserved, including `/health`, `/api/v1/dashboard`, `/api/v1/bins`, `/api/v1/cv/status`, `/api/v1/waste/detect`, `/api/v1/waste/events`, `/api/v1/digital-twin/state`, `/api/v1/digital-twin/flow`, `/api/v1/simulation/run`, `/api/v1/ai/analyze`, `/api/v1/ai/chat`, and `/api/v1/ai/report`.

Added/extended routes: `POST /api/v1/bins/recommend` ranks existing compatible bins; the waste-event contract accepts optional `idempotency_key`; `GET /api/v1/digital-twin/flow?category=…` provides category-aware topology and honest quantity availability; `POST /api/v1/ai/explain-simulation` explains deterministic results.

## Recommended demo sequence

1. Open the dashboard and point out stored-state and demo/estimate labels.
2. Open AI Scanner; capture a camera frame or upload an image.
3. Show all real YOLO detections and verify none is preselected.
4. Select exactly one object; review its category, confidence, recyclability, and estimated weight.
5. Request recommendations; compare the best compatible existing bin with alternatives and reasons.
6. Confirm one existing bin. Verify one persisted event, refreshed dashboard, cleared selection, and scanner ready for another scan.
7. Open the Twin; inspect the highlighted bin and its detailed state. Enable a waste-flow category and compare Organic, Plastic, and Other paths.
8. Open the Operations Copilot and run segregation, recycling, collection, and daily-plan actions.
9. Run a what-if scenario, then use Explain with Operations Copilot; distinguish deterministic estimates from persisted observations.

## Software-only limitation

The project contains no Arduino, ESP32, Raspberry Pi, physical bins/sensors, MQTT hardware, authentication, or payment flows. The Twin is a software representation and the CV camera is only an image input.
