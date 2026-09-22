# Prispulsen — Persistence and Scaling

This document explains how Prispulsen handles data durability across pod
restarts, how to perform database backups and restores, how rolling updates
work, and what happens when an ingestion run fails.

---

## 1. How Data Persists Across PostgreSQL Pod Restarts

PostgreSQL is deployed as a **StatefulSet** (`k8s/postgres-statefulset.yaml`)
with a **PersistentVolumeClaim (PVC)** template. This is the key distinction
from a regular Deployment: the PVC is provisioned once and is _not_ deleted
when the pod is rescheduled or restarted.

```
StatefulSet: postgres
  └─ Pod: postgres-0
       └─ VolumeMount: /var/lib/postgresql/data
            └─ PVC: postgres-data-postgres-0   (ReadWriteOnce, 5Gi)
                 └─ PersistentVolume            (provisioned by StorageClass)
```

When `postgres-0` is deleted (e.g. OOMKilled, node eviction, manual delete),
the StatefulSet controller recreates the pod with the **same name** and
**same PVC**. Because the data directory is on the PVC — not inside the
container filesystem — all rows survive the restart without any extra steps.

The `PGDATA` environment variable is set to `/var/lib/postgresql/data/pgdata`
so that PostgreSQL's data directory sits one level below the mount point,
which is required by the official `postgres` Docker image to avoid permission
issues on the mounted volume.

---

## 2. Verifying Data Integrity After a Pod Restart

Run the following sequence to prove that rows survive a restart:

```bash
# 1. Note the current row count (before restart)
kubectl exec postgres-0 -n prispulsen -- \
  psql -U prispulsen -d prispulsen -c \
  'SELECT COUNT(*) FROM price_history;'

# 2. Delete the pod (StatefulSet will immediately recreate it)
kubectl delete pod postgres-0 -n prispulsen

# 3. Wait until the replacement pod is Ready
kubectl wait pod/postgres-0 \
  --for=condition=Ready \
  -n prispulsen \
  --timeout=120s

# 4. Check the row count again — it should be identical
kubectl exec postgres-0 -n prispulsen -- \
  psql -U prispulsen -d prispulsen -c \
  'SELECT COUNT(*) FROM price_history;'
```

A matching row count confirms that the PVC preserved all data through the
restart cycle.

---

## 3. Database Backup

Use `pg_dump` from inside the running `postgres-0` pod. The dump is streamed
via `kubectl exec` directly to a local file — no intermediate storage needed
inside the cluster.

```bash
kubectl exec postgres-0 -n prispulsen -- \
  pg_dump -U prispulsen prispulsen \
  > backup.sql
```

The resulting `backup.sql` is a plain-text SQL script containing the schema
and all data. Store it somewhere safe (e.g., S3, encrypted local storage).

To create a compressed custom-format dump (faster restore, smaller file):

```bash
kubectl exec postgres-0 -n prispulsen -- \
  pg_dump -U prispulsen -Fc prispulsen \
  > backup.dump
```

---

## 4. Database Restore

### From a plain-text dump

```bash
kubectl exec -i postgres-0 -n prispulsen -- \
  psql -U prispulsen prispulsen \
  < backup.sql
```

The `-i` flag keeps stdin open so the local `backup.sql` can be piped into
the container.

### From a custom-format dump

```bash
kubectl exec -i postgres-0 -n prispulsen -- \
  pg_restore -U prispulsen -d prispulsen --no-owner \
  < backup.dump
```

**Before restoring to an existing database**, drop and recreate the target
database or truncate affected tables to avoid duplicate-key conflicts:

```bash
# Drop and recreate (destructive — make sure you have a good backup first)
kubectl exec postgres-0 -n prispulsen -- \
  psql -U prispulsen -c \
  'DROP DATABASE prispulsen; CREATE DATABASE prispulsen OWNER prispulsen;'
```

---

## 5. How Rolling Updates Work (Zero-Downtime Deployments)

The three application Deployments (`prispulsen-api`, `prispulsen-ingestion`,
`prispulsen-frontend`) use Kubernetes' default `RollingUpdate` strategy.

With **2 or more replicas running**, the update proceeds as follows:

1. Kubernetes creates a new pod running the updated image.
2. The new pod passes its `readinessProbe` (HTTP GET `/healthz`) before being
   added to the Service's endpoint list.
3. One old pod is terminated — Kubernetes sends it a `SIGTERM`, waits for
   in-flight requests to drain (`terminationGracePeriodSeconds: 30`), then
   removes it.
4. Steps 1–3 repeat for each remaining old pod.

Because traffic only routes to pods that have passed their readiness probe,
there is no moment when the service is entirely unavailable. The minimum
number of pods in service at any time is controlled by:

```yaml
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxUnavailable: 0    # never reduce below desired count
    maxSurge: 1          # allow one extra pod during transition
```

> **Note:** With `replicas: 1` (the ingestion-service default), there is a
> brief unavailability window during an update. Scale to 2+ replicas before
> performing updates if zero downtime is required for the ingestion endpoint.

---

## 6. What Happens When an Ingestion Run Fails

The ingestion-service uses a two-layer failure-safety mechanism:

### PostgreSQL Advisory Lock

Before writing any offer rows, the background worker calls:

```sql
SELECT pg_try_advisory_lock(42);
```

If another replica (or a concurrent run on the same pod) already holds the
lock, `pg_try_advisory_lock` returns `false` immediately. The job is then
marked `failed` in the `ingestion_runs` table without writing any partial
data.

The lock is **always released** in a `finally` block:

```sql
SELECT pg_advisory_unlock(42);
```

This means that even if the worker exits abnormally, the lock is released when
the database connection is closed — no manual cleanup is needed.

### Status Tracking

Every ingestion run is recorded in the `ingestion_runs` table with one of
four statuses: `queued → running → succeeded | failed`.

On failure:
- `status` is set to `'failed'`
- `completed_at` is set to the current timestamp
- `error` column contains the error message

### CronJob Retry Behaviour

The weekly CronJob (`k8s/ingestion-cronjob.yaml`) sets:

```yaml
spec:
  backoffLimit: 3
```

If the Job Pod exits with a non-zero status code (unhandled crash, OOMKill,
etc.), Kubernetes retries up to 3 times with exponential back-off (10 s →
20 s → 40 s). If all 3 retries fail, the CronJob is marked as `Failed` and
an alert should be raised.

A triggered ingestion that returns HTTP 200 from `/fetch` but writes 0 rows
is _not_ a CronJob failure — the pod exited cleanly. That case is visible via
the `ingestion_runs` table status and the `/fetch/status` endpoint.

To inspect recent CronJob history:

```bash
kubectl get jobs -n prispulsen
kubectl describe job <job-name> -n prispulsen
kubectl logs job/<job-name> -n prispulsen
```
