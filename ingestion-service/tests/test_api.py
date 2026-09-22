"""
API tests for ingestion-service (FastAPI).

Uses FastAPI TestClient (synchronous) so that threading behaviour works
correctly without needing an asyncio event loop in tests.
"""

import threading
import pytest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app, JobState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_job_state():
    """Reset global job state before each test to avoid cross-test pollution."""
    with main_module.job_lock:
        main_module.current_job = None
    yield
    with main_module.job_lock:
        main_module.current_job = None


@pytest.fixture()
def client():
    """TestClient with lifespan disabled (DB not required for unit tests)."""
    # Override lifespan so we don't need a real DB in unit tests
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _noop_lifespan(app):
        yield

    app.router.lifespan_context = _noop_lifespan
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------


class TestHealthz:
    def test_healthz_returns_200(self, client):
        """GET /healthz always returns 200 with status=ok."""
        resp = client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "version" in body

    def test_healthz_has_version(self, client):
        """GET /healthz includes a version key."""
        resp = client.get("/healthz")
        assert "version" in resp.json()


# ---------------------------------------------------------------------------
# /fetch/status
# ---------------------------------------------------------------------------


class TestFetchStatus:
    def test_fetch_status_when_idle(self, client):
        """GET /fetch/status returns idle-like state when no job has run."""
        resp = client.get("/fetch/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") == "idle"

    def test_fetch_status_reflects_running_job(self, client):
        """GET /fetch/status returns job details when a job is set."""
        job = JobState(run_id=99, status="running")
        with main_module.job_lock:
            main_module.current_job = job

        resp = client.get("/fetch/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "running"
        assert body["run_id"] == 99

    def test_fetch_status_reflects_succeeded_job(self, client):
        """GET /fetch/status returns succeeded job details."""
        job = JobState(
            run_id=7,
            status="succeeded",
            offers_stored=42,
            completed_at="2026-09-22T12:00:00+00:00",
        )
        with main_module.job_lock:
            main_module.current_job = job

        resp = client.get("/fetch/status")
        body = resp.json()
        assert body["status"] == "succeeded"
        assert body["offers_stored"] == 42


# ---------------------------------------------------------------------------
# POST /fetch
# ---------------------------------------------------------------------------


class TestTriggerFetch:
    def test_fetch_triggers_job(self, client):
        """POST /fetch returns 202 and queues a job (background thread mocked)."""
        with patch("app.main.db.create_ingestion_run", return_value=123) as mock_create, \
             patch("threading.Thread") as mock_thread_cls:

            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread

            resp = client.post("/fetch")

        assert resp.status_code == 202
        body = resp.json()
        assert body["run_id"] == 123
        assert body["status"] == "queued"
        mock_create.assert_called_once()
        mock_thread.start.assert_called_once()

    def test_fetch_sets_current_job(self, client):
        """POST /fetch sets current_job with the new run_id."""
        with patch("app.main.db.create_ingestion_run", return_value=55), \
             patch("threading.Thread") as mock_thread_cls:

            mock_thread_cls.return_value = MagicMock()
            client.post("/fetch")

        with main_module.job_lock:
            assert main_module.current_job is not None
            assert main_module.current_job.run_id == 55


class TestFetchRejectsConcurrent:
    def test_fetch_rejects_concurrent(self, client):
        """POST /fetch returns 409 if a job is already running."""
        # Directly inject a running job into global state
        running_job = JobState(run_id=77, status="running")
        with main_module.job_lock:
            main_module.current_job = running_job

        with patch("app.main.db.create_ingestion_run") as mock_create:
            resp = client.post("/fetch")

        assert resp.status_code == 409
        body = resp.json()
        assert "error" in body
        # DB row must NOT have been created for the rejected request
        mock_create.assert_not_called()

    def test_fetch_allows_new_job_after_completion(self, client):
        """POST /fetch returns 202 if the previous job has succeeded."""
        done_job = JobState(run_id=10, status="succeeded")
        with main_module.job_lock:
            main_module.current_job = done_job

        with patch("app.main.db.create_ingestion_run", return_value=11), \
             patch("threading.Thread") as mock_thread_cls:

            mock_thread_cls.return_value = MagicMock()
            resp = client.post("/fetch")

        assert resp.status_code == 202

    def test_fetch_allows_new_job_after_failure(self, client):
        """POST /fetch returns 202 if the previous job failed."""
        failed_job = JobState(run_id=20, status="failed", error="oops")
        with main_module.job_lock:
            main_module.current_job = failed_job

        with patch("app.main.db.create_ingestion_run", return_value=21), \
             patch("threading.Thread") as mock_thread_cls:

            mock_thread_cls.return_value = MagicMock()
            resp = client.post("/fetch")

        assert resp.status_code == 202
