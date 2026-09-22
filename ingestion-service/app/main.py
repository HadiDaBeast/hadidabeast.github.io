"""
ingestion-service — FastAPI application entry point.

Endpoints
---------
POST  /fetch         Trigger a new ingestion run (returns 202 or 409)
GET   /fetch/status  Return current/last job state
GET   /healthz       Health check

Advisory lock: before writing any offer rows the background thread acquires
pg_try_advisory_lock(42) so that multiple replicas cannot run ingestion
simultaneously.
"""

import logging
import os
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import db
from app.ingestion import run_ingestion
from app.willys import run_willys_ingestion

# ---------------------------------------------------------------------------
# Bootstrap — load .env before reading os.environ
# ---------------------------------------------------------------------------

load_dotenv()

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
APP_VERSION = os.environ.get("APP_VERSION", "dev")
INGESTION_PORT = int(os.environ.get("INGESTION_PORT", "8000"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")
ETILBUDSAVIS_API_URL = os.environ.get(
    "ETILBUDSAVIS_API_URL",
    "https://api.etilbudsavis.dk/v2/offers/search",
)
WILLYS_API_URL = os.environ.get(
    "WILLYS_API_URL",
    "https://api.etilbudsavis.dk/v2/offers",
)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Job state
# ---------------------------------------------------------------------------


@dataclass
class JobState:
    run_id: int
    status: str  # queued | running | succeeded | failed
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    offers_stored: Optional[int] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "offers_stored": self.offers_stored,
            "error": self.error,
        }


current_job: Optional[JobState] = None
job_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

ADVISORY_LOCK_KEY = 42


def _background_worker(run_id: int) -> None:
    """
    Run both ingestion jobs inside a PostgreSQL advisory lock.

    If another replica already holds the lock (pg_try_advisory_lock returns
    False) the job is marked as failed immediately.
    """
    global current_job  # noqa: PLW0603

    # Mark DB row as started
    try:
        db.mark_ingestion_run_started(run_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not mark run started (run_id=%s): %s", run_id, exc)

    with job_lock:
        if current_job:
            current_job.status = "running"
            current_job.started_at = datetime.now(timezone.utc).isoformat()

    # Acquire advisory lock using a dedicated connection
    lock_conn = None
    lock_acquired = False
    try:
        lock_conn = db._pool.getconn()  # type: ignore[union-attr]
        lock_conn.autocommit = True
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
            lock_acquired = cur.fetchone()[0]

        if not lock_acquired:
            msg = "Another ingestion is already running"
            logger.warning("Advisory lock not acquired for run_id=%s: %s", run_id, msg)
            _fail_job(run_id, msg)
            return

        # Run both ingestion functions
        total_stored = 0
        all_errors = 0

        try:
            result = run_ingestion(db._pool, ETILBUDSAVIS_API_URL, run_id)
            total_stored += result.get("offers_stored", 0)
            all_errors += result.get("errors", 0)
        except Exception as exc:  # noqa: BLE001
            logger.error("run_ingestion raised for run_id=%s: %s", run_id, exc)
            all_errors += 1

        try:
            w_result = run_willys_ingestion(db._pool, WILLYS_API_URL, run_id)
            total_stored += w_result.get("offers_stored", 0)
            all_errors += w_result.get("errors", 0)
        except Exception as exc:  # noqa: BLE001
            logger.error("run_willys_ingestion raised for run_id=%s: %s", run_id, exc)
            all_errors += 1

        completed_at = datetime.now(timezone.utc).isoformat()
        try:
            db.mark_ingestion_run_succeeded(run_id, total_stored)
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not mark run succeeded (run_id=%s): %s", run_id, exc)

        with job_lock:
            if current_job and current_job.run_id == run_id:
                current_job.status = "succeeded"
                current_job.completed_at = completed_at
                current_job.offers_stored = total_stored
                current_job.error = (
                    f"{all_errors} error(s) during ingestion" if all_errors else None
                )

        logger.info(
            "Job succeeded: run_id=%s offers_stored=%s errors=%s",
            run_id, total_stored, all_errors,
        )

    except Exception as exc:  # noqa: BLE001
        logger.error("Unhandled error in background worker (run_id=%s): %s", run_id, exc)
        _fail_job(run_id, str(exc))

    finally:
        # Always release the advisory lock and return the connection
        if lock_conn is not None:
            try:
                if lock_acquired:
                    with lock_conn.cursor() as cur:
                        cur.execute(
                            "SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,)
                        )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not release advisory lock: %s", exc)
            try:
                db._pool.putconn(lock_conn)  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not return lock conn to pool: %s", exc)


def _fail_job(run_id: int, message: str) -> None:
    """Mark a job as failed both in memory and in the database."""
    global current_job  # noqa: PLW0603
    completed_at = datetime.now(timezone.utc).isoformat()
    try:
        db.mark_ingestion_run_failed(run_id, message)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not mark run failed (run_id=%s): %s", run_id, exc)

    with job_lock:
        if current_job and current_job.run_id == run_id:
            current_job.status = "failed"
            current_job.completed_at = completed_at
            current_job.error = message


# ---------------------------------------------------------------------------
# FastAPI lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise DB pool on startup; close it on shutdown."""
    if not DATABASE_URL:
        logger.warning("DATABASE_URL is not set — DB features will be unavailable")
    else:
        try:
            db.init_pool(DATABASE_URL, retry_timeout=30)
            db.init_schema()
            logger.info("Schema initialised successfully")
        except Exception as exc:  # noqa: BLE001
            logger.error("DB startup failed: %s", exc)
            raise

    yield

    # Shutdown: close the connection pool
    if db._pool is not None:
        try:
            db._pool.closeall()
            logger.info("DB pool closed")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error closing DB pool: %s", exc)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ingestion-service",
    version=APP_VERSION,
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/healthz", status_code=200)
def healthz():
    return {"status": "ok", "version": APP_VERSION}


@app.post("/fetch", status_code=202)
def trigger_fetch():
    """
    Trigger a new ingestion run.

    Returns 202 if queued successfully, 409 if another job is running.
    """
    global current_job  # noqa: PLW0603

    with job_lock:
        if current_job is not None and current_job.status == "running":
            return JSONResponse(
                status_code=409,
                content={
                    "error": "A job is already running",
                    "run_id": current_job.run_id,
                    "status": current_job.status,
                },
            )

        # Create DB row first so we have an ID
        try:
            run_id = db.create_ingestion_run()
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not create ingestion run: %s", exc)
            return JSONResponse(
                status_code=503,
                content={"error": "Database unavailable"},
            )

        current_job = JobState(run_id=run_id, status="queued")

    # Start background thread outside the lock
    thread = threading.Thread(
        target=_background_worker,
        args=(run_id,),
        daemon=True,
        name=f"ingestion-worker-{run_id}",
    )
    thread.start()
    logger.info("Ingestion job queued: run_id=%s", run_id)

    return JSONResponse(
        status_code=202,
        content={"run_id": run_id, "status": "queued"},
    )


@app.get("/fetch/status")
def fetch_status():
    """Return the current job state, or {status: 'idle'} if none."""
    with job_lock:
        if current_job is None:
            return {"status": "idle"}
        return current_job.to_dict()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=INGESTION_PORT)
