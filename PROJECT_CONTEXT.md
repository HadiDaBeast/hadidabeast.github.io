# Prispulsen — Project Context

## What It Is
A Swedish grocery price tracker for 7 stores in Karlskrona. Tracks weekly deal offers and price history. Originally a static GitHub Pages site; migrated to a Kubernetes microservices architecture.

**Domain:** `prispulsen.nu`  
**Stores tracked:** Lidl, Hemköp, Willys, City Gross, ICA Supermarket Cityhallen, ICA Maxi Stormarknad, Coop X:-tra

---

## Architecture: 3 Services + PostgreSQL

```
Internet → nginx Ingress (prispulsen.nu)
              ├─ /api/* → api-service:8001
              └─ /*     → frontend:8080 (nginx static + proxy)

ingestion-service:8000 ←── weekly CronJob (POST /fetch, every Monday 06:00 UTC)
        │
        ▼
  PostgreSQL 16 (StatefulSet, 5Gi PVC)
        ▲
  api-service:8001
```

All resources in the `prispulsen` Kubernetes namespace.

---

## Services

### ingestion-service (`ingestion-service/`)
- FastAPI on port 8000
- Fetches from **Tjek/Etilbudsavis API** (`app/ingestion.py`) — 10 grocery queries × 7 store geolocations, 5-thread pool
- Fetches from **Willys dealer API** (`app/willys.py`)
- Uses `pg_try_advisory_lock(42)` to prevent concurrent runs across replicas
- Endpoints: `POST /fetch` (202/409), `GET /fetch/status`, `GET /healthz`
- Job state tracked in memory (`JobState` dataclass) and `ingestion_runs` DB table
- 1 replica, HPA 1–3

### api-service (`api-service/`)
- FastAPI read-only API on port 8001
- Endpoints: `GET /stores`, `GET /products` (paginated, `?store=` filter), `GET /products/{name}/history`, `GET /healthz`, `GET /version`
- X-Request-ID middleware, structured logging, no SQL details in error responses
- 2 replicas, HPA 2–6 at 60% CPU

### frontend (`frontend/`)
- nginx:1.27-alpine serving `frontend/www/` (static HTML/CSS/JS)
- Proxies `/api/*` to api-service
- Runs as `nobody`, read-only root filesystem

---

## Database Schema (PostgreSQL 16)

| Table | Purpose |
|---|---|
| `price_history` | Core data: store_name, product_name, price, unit_price, base_unit, valid_from, valid_until, fetched_at, raw_json (JSONB) |
| `ingestion_runs` | Audit log: status, timing, offers_stored, error_message |
| `stores` | Store registry with lat/lng |
| `schema_version` | Migration tracking (plain versioned SQL, no Alembic) |

**Key constraint:** `UNIQUE (store_name, product_name, price, valid_from, valid_until)` + `ON CONFLICT DO NOTHING` — ingestion is fully idempotent.

Migrations: `migrations/NNN_description.sql`, runner: `migrations/run_migrations.py`

---

## Kubernetes Resources (`k8s/`)

| File | Purpose |
|---|---|
| `namespace.yaml` | `prispulsen` namespace |
| `configmap.yaml` | Non-secret config (DB name, ports, API URLs, log level) |
| `secret.yaml` | DB credentials + DATABASE_URL (**placeholder values — must replace before deploy**) |
| `postgres-statefulset.yaml` | Postgres 16 with PVC |
| `postgres-service.yaml` | Headless service |
| `ingestion-deployment.yaml` | 1 replica |
| `api-deployment.yaml` | 2 replicas |
| `frontend-deployment.yaml` | Frontend nginx |
| `*-service.yaml` | ClusterIP services |
| `*-hpa.yaml` | HPA for all 3 app services |
| `ingestion-cronjob.yaml` | Weekly Monday 06:00 UTC curl POST to `/fetch` |
| `ingestion-serviceaccount.yaml` | Dedicated ServiceAccount |
| `ingress.yaml` | Routes `/api/*` → api-service, `/*` → frontend (host: prispulsen.nu + localhost fallback) |

---

## CI/CD (`.github/workflows/`)

- **`ci.yml`** — on push/PR to `main`: `ruff` lint, `pytest`, `kubectl --dry-run` manifest validation, credential check (no `CHANGEME` in non-comment lines)
- **`docker-publish.yml`** — builds and pushes 3 Docker images to Docker Hub after CI passes. Tag format: `dev-<sha>` or `v<semver>-<sha>`

---

## Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Database | PostgreSQL (was SQLite) | Multi-pod shared state, JSONB, concurrent connections |
| Web framework | FastAPI (was Flask) | Auto OpenAPI docs, Pydantic validation, async support |
| DB migrations | Plain versioned SQL (no Alembic) | Schema changes are rare; raw SQL is simpler |
| Concurrency lock | PostgreSQL advisory lock | Prevents duplicate ingestion without a distributed queue |
| Service split | 3 services (Willys lives in ingestion-service) | Splitting would duplicate boilerplate for no boundary benefit |
| Container registry | Docker Hub (`hadidabeast/`) | Cost — personal/hobbyist project |
| Config | Env vars via ConfigMap/Secret | 12-factor, no config baked into images |

---

## Known Issues / TODOs

- `secret.yaml` contains placeholder credentials — must be replaced with real values before any real deployment.

---

## Legacy Files (root directory, not containerized)

- `app.py` — original Flask app
- `fetch_prices.py`, `fetch_willys.py` — original standalone scripts
- `db.py` — SQLite data layer
- `export.py` — JSON export for static site
- `index.html`, `style.css` — original GitHub Pages frontend
- `prispulsen.db` — SQLite database (~1MB)
- `public/data/` — pre-generated static JSON (current.json, products.json, ~130 per-product history files)
- `weekly-update.yml` — original GitHub Actions schedule workflow
- `prispulsen-k8s-plan (1).md` — detailed migration planning document

---

## Local Development

```bash
# Deploy to minikube/kind
chmod +x scripts/*.sh
scripts/deploy-local.sh

# Build Docker images
scripts/build-images.sh

# Push to Docker Hub
scripts/push-images.sh

# Teardown
scripts/teardown.sh

# Manually trigger ingestion
kubectl create job --from=cronjob/prispulsen-weekly-fetch manual-fetch -n prispulsen
```

### Accessing the app locally (WSL + minikube)

The dev environment is **WSL2 on Windows** running minikube. The minikube IP
(`192.168.49.2`) is not reachable from Windows directly, and `minikube tunnel`
only bridges WSL → Windows if the ingress-nginx controller is `LoadBalancer`
type — even then Windows portproxy shenanigans make it unreliable.

**Simplest approach — just use port-forward:**

```bash
kubectl port-forward svc/prispulsen-frontend 8080:8080 -n prispulsen
```

Then open `http://localhost:8080` in the Windows browser. Works immediately,
no hosts file or tunnel needed. The frontend's nginx config proxies `/api/*`
to `prispulsen-api:8001` inside the cluster, so the full stack works.

**If you want `http://prispulsen.nu` locally (more complex):**
1. Patch ingress-nginx to LoadBalancer: `kubectl patch svc ingress-nginx-controller -n ingress-nginx -p '{"spec": {"type": "LoadBalancer"}}'`
2. Run `minikube tunnel` in a separate terminal (keep it open)
3. Add a Windows portproxy (Command Prompt as Administrator):
   ```cmd
   netsh interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=80 connectaddress=<WSL_IP> connectport=80
   ```
   Where `<WSL_IP>` is from `ip addr show eth0` in WSL (e.g. `172.31.171.234` — changes on reboot).
4. Add to `C:\Windows\System32\drivers\etc\hosts`: `127.0.0.1 prispulsen.nu`
5. Disable "Use secure DNS" in Chrome/Edge settings
6. Open `http://prispulsen.nu` (must be `http://`, not `https://`)

To clean up portproxy later:
```cmd
netsh interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=80
```

---

## File Layout

```
.
├── ingestion-service/       # Ingestion FastAPI service
│   ├── app/
│   │   ├── main.py          # FastAPI app, job state, background worker
│   │   ├── ingestion.py     # Tjek API fetching + parsing
│   │   ├── willys.py        # Willys API fetching
│   │   └── db.py            # PostgreSQL pool + queries
│   └── tests/
├── api-service/             # Read API FastAPI service
│   ├── app/
│   │   ├── main.py          # Routes, middleware, error handlers
│   │   └── db.py            # Read queries (paginated, filtered)
│   └── tests/
├── frontend/                # nginx static frontend
│   ├── www/                 # index.html + style.css
│   ├── nginx.conf
│   └── Dockerfile
├── k8s/                     # All Kubernetes manifests
├── migrations/              # SQL migration files + runner
├── scripts/                 # deploy-local.sh, build/push/teardown
├── docs/                    # decisions.md, runbook.md, scaling.md, etc.
└── .github/workflows/       # ci.yml, docker-publish.yml
```
