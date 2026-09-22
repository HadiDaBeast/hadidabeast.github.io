# Prispulsen — Deployment Decision Record

This document records all architectural and deployment decisions made for the
migration of Prispulsen from a static GitHub Pages site to a Kubernetes-based
microservices architecture.

---

## D01 — Container Registry

**Decision:** Docker Hub, username `taylorbourne`  
**Date:** 2026-09-22  
**Status:** Accepted

**Context:** The project is a small-scale personal/hobbyist application. A paid
managed registry (ECR, GCR, ACR) adds cost and operational overhead that is not
justified at this scale.

**Consequences:**
- Image names follow the pattern `taylorbourne/<service>:<tag>`
- Free tier rate limits apply (100 pulls/6 h for unauthenticated). Kubernetes
  nodes should be configured with a `imagePullSecret` referencing Docker Hub
  credentials to avoid pull failures.

---

## D02 — Image Tag Policy

**Decision:** Commit SHA + semver, e.g. `v1.0.0-abc1234`  
**Date:** 2026-09-22  
**Status:** Accepted

**Context:** Using `:latest` makes rollbacks ambiguous and breaks GitOps
auditability. Pure semver tags require manual tagging discipline. Combining
semver with the short commit SHA gives both human-readable release versions and
an exact pointer back to source.

**Format:** `v<MAJOR>.<MINOR>.<PATCH>-<7-char-SHA>`  
**Example:** `taylorbourne/api-service:v1.0.0-abc1234`

**Consequences:**
- CI/CD pipeline must compute both the semver and the SHA and compose the tag.
- Kubernetes manifests in `k8s/` reference explicit image tags; no `latest`.
- Rolling back to a prior version is trivially auditable via `git log`.

---

## D03 — Ingress Controller

**Decision:** `ingress-nginx`  
**Date:** 2026-09-22  
**Status:** Accepted

**Context:** `ingress-nginx` is the most widely deployed community ingress
controller, has excellent documentation, and supports path-based routing,
TLS termination, and custom headers out of the box.

**Consequences:**
- The cluster must have the `ingress-nginx` controller installed before deploying
  Ingress resources.
- TLS termination happens at the ingress layer; services communicate internally
  over plain HTTP.
- Annotations use `nginx.ingress.kubernetes.io/*` namespace.

---

## D04 — Public Hostname

**Decision:** `prispulsen.nu` (production), `localhost` (local dev)  
**Date:** 2026-09-22  
**Status:** Accepted

**Context:** The domain `prispulsen.nu` is the existing public hostname used
when the site ran on GitHub Pages.

**Consequences:**
- Ingress manifests set `host: prispulsen.nu`.
- TLS certificate issued via cert-manager (Let's Encrypt) for `prispulsen.nu`.
- Local development uses `kubectl port-forward` or a `localhost` Ingress
  override — no DNS changes needed for dev work.

---

## D05 — Python Web Framework

**Decision:** FastAPI (replaces Flask for new microservices)  
**Date:** 2026-09-22  
**Status:** Accepted

**Context:** The original `app.py` used Flask. FastAPI offers automatic
OpenAPI/Swagger documentation, native async support, Pydantic-based request
validation, and better performance under concurrent load — all relevant for a
publicly exposed API service.

**Consequences:**
- `api-service` and `ingestion-service` are built on FastAPI + uvicorn.
- The existing `app.py` (Flask) is retained as-is for local legacy reference
  but is not containerised.
- No Flask dependency in service `requirements.txt` files.

---

## D06 — Database

**Decision:** PostgreSQL (replaces SQLite)  
**Date:** 2026-09-22  
**Status:** Accepted

**Context:** SQLite is file-based and cannot be shared across multiple pods.
PostgreSQL is the natural next step: it supports `JSONB`, `TIMESTAMPTZ`,
concurrent connections, and `ON CONFLICT DO NOTHING` upserts — all used by
this project.

**Consequences:**
- A PostgreSQL instance must be provisioned (e.g. managed cloud DB, or a
  Kubernetes StatefulSet for dev).
- All services connect via `DATABASE_URL` (see `docs/configuration.md`).
- Existing SQLite data is migrated once using `scripts/migrate_sqlite.py`.

---

## D07 — DB Migrations

**Decision:** Plain versioned SQL files, no Alembic  
**Date:** 2026-09-22  
**Status:** Accepted

**Context:** Alembic adds a Python dependency and an abstraction layer on top
of raw SQL. For a schema that will change rarely, the overhead is not
justified. Plain `.sql` files are readable by anyone, runnable with `psql`,
and simple to version in git.

**Format:** `migrations/NNN_description.sql` where NNN is a zero-padded
integer (e.g. `001_initial_schema.sql`).  
**Runner:** `migrations/run_migrations.py` tracks applied versions in the
`schema_version` table and skips already-applied files.

**Consequences:**
- Schema changes require a new numbered `.sql` file.
- The migration runner must be executed before deploying new code that depends
  on schema changes (typically as a Kubernetes Job or init container).

---

## D08 — Service Decomposition

**Decision:** Three services — `ingestion-service`, `api-service`, `frontend`  
**Date:** 2026-09-22  
**Status:** Accepted

**Rationale for three (not four) services:**  
The original `fetch_prices.py` (Tjek API, 7 stores) and `fetch_willys.py`
(Willys dealer endpoint) both write to the same `price_history` table and
share identical domain logic. Splitting them would duplicate the DB access
layer and the FastAPI app boilerplate for no meaningful boundary benefit.
Willys ingestion therefore lives in `ingestion-service` as a separate module
(`app/willys.py`), triggered by the same scheduled endpoint.

| Service | Responsibility |
|---|---|
| `ingestion-service` | Fetches offers from Tjek API and Willys; writes to PostgreSQL; exposes `/ingest` trigger and `/health` endpoints |
| `api-service` | Read-only REST API over price history data; serves the frontend via JSON |
| `frontend` | nginx serving static HTML/CSS/JS; proxies `/api/*` to `api-service` |

---

## D09 — Scheduling

**Decision:** Kubernetes CronJob for ingestion (weekly)  
**Date:** 2026-09-22  
**Status:** Accepted

**Context:** The original project ran `fetch_prices.py` manually or via a
GitHub Actions `schedule`. In Kubernetes, a `CronJob` is the idiomatic
replacement.

**Consequences:**
- A `CronJob` manifest in `k8s/` schedules a POST to `ingestion-service`
  once per week.
- The CronJob can also be triggered manually with `kubectl create job`.

---

## D10 — Configuration Management

**Decision:** Environment variables for all runtime configuration  
**Date:** 2026-09-22  
**Status:** Accepted

**Context:** The twelve-factor app methodology and Kubernetes `ConfigMap` /
`Secret` resources both align with env-var-based configuration. No config
files are baked into images.

**Consequences:**
- All required and optional env vars are documented in `docs/configuration.md`.
- Secrets (DB password) are stored in Kubernetes `Secret` objects and injected
  as env vars at runtime.
- `python-dotenv` is included in requirements so `.env` files work for local
  development.
