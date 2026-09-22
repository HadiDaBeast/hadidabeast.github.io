# Prispulsen — Horizontal Scaling

This document explains how the three application services scale independently,
why PostgreSQL does not scale, and how to operate and observe the
HorizontalPodAutoscaler (HPA) resources in the cluster.

---

## 1. The Three HPAs

Each app service has its own independent HPA, targeting its corresponding
Deployment:

| HPA Name | Target Deployment | Min Replicas | Max Replicas | Scale Trigger |
|---|---|---|---|---|
| `prispulsen-ingestion-hpa` | `prispulsen-ingestion` | 1 | 3 | CPU ≥ 70 % |
| `prispulsen-api-hpa` | `prispulsen-api` | 2 | 6 | CPU ≥ 60 % |
| `prispulsen-frontend-hpa` | `prispulsen-frontend` | 2 | 5 | CPU ≥ 70 % |

The HPA controller checks CPU utilisation every 15 seconds (default scrape
interval). When average CPU across the pods in the Deployment exceeds the
target percentage, the HPA gradually adds pods up to `maxReplicas`. When load
drops, it scales back down after a cool-down period (default 5 minutes).

The services scale completely independently: a spike in API read traffic will
not affect the ingestion-service replica count, and vice versa.

---

## 2. Viewing HPA Status

```bash
kubectl get hpa -n prispulsen
```

Example output:

```
NAME                        REFERENCE                     TARGETS   MINPODS   MAXPODS   REPLICAS
prispulsen-api-hpa          Deployment/prispulsen-api     18%/60%   2         6         2
prispulsen-frontend-hpa     Deployment/prispulsen-frontend 12%/70%  2         5         2
prispulsen-ingestion-hpa    Deployment/prispulsen-ingestion 5%/70%  1         3         1
```

For a detailed view including events and conditions:

```bash
kubectl describe hpa -n prispulsen
```

---

## 3. Manual Scaling

Override the HPA by setting replicas directly:

```bash
# Scale api-service to 4 replicas
kubectl scale deployment prispulsen-api --replicas=4 -n prispulsen

# Scale ingestion-service to 2 replicas
kubectl scale deployment prispulsen-ingestion --replicas=2 -n prispulsen

# Scale frontend to 3 replicas
kubectl scale deployment prispulsen-frontend --replicas=3 -n prispulsen
```

> **Note:** If HPA is active, it will eventually override a manual scale back
> to the range `[minReplicas, maxReplicas]`. To permanently fix the replica
> count, either disable the HPA or update its `minReplicas` / `maxReplicas`.

To watch the replica count change in real time:

```bash
kubectl get pods -n prispulsen -l app=prispulsen-api -w
```

---

## 4. Why PostgreSQL Does Not Scale

PostgreSQL is a **StatefulSet** with `replicas: 1`. It stays at a single
replica for two structural reasons:

1. **ReadWriteOnce PVC:** The PersistentVolumeClaim uses `accessModes:
   ReadWriteOnce`, which means the volume can only be mounted by one node at
   a time. A second PostgreSQL pod on a different node could not attach to the
   same volume.

2. **Single writer model:** PostgreSQL's streaming replication (primary +
   standby) requires a separate operator (e.g., CloudNativePG, Patroni) to
   manage failover and read-replica routing. Without such an operator, running
   multiple postgres pods would result in data corruption from concurrent
   writes to the same data directory.

For this project the database is intentionally kept at a single replica. Read
throughput is handled by the `api-service` replicas which maintain a
connection pool, so the database is not the bottleneck under typical load.

There is **no HPA for the postgres StatefulSet**, and none should be added.

---

## 5. Prerequisites for HPA: metrics-server

The HPA controller requires the **metrics-server** to be installed in the
cluster. Without it, HPA targets will show `<unknown>` and no scaling will
occur.

Check whether metrics-server is installed:

```bash
kubectl get deployment metrics-server -n kube-system
```

If it is missing, install it:

```bash
# Using the official manifest
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

On clusters where the kubelet serving certificate is self-signed (e.g., local
`kind` or `k3s`), add the `--kubelet-insecure-tls` flag:

```bash
kubectl patch deployment metrics-server -n kube-system \
  --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```

Verify that CPU metrics are being collected:

```bash
kubectl top pods -n prispulsen
```

---

## 6. Advisory Lock and Multi-Replica Ingestion

When `prispulsen-ingestion` has 2 or more replicas, multiple pods can receive
`POST /fetch` requests simultaneously. The advisory lock prevents duplicate
ingestion runs:

1. Pod A calls `SELECT pg_try_advisory_lock(42)` — returns `true`, proceeds.
2. Pod B calls `SELECT pg_try_advisory_lock(42)` — returns `false` because A
   holds the lock.
3. Pod B marks its run as `failed` with message _"Another ingestion is already
   running"_ and exits immediately without touching `price_history`.
4. Pod A completes normally and calls `SELECT pg_advisory_unlock(42)`.

The lock is held on a **dedicated connection** and is always released in a
`finally` block, so a crashed pod will not leave the lock held indefinitely —
PostgreSQL releases session-level advisory locks automatically when the
connection is closed.

This means scaling ingestion-service to multiple replicas is safe: at most one
actual ingestion run will execute at any given time.

---

## 7. Load Test to Trigger HPA

Use `busybox` to generate sustained HTTP traffic against the API service,
which will push CPU utilisation above the 60 % threshold and cause the HPA to
scale up:

```bash
kubectl run load-test \
  --image=busybox \
  --rm -it \
  -- sh -c 'while true; do wget -q -O- http://prispulsen-api:8001/products; done'
```

While the load test is running, watch HPA activity in a separate terminal:

```bash
watch kubectl get hpa -n prispulsen
```

To also watch pod count change:

```bash
kubectl get pods -n prispulsen -l app=prispulsen-api -w
```

Stop the load test with `Ctrl-C`. The HPA will scale back down after the
5-minute cool-down window.
