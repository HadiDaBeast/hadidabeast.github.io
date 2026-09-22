# Prispulsen — Configuration Reference

All runtime configuration is passed via environment variables. No configuration
is baked into container images. Use Kubernetes `ConfigMap` for non-secret values
and `Secret` for credentials.

For local development, create a `.env` file in the service directory. The
`python-dotenv` package loads it automatically on startup.

---

## ingestion-service

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | **Yes** | — | PostgreSQL connection string. Format: `postgresql://user:pass@host:5432/dbname` |
| `TJEK_API_URL` | No | `https://api.etilbudsavis.dk/v2` | Base URL for the Tjek/eTilbudsavis API. Override for testing against a mock server. |
| `INGESTION_PORT` | No | `8000` | TCP port the uvicorn server binds to inside the container. |
| `LOG_LEVEL` | No | `INFO` | Python logging level. One of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `APP_VERSION` | No | `dev` | Reported in `/health` and structured log output. Set by CI to the image tag (e.g. `v1.0.0-abc1234`). |

### Example `.env` (local dev)

```dotenv
DATABASE_URL=postgresql://prispulsen:secret@localhost:5432/prispulsen
TJEK_API_URL=https://api.etilbudsavis.dk/v2
INGESTION_PORT=8000
LOG_LEVEL=DEBUG
APP_VERSION=dev
```

### Kubernetes ConfigMap + Secret example

```yaml
# configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: ingestion-config
data:
  TJEK_API_URL: "https://api.etilbudsavis.dk/v2"
  INGESTION_PORT: "8000"
  LOG_LEVEL: "INFO"
  APP_VERSION: "v1.0.0-abc1234"
---
# secret.yaml (create with: kubectl create secret generic ingestion-secret --from-literal=DATABASE_URL=...)
apiVersion: v1
kind: Secret
metadata:
  name: ingestion-secret
type: Opaque
stringData:
  DATABASE_URL: "postgresql://prispulsen:CHANGEME@postgres-svc:5432/prispulsen"
```

---

## api-service

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | **Yes** | — | PostgreSQL connection string. Same format as ingestion-service. The api-service user only needs `SELECT` privileges. |
| `API_PORT` | No | `8001` | TCP port the uvicorn server binds to inside the container. |
| `LOG_LEVEL` | No | `INFO` | Python logging level. |
| `APP_VERSION` | No | `dev` | Reported in `/health` responses and log output. |

### Example `.env` (local dev)

```dotenv
DATABASE_URL=postgresql://prispulsen:secret@localhost:5432/prispulsen
API_PORT=8001
LOG_LEVEL=DEBUG
APP_VERSION=dev
```

---

## frontend

The frontend container is an nginx image. Configuration is injected via
environment variables that are substituted into `nginx.conf` at container
startup using `envsubst`.

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_SERVICE_URL` | **Yes** | — | Internal URL of the api-service. In Kubernetes: `http://api-service:8001`. Used by nginx to proxy `/api/*` requests. |
| `NGINX_PORT` | No | `80` | Port nginx listens on inside the container. |
| `APP_VERSION` | No | `dev` | Exposed via `X-App-Version` response header for debugging. |

### Example `.env` (local dev)

```dotenv
API_SERVICE_URL=http://localhost:8001
NGINX_PORT=8080
APP_VERSION=dev
```

### Kubernetes ConfigMap example

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: frontend-config
data:
  API_SERVICE_URL: "http://api-service:8001"
  NGINX_PORT: "80"
  APP_VERSION: "v1.0.0-abc1234"
```

---

## Shared conventions

- **`DATABASE_URL` is always a secret.** Never store it in a `ConfigMap` or
  commit it to version control.
- **`APP_VERSION`** should be set by CI/CD to the image tag so that
  `/health` endpoints report the exact running version. Default `dev` is only
  for local work.
- **`LOG_LEVEL=DEBUG`** in production will produce very high log volume.
  Use `INFO` or `WARNING` in production.
- All services load `.env` via `python-dotenv` with `override=False`, meaning
  real environment variables always take precedence over `.env` file values.
  This ensures Kubernetes-injected env vars are never shadowed.
