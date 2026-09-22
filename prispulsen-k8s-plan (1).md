# Prispulsen — Kubernetes Microservices Plan

## Context

Prispulsen (prispulsen.nu) currently runs as a fully static site: a Python fetch/export
pipeline writes to a committed SQLite DB, GitHub Actions runs the pipeline weekly, and
GitHub Pages serves the static JSON/HTML output. That architecture is simple and
near-zero-cost, but it doesn't run *on* anything — there's no server process, no API,
and nothing to deploy to Kubernetes.

This plan restructures the project into a small set of containerized microservices that
satisfy the assignment's deployment requirements, while keeping the core domain logic
(Tjek API fetch, price normalization, jämförpris calculation, product matching) mostly
intact — it just moves from a script run by cron into a service run by a Deployment.

## Requirements checklist

| # | Requirement | How this plan satisfies it |
|---|---|---|
| 1 | Deployable via Kubernetes | Manifests (Deployments, Services, Ingress, StatefulSet, PVC) for a local/managed cluster |
| 2 | ≥2 microservice types + a database | `ingestion-service`, `api-service`, `frontend` + PostgreSQL |
| 3 | Each microservice implements a REST API | All three app services expose HTTP/REST endpoints (see below) |
| 4 | Accessible from outside Kubernetes | Ingress (or NodePort/LoadBalancer) exposes `frontend` and `api-service` to a browser |
| 5 | Independent horizontal scaling | Each service is its own Deployment with its own `replicas`/HPA, no shared in-memory state |
| 6 | Images pushed to Docker Hub | `<dockerhub-user>/prispulsen-ingestion`, `-api`, `-frontend` |
| 7 | Database as a separate microservice | PostgreSQL runs as its own StatefulSet, not embedded in app pods |
| 8 | Persistent storage across restarts | PVC bound to the Postgres StatefulSet via a PersistentVolumeClaim template |
| 9 | Database doesn't need to scale | Postgres runs as a single replica (StatefulSet with `replicas: 1`) |

## Service breakdown

### 1. `ingestion-service`
Replaces `fetch_prices.py` / `ingest_willys_catalog.py`, run as a long-lived service instead
of a one-off script.

- **Responsibility:** pull weekly offers from the Tjek API for the 7 Karlskrona stores,
  normalize price/unit fields, dedupe, and upsert into Postgres. Willys catalog ingestion
  (via `willys-cli`) lives here too, or as a second internal job triggered the same way.
- **REST API:**
  - `POST /fetch` — trigger an ingestion run (replaces the GitHub Actions cron trigger)
  - `GET /fetch/status` — status/last-run info for the most recent ingestion job
  - `GET /healthz` — liveness/readiness probe target
- **Scaling note:** ingestion is inherently a "one active job at a time" workload, but the
  service itself can still run multiple replicas behind a Service (only the pod handling
  the active job does DB writes; others just serve status/health). If that's awkward to
  justify, running it with `replicas: 1` plus an HPA that *can* scale to N is still valid —
  the requirement is that scaling is possible and independent, not that it's always useful.
- **Trigger:** a Kubernetes `CronJob` calling `POST /fetch` weekly is a clean way to keep
  the "runs on Mondays" behavior without re-adding GitHub Actions as a dependency.

### 2. `api-service`
Replaces the static `data/*.json` exports. Serves price data to the frontend and to any
future client.

- **Responsibility:** read-only queries against Postgres — current prices, per-store
  filtering, product history, store metadata.
- **REST API:**
  - `GET /products` — current sale items across all stores
  - `GET /products/{id}/history` — full price history for one product, optional `?store=`
  - `GET /stores` — the 7 store list + metadata
  - `GET /healthz`
- **Scaling note:** stateless, read-heavy — the easiest of the three to horizontally scale;
  a good one to demo an HPA against (e.g. scale on CPU or request rate).

### 3. `frontend`
Replaces GitHub Pages. A small Nginx (or Node) container serving the existing HTML/CSS/JS,
configured to call `api-service` instead of fetching static JSON files.

- **Responsibility:** serve the static assets (receipt-style UI, JetBrains Mono/Inter
  fonts) and proxy `/api/*` calls to `api-service` (or call it directly cross-origin).
- **REST API:** minimal, but to satisfy "implements a REST API" cleanly, add:
  - `GET /healthz` — used by readiness/liveness probes
  - (optionally) `GET /version` — build/version info, trivial but a real REST endpoint
- **Scaling note:** stateless static server — scales trivially, good baseline example.

### 4. Database — PostgreSQL
SQLite doesn't fit this model well (no real concurrent-write story across pods, and it's
awkward with PVCs since it's usually a single file). Moving to PostgreSQL is the natural
fix and satisfies requirement 7/8/9 cleanly.

- Runs as a `StatefulSet` (`replicas: 1`) with a `volumeClaimTemplate` → PVC, so data
  survives pod restarts and node reschedules.
- Exposed only inside the cluster via a headless/ClusterIP `Service` (`postgres:5432`) —
  not reachable from outside Kubernetes.
- Migration note: the existing SQLite schema (`price_history` table, the
  `UNIQUE(store_name, product_name, price, valid_from, valid_until)` constraint) ports
  directly to Postgres; the `substr(valid_until, 1, 10) >= date('now')` SQLite workaround
  is no longer needed since Postgres handles ISO timestamp comparisons natively.

## Kubernetes objects (per service)

Each of `ingestion-service`, `api-service`, `frontend`:
- `Deployment` (own image, own `replicas`, resource requests/limits, `/healthz` probes)
- `Service` (ClusterIP internally; `frontend` and/or `api-service` also reachable via Ingress)
- `HorizontalPodAutoscaler` (independent per service — this is what satisfies requirement 5)

Cluster-wide:
- `Ingress` (or a single `LoadBalancer`/`NodePort` Service) routing:
  - `/` → `frontend`
  - `/api` → `api-service`
  (keeps only one external entry point, satisfying requirement 4 without exposing
  `ingestion-service` or Postgres publicly)
- `Secret` for Postgres credentials, mounted into `ingestion-service`, `api-service`, and
  the Postgres StatefulSet itself
- `ConfigMap` for shared non-secret config (Tjek store IDs, Postgres host/port/db name)

## Docker Hub

Build and push three images:
```
docker build -t <dockerhub-user>/prispulsen-ingestion:latest ./ingestion-service
docker build -t <dockerhub-user>/prispulsen-api:latest ./api-service
docker build -t <dockerhub-user>/prispulsen-frontend:latest ./frontend
docker push <dockerhub-user>/prispulsen-ingestion:latest
docker push <dockerhub-user>/prispulsen-api:latest
docker push <dockerhub-user>/prispulsen-frontend:latest
```
Postgres uses the official `postgres:16` image straight from Docker Hub — no build needed.

## Suggested repo layout

```
prispulsen/
├── ingestion-service/
│   ├── Dockerfile
│   ├── app.py            # fetch_prices.py + ingest_willys_catalog.py wrapped in a REST API
│   └── requirements.txt
├── api-service/
│   ├── Dockerfile
│   ├── app.py            # replaces export.py's role, but as live queries
│   └── requirements.txt
├── frontend/
│   ├── Dockerfile
│   └── (existing static HTML/CSS/JS)
└── k8s/
    ├── postgres-statefulset.yaml
    ├── postgres-service.yaml
    ├── ingestion-deployment.yaml
    ├── ingestion-service.yaml
    ├── ingestion-cronjob.yaml
    ├── api-deployment.yaml
    ├── api-service.yaml
    ├── frontend-deployment.yaml
    ├── frontend-service.yaml
    ├── ingress.yaml
    ├── configmap.yaml
    └── secret.yaml
```

## Open questions / next steps

- Decide whether `ingestion-service` should also absorb the Willys catalog scraping
  (Node.js `willys-cli`) as a second container in the same pod, or split it into a fourth
  microservice — either satisfies the "≥2 types" requirement, but a 4th service is a
  clean way to show a *different* tech stack (Node vs Python) if that's worth the effort.
- Pick an ingress controller (e.g. `ingress-nginx`) or confirm the course's cluster
  already provides one — otherwise a `NodePort`/`LoadBalancer` Service is the simpler
  fallback for requirement 4.
- Confirm HPA metrics source (`metrics-server` needs to be installed in-cluster for
  CPU-based autoscaling to work at all).

## Execution task board

Complete the tasks in order within each phase. Every implementation task has a paired
verification task; do not mark a task complete until its test passes and the result is
recorded in the pull request or deployment notes.

### Phase 0 — decisions and project foundation

- [ ] **T01 — Freeze deployment decisions.** Choose the Kubernetes target, ingress
  controller, Docker Hub username, image tag policy, public hostname, and whether
  Willys catalog ingestion stays in `ingestion-service` or becomes a fourth service.
  **Test:** review a checked-in deployment decision record and verify that every open
  question above has exactly one selected value.
- [ ] **T02 — Create the service and test layout.** Add `ingestion-service/`,
  `api-service/`, `frontend/`, `k8s/`, and a repository-level test strategy without
  moving unrelated legacy files until their replacements are covered.
  **Test:** run the repository test command and verify collection succeeds with at
  least one test module for each service.
- [ ] **T03 — Define configuration contracts.** Document required environment
  variables, defaults, secret names, database URL format, Tjek store configuration,
  API base URL, and version/build metadata.
  **Test:** start each service with a deliberately incomplete environment and verify it
  fails with a clear configuration error; start it with the documented values and
  verify startup succeeds.
- [ ] **T04 — Add quality gates.** Configure formatting, linting, unit tests, and a
  CI workflow that runs them for application and Kubernetes changes.
  **Test:** introduce a temporary failing assertion locally to confirm the CI command
  fails, then run the unmodified suite and verify it passes.

### Phase 1 — PostgreSQL persistence and migration

- [ ] **T05 — Port the schema to PostgreSQL.** Implement tables and indexes for price
  history, stores, products, and any ingestion-run/status data needed by the APIs;
  preserve the uniqueness and history semantics from SQLite.
  **Test:** apply the schema to a disposable PostgreSQL database and verify tables,
  constraints, indexes, and timestamp types exist as documented.
- [ ] **T06 — Build a database access layer.** Add pooled connections, parameterized
  queries, transaction handling, retry behavior for startup races, and clean shutdown.
  **Test:** integration-test insert, duplicate upsert, current-price query, history
  query, store filtering, rollback on failure, and connection recovery.
- [ ] **T07 — Migrate existing SQLite data.** Create a repeatable migration/import
  command that maps existing `price_history` rows and preserves raw offer JSON and
  validity windows.
  **Test:** import a fixture copy twice and verify row counts and unique keys are
  unchanged on the second run; compare representative rows before and after import.
- [ ] **T08 — Add database migration versioning.** Make schema changes deployable in a
  controlled order and document how a fresh database and an upgraded database are
  handled.
  **Test:** create a fresh database from zero and upgrade a database containing fixture
  data; verify both reach the same schema version without data loss.

### Phase 2 — ingestion-service

- [ ] **T09 — Extract and harden ingestion domain logic.** Move fetch, normalization,
  jämförpris calculation, content dedupe, and upsert behavior from the legacy scripts
  behind service-owned modules; keep external API timeouts and bounded concurrency.
  **Test:** unit-test price/unit conversions, missing quantity fields, malformed offers,
  duplicate IDs, duplicate content, failed upstream requests, and partial store data.
- [ ] **T10 — Implement the ingestion application.** Add a long-lived HTTP process with
  `POST /fetch`, `GET /fetch/status`, and `GET /healthz`, plus an in-process job state
  model that reports queued, running, succeeded, and failed runs.
  **Test:** API-test each endpoint, verify a fetch starts exactly one job, verify a
  second concurrent trigger is rejected or coalesced, and verify status survives
  worker completion and failure.
- [ ] **T11 — Add safe multi-replica job coordination.** Prevent two ingestion pods from
  writing the same run concurrently using a PostgreSQL advisory lock or equivalent
  database-backed lease; do not rely on process memory.
  **Test:** run two service instances against one database, trigger both at once, and
  verify only one active job performs writes while the other returns a deterministic
  response.
- [ ] **T12 — Integrate Willys catalog ingestion.** Implement the selected Willys
  strategy from T01, including timeout, retry, normalization, and failure reporting.
  **Test:** run against a mocked Willys response and verify successful products,
  malformed products, duplicate products, and upstream failure handling.
- [ ] **T13 — Containerize ingestion-service.** Add a minimal Docker image, non-root
  runtime, pinned dependencies, healthcheck-compatible process, and configurable port.
  **Test:** build the image, run it against a test database, call `/healthz`, and verify
  the container has no root user and contains no development-only files.

### Phase 3 — api-service

- [ ] **T14 — Implement product endpoints.** Add `GET /products` with validated store,
  pagination, sorting, and current-price filtering; add
  `GET /products/{id}/history` with optional `store` filtering.
  **Test:** endpoint-test valid results, empty results, pagination boundaries, invalid
  parameters, unknown product IDs, and SQL-injection-shaped input.
- [ ] **T15 — Implement store and health endpoints.** Add `GET /stores` and `GET
  /healthz`, with stable response schemas and database readiness checks.
  **Test:** contract-test response fields and types, verify stores are ordered
  deterministically, and verify readiness changes when the database is unavailable.
- [ ] **T16 — Add API error and observability behavior.** Standardize JSON errors,
  request IDs, structured logs, and latency/status metrics without logging credentials
  or raw secrets.
  **Test:** force validation, database, and unexpected errors; verify status codes,
  response shape, request ID propagation, and secret redaction.
- [ ] **T17 — Containerize api-service.** Add a production WSGI/ASGI process, minimal
  non-root image, dependency pinning, and database configuration from environment.
  **Test:** build and run the image, execute the API contract suite through the exposed
  port, and verify the image starts with a read-only root filesystem where supported.

### Phase 4 — frontend migration

- [ ] **T18 — Replace static-data reads with API reads.** Update the existing receipt UI
  to load products, history, and stores from `/api`, while preserving loading, empty,
  error, and stale-data states.
  **Test:** browser-test initial load with mocked API responses, API failure, empty
  data, store filtering, and product-history rendering.
- [ ] **T19 — Add frontend REST endpoints and proxying.** Serve `GET /healthz` and
  `GET /version`; configure Nginx to proxy `/api/*` to `api-service` and serve assets
  for `/` without exposing internal database or ingestion routes.
  **Test:** run the frontend container and verify both endpoints, asset loading,
  `/api/products` proxying, cache behavior, and 404 handling with `curl` or browser
  integration tests.
- [ ] **T20 — Containerize and accessibility-check frontend.** Add a small non-root
  Nginx image, preserve the current visual language, and ensure the main workflows are
  usable on desktop and mobile.
  **Test:** build the image, run an automated browser smoke test at desktop and mobile
  widths, and check keyboard navigation, meaningful page headings, and failed-request
  messaging.

### Phase 5 — Kubernetes resources

- [ ] **T21 — Add PostgreSQL StatefulSet and storage.** Define the single-replica
  StatefulSet, headless or ClusterIP Service, PVC template, resource requests/limits,
  probes, and safe environment wiring from Secret.
  **Test:** run `kubectl apply --dry-run=server`, inspect the rendered object, and
  verify the pod mounts its PVC, uses the Secret, and has `replicas: 1`.
- [ ] **T22 — Add Secrets and ConfigMap.** Define non-secret shared settings separately
  from credentials; document local development placeholders and production secret
  injection.
  **Test:** render manifests and verify passwords never appear in ConfigMaps, image
  arguments, or committed plain-text values; verify every referenced key exists.
- [ ] **T23 — Deploy ingestion-service.** Add Deployment, ClusterIP Service, probes,
  resource policy, security context, configurable replica count, and independent HPA.
  **Test:** validate the manifest, deploy to a disposable namespace, verify pod probes,
  Service discovery, one active job across replicas, and HPA target configuration.
- [ ] **T24 — Deploy api-service.** Add Deployment, ClusterIP Service, probes,
  resource policy, security context, configurable replicas, and independent HPA.
  **Test:** validate and deploy it, call `/healthz` through the Service, scale replicas
  independently, and verify all replicas return identical read-only results.
- [ ] **T25 — Deploy frontend.** Add Deployment and Service with probes, resource
  policy, security context, configurable replicas, and independent HPA.
  **Test:** validate and deploy it, load the frontend through its Service, scale it
  independently, and verify assets and API proxying still work after rescheduling.
- [ ] **T26 — Add weekly CronJob.** Configure the Monday trigger to call
  `ingestion-service` internally, with bounded retries, timeout, concurrency policy,
  history limits, and a service account with only required permissions.
  **Test:** suspend/unsuspend and manually create a Job from the CronJob; verify the
  request reaches ingestion, duplicate runs are prevented, and failed runs retry then
  surface a failed Job status.
- [ ] **T27 — Add external Ingress.** Route `/` to frontend and `/api` to api-service;
  keep ingestion and PostgreSQL internal and document the NodePort/LoadBalancer
  fallback if the selected cluster lacks an ingress controller.
  **Test:** deploy with the chosen ingress controller and verify browser access to both
  routes from outside the cluster while direct external access to internal Services is
  unavailable.
- [ ] **T28 — Add manifest validation and policy checks.** Make rendered manifests the
  source of truth and check image tags, probes, resource bounds, security contexts,
  selectors, namespace consistency, and required labels.
  **Test:** run schema validation plus a policy tool against all manifests and include a
  deliberately broken fixture in CI to prove the gate catches it.

### Phase 6 — images, deployment, and acceptance

- [ ] **T29 — Build and publish versioned images.** Build ingestion, API, and frontend
  images with immutable commit/version tags, publish them to Docker Hub, and update
  manifests to use the chosen tag rather than relying only on `latest`.
  **Test:** pull each published image on a clean machine, verify its digest, start each
  container, and run its service-specific smoke test.
- [ ] **T30 — Automate CI image builds.** Add a workflow that runs tests first, builds
  all three images, scans them, and publishes only from the approved branch/tag with
  Docker Hub credentials supplied as repository secrets.
  **Test:** run the workflow on a non-publishing change and verify no image is pushed;
  run a controlled release and verify all three digests are recorded.
- [ ] **T31 — Create a full local-cluster deployment script.** Provide one documented
  path for namespace creation, secret/config setup, database readiness, migrations,
  image deployment, and ingress discovery.
  **Test:** execute the script on a clean local cluster, destroy and recreate app pods,
  and verify the application returns with data intact.
- [ ] **T32 — Verify persistence and restart recovery.** Exercise database pod restart,
  app rollout, node rescheduling where available, and failed upstream ingestion.
  **Test:** compare a known product/history fixture before and after each restart and
  verify no duplicate rows or lost data.
- [ ] **T33 — Verify independent horizontal scaling.** Demonstrate separate replica
  changes and HPA behavior for ingestion, API, and frontend, including the deliberately
  single-replica PostgreSQL constraint.
  **Test:** scale each app Deployment independently, generate API load, inspect HPA
  decisions, and verify PostgreSQL remains at one replica with its PVC attached.
- [ ] **T34 — Run end-to-end acceptance tests.** Cover external browser access,
  frontend-to-API calls, API-to-PostgreSQL reads, CronJob-to-ingestion triggering,
  ingestion-to-Tjek mocking, and product history visibility.
  **Test:** run the complete smoke suite against the deployed namespace and record
  passing checks for requirements 1 through 9 in the release checklist.
- [ ] **T35 — Document operations and rollback.** Document deploy, migration, logs,
  probes, HPA diagnosis, backup/restore, secret rotation, image rollback, and the
  fallback exposure method.
  **Test:** have a second operator follow the runbook to roll back one application
  image and restore a database backup in a disposable namespace.

### Definition of done

The project is complete only when T01–T35 are checked, all paired tests pass in CI or
against a disposable Kubernetes namespace, three versioned images are pullable from
Docker Hub, the external smoke test succeeds, and the acceptance checklist explicitly
maps evidence to requirements 1–9.
