# Prispulsen — Operations Runbook

This runbook covers day-to-day operational procedures for the Prispulsen
Kubernetes deployment. All commands assume `kubectl` is configured with access
to the target cluster and that the `prispulsen` namespace exists.

---

## 1. Initial Deployment

Complete setup from a fresh clone to a running cluster.

### Prerequisites

- Docker (for building images locally)
- `kubectl` configured against your cluster
- `ingress-nginx` controller installed in the cluster
- `metrics-server` installed in the cluster (required for HPA)
- Docker Hub credentials (for pushing images)

### Build Docker images

```bash
docker build -t hadidabeast/prispulsen-ingestion:latest ./ingestion-service
docker build -t hadidabeast/prispulsen-api:latest ./api-service
docker build -t hadidabeast/prispulsen-frontend:latest ./frontend
```

### Push images to Docker Hub

```bash
docker login
docker push hadidabeast/prispulsen-ingestion:latest
docker push hadidabeast/prispulsen-api:latest
docker push hadidabeast/prispulsen-frontend:latest
```

### Deploy to Kubernetes

```bash
# Deploy everything in one command
kubectl apply -f k8s/prispulsen.yaml

# Wait for PostgreSQL to be ready, then run db-init
kubectl rollout status statefulset/postgres -n prispulsen --timeout=120s
kubectl run db-init --image=hadidabeast/prispulsen-ingestion:latest \
  --restart=Never --namespace=prispulsen \
  --env="DATABASE_URL=$(kubectl get secret prispulsen-secrets -n prispulsen -o jsonpath='{.data.DATABASE_URL}' | base64 -d)" \
  --command -- python /app/db-init/run_migrations.py

# Wait for all deployments
kubectl rollout status deployment/prispulsen-ingestion -n prispulsen --timeout=120s
kubectl rollout status deployment/prispulsen-api -n prispulsen --timeout=120s
kubectl rollout status deployment/prispulsen-frontend -n prispulsen --timeout=120s
```

### Access the app locally

```bash
# Frontend
kubectl port-forward svc/prispulsen-frontend 8080:8080 -n prispulsen

# pgAdmin (browser DB viewer)
kubectl port-forward svc/pgadmin 8081:80 -n prispulsen
```

Then open `http://localhost:8080` in your browser.

### Trigger ingestion manually

```bash
kubectl exec -n prispulsen deploy/prispulsen-ingestion -- curl -X POST localhost:8000/fetch
```

### Wipe everything (start fresh)

```bash
kubectl delete namespace prispulsen --ignore-not-found
```

---

## 2. Running DB Init / Schema Updates

When a new SQL file is added (e.g., `db-init/003_add_index.sql`):

```bash
# Apply directly against the running postgres pod
kubectl exec -i postgres-0 -n prispulsen -- \
  psql -U prispulsen prispulsen \
  < db-init/003_add_index.sql
```

Using the Python runner (handles versioning automatically):

```bash
# Forward the postgres port locally, then run the runner
kubectl port-forward pod/postgres-0 5432:5432 -n prispulsen &

DATABASE_URL="postgresql://prispulsen:<PASSWORD>@localhost:5432/prispulsen" \
  python3 db-init/run_migrations.py

kill %1   # Stop the port-forward
```

The runner records applied versions in the `schema_version` table and skips
files that have already been applied — it is safe to run repeatedly.

---

## 3. Viewing Logs

### Application logs (last 100 lines, all replicas)

```bash
# api-service
kubectl logs -n prispulsen -l app=prispulsen-api --tail=100

# ingestion-service
kubectl logs -n prispulsen -l app=prispulsen-ingestion --tail=100

# frontend (nginx access/error log)
kubectl logs -n prispulsen -l app=prispulsen-frontend --tail=100

# PostgreSQL
kubectl logs -n prispulsen -l app=postgres --tail=100
```

### Stream logs live

```bash
kubectl logs -n prispulsen -l app=prispulsen-api -f
```

### Logs from a specific pod

```bash
# List pods first
kubectl get pods -n prispulsen

# Then target one by name
kubectl logs prispulsen-api-<pod-id> -n prispulsen --tail=100
```

### Logs from a completed CronJob run

```bash
kubectl get jobs -n prispulsen
kubectl logs job/<job-name> -n prispulsen
```

---

## 4. Health Probe Status

Inspect liveness and readiness probe results for a Deployment:

```bash
kubectl describe pod -n prispulsen -l app=prispulsen-api
```

Scroll to the **Conditions** and **Events** sections. A healthy pod shows:

```
Conditions:
  Type              Status
  Initialized       True
  Ready             True
  ContainersReady   True
  PodScheduled      True
```

If a pod is in `CrashLoopBackOff` or `Not Ready`, look at the Events section
for probe failure details:

```
Events:
  Warning  Unhealthy  Liveness probe failed: HTTP probe failed with status code: 503
```

To check a specific probe manually:

```bash
# Port-forward to the pod and hit /healthz directly
kubectl port-forward pod/<pod-name> 8001:8001 -n prispulsen
curl http://localhost:8001/healthz
```

---

## 5. HPA Diagnosis

### View all HPAs

```bash
kubectl get hpa -n prispulsen
```

### Detailed HPA status

```bash
kubectl describe hpa -n prispulsen
```

### Troubleshooting: HPA shows `<unknown>` for metrics

If the TARGETS column shows `<unknown>/60%`, the HPA cannot read CPU metrics.

**Step 1:** Check that metrics-server is running:

```bash
kubectl get deployment metrics-server -n kube-system
kubectl top pods -n prispulsen
```

If `kubectl top pods` fails with "Metrics API not available":

**Step 2:** Install metrics-server:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

**Step 3:** For local clusters with self-signed kubelet certs:

```bash
kubectl patch deployment metrics-server -n kube-system \
  --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```

**Step 4:** Check that pods have resource requests defined (HPA requires
`resources.requests.cpu` to calculate utilisation percentage). All Prispulsen
deployments have these set — if you add a new deployment, ensure it includes
resource requests.

---

## 6. Image Rollback

If a bad image is deployed, roll back to the previous revision:

```bash
# Undo the last rollout on api-service
kubectl rollout undo deployment/prispulsen-api -n prispulsen

# Undo the last rollout on ingestion-service
kubectl rollout undo deployment/prispulsen-ingestion -n prispulsen

# Undo the last rollout on frontend
kubectl rollout undo deployment/prispulsen-frontend -n prispulsen
```

To roll back to a specific revision:

```bash
# See revision history
kubectl rollout history deployment/prispulsen-api -n prispulsen

# Roll back to revision 2
kubectl rollout undo deployment/prispulsen-api --to-revision=2 -n prispulsen
```

Monitor the rollback progress:

```bash
kubectl rollout status deployment/prispulsen-api -n prispulsen
```

---

## 7. Database Backup & Restore

### Backup (plain SQL)

```bash
kubectl exec postgres-0 -n prispulsen -- \
  pg_dump -U prispulsen prispulsen \
  > backup-$(date +%Y%m%d-%H%M%S).sql
```

### Backup (compressed custom format — recommended)

```bash
kubectl exec postgres-0 -n prispulsen -- \
  pg_dump -U prispulsen -Fc prispulsen \
  > backup-$(date +%Y%m%d-%H%M%S).dump
```

### Restore from plain SQL

```bash
kubectl exec -i postgres-0 -n prispulsen -- \
  psql -U prispulsen prispulsen \
  < backup.sql
```

### Restore from custom format

```bash
kubectl exec -i postgres-0 -n prispulsen -- \
  pg_restore -U prispulsen -d prispulsen --no-owner \
  < backup.dump
```

### Full restore to a clean database

```bash
# 1. Drop and recreate the database
kubectl exec postgres-0 -n prispulsen -- \
  psql -U postgres -c \
  "DROP DATABASE IF EXISTS prispulsen; CREATE DATABASE prispulsen OWNER prispulsen;"

# 2. Restore
kubectl exec -i postgres-0 -n prispulsen -- \
  psql -U prispulsen prispulsen \
  < backup.sql
```

---

## 8. Secret Rotation

When the PostgreSQL password needs to be changed:

### Step 1 — Update the Kubernetes Secret

```bash
# Generate a new password
NEW_PASSWORD=$(openssl rand -base64 32)

# Base64-encode the values
NEW_PW_B64=$(echo -n "$NEW_PASSWORD" | base64)
NEW_DB_URL_B64=$(echo -n "postgresql://prispulsen:${NEW_PASSWORD}@postgres:5432/prispulsen" | base64)

# Patch the secret
kubectl patch secret prispulsen-secrets -n prispulsen \
  --type='json' \
  -p="[
    {\"op\":\"replace\",\"path\":\"/data/POSTGRES_PASSWORD\",\"value\":\"${NEW_PW_B64}\"},
    {\"op\":\"replace\",\"path\":\"/data/DATABASE_URL\",\"value\":\"${NEW_DB_URL_B64}\"}
  ]"
```

### Step 2 — Update the PostgreSQL user password

```bash
kubectl exec postgres-0 -n prispulsen -- \
  psql -U prispulsen -c \
  "ALTER USER prispulsen PASSWORD '${NEW_PASSWORD}';"
```

### Step 3 — Restart all Deployments to pick up the new secret

```bash
kubectl rollout restart deployment/prispulsen-api -n prispulsen
kubectl rollout restart deployment/prispulsen-ingestion -n prispulsen
kubectl rollout restart deployment/prispulsen-frontend -n prispulsen

# Monitor the rollout
kubectl rollout status deployment/prispulsen-api -n prispulsen
kubectl rollout status deployment/prispulsen-ingestion -n prispulsen
kubectl rollout status deployment/prispulsen-frontend -n prispulsen
```

> **Note:** The PostgreSQL StatefulSet itself reads `POSTGRES_PASSWORD` only
> at `initdb` time (first start). Changing the secret after initialisation
> only takes effect for the PostgreSQL user password via the `ALTER USER`
> command above, not for the container environment variable.

---

## 9. Triggering a Manual Ingestion

The CronJob runs weekly automatically, but you can trigger an ingestion at any
time:

### Via the ingestion-service HTTP endpoint

```bash
kubectl exec -n prispulsen deploy/prispulsen-ingestion -- \
  curl -X POST localhost:8000/fetch
```

Expected response (202 Accepted):

```json
{"run_id": 42, "status": "queued"}
```

### Check ingestion status

```bash
kubectl exec -n prispulsen deploy/prispulsen-ingestion -- \
  curl localhost:8000/fetch/status
```

### Via a one-off Kubernetes Job (mirrors the CronJob)

```bash
kubectl create job manual-ingest-$(date +%s) \
  --from=cronjob/prispulsen-ingestion-cron \
  -n prispulsen
```

Monitor the job:

```bash
kubectl get jobs -n prispulsen
kubectl logs job/manual-ingest-<timestamp> -n prispulsen
```

---

## 10. Fallback Exposure (NodePort)

If the `ingress-nginx` controller is unavailable (e.g., testing on a bare
`kind` cluster without a load balancer), expose the frontend via a NodePort
Service:

### Create a NodePort Service

```bash
kubectl expose deployment prispulsen-frontend \
  --name=prispulsen-frontend-nodeport \
  --type=NodePort \
  --port=8080 \
  --target-port=8080 \
  -n prispulsen
```

### Find the assigned NodePort

```bash
kubectl get service prispulsen-frontend-nodeport -n prispulsen
```

Look for the port in the `PORT(S)` column, e.g. `8080:32456/TCP`. The
NodePort is `32456`.

### Access the app

```bash
# Get the node IP
kubectl get nodes -o wide

# Then open in browser or curl
curl http://<node-ip>:32456
```

For `kind` clusters on Docker Desktop, the node IP is typically `127.0.0.1`:

```bash
curl http://localhost:32456
```

### Clean up the temporary NodePort Service when done

```bash
kubectl delete service prispulsen-frontend-nodeport -n prispulsen
```

> The NodePort approach is for testing only. In production, use the Ingress
> with a proper load balancer or cloud-provider's external IP assignment.
