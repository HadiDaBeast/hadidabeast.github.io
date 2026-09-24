import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import db
from app.ingestion import run_ingestion
from app.willys import run_willys_ingestion

load_dotenv()

APP_VERSION = os.environ.get("APP_VERSION", "dev")
INGESTION_PORT = int(os.environ.get("INGESTION_PORT", "8000"))
ETILBUDSAVIS_API_URL = os.environ.get(
    "ETILBUDSAVIS_API_URL",
    "https://api.etilbudsavis.dk/v2/offers/search",
)
WILLYS_API_URL = os.environ.get(
    "WILLYS_API_URL",
    "https://api.etilbudsavis.dk/v2/offers",
)

@dataclass
class JobState:
    run_id: int
    status: str
    started_at = None
    completed_at = None
    offers_stored = None
    error = None

    def to_dict(self):
        return {
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "offers_stored": self.offers_stored,
            "error": self.error,
        }


current_job = None


def _background_worker(run_id):
    global current_job

    db.mark_ingestion_run_started(run_id)

    if current_job:
        current_job.status = "running"
        current_job.started_at = datetime.now(timezone.utc).isoformat()

    total_stored = 0
    all_errors = 0

    result = run_ingestion(ETILBUDSAVIS_API_URL, run_id)
    total_stored += result.get("offers_stored", 0)
    all_errors += result.get("errors", 0)

    w_result = run_willys_ingestion(WILLYS_API_URL, run_id)
    total_stored += w_result.get("offers_stored", 0)
    all_errors += w_result.get("errors", 0)

    completed_at = datetime.now(timezone.utc).isoformat()
    db.mark_ingestion_run_succeeded(run_id, total_stored)

    if current_job and current_job.run_id == run_id:
        current_job.status = "succeeded"
        current_job.completed_at = completed_at
        current_job.offers_stored = total_stored
        current_job.error = f"{all_errors} error(s) during ingestion" if all_errors else None


app = FastAPI(title="ingestion-service", version=APP_VERSION)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": APP_VERSION}


@app.post("/fetch", status_code=202)
def trigger_fetch():
    global current_job

    run_id = db.create_ingestion_run()
    current_job = JobState(run_id=run_id, status="queued")

    thread = threading.Thread(
        target=_background_worker,
        args=(run_id,),
        daemon=True,
        name=f"ingestion-worker-{run_id}",
    )
    thread.start()

    return JSONResponse(status_code=202, content={"run_id": run_id, "status": "queued"})


@app.get("/fetch/status")
def fetch_status():
    if current_job is None:
        return {"status": "idle"}
    return current_job.to_dict()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=INGESTION_PORT)
