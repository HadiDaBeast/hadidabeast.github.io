# Prispulsen — Acceptance Checklist

This document maps evidence to the 9 assignment requirements.

## Requirement 1: Deployable via Kubernetes
- [ ] `kubectl get all -n prispulsen` shows all pods Running
- [ ] `kubectl apply -f k8s/` completes without error
- [ ] Manifests include: Deployments, Services, Ingress, StatefulSet, PVC, CronJob, HPA
Evidence: _____________

## Requirement 2: ≥2 microservice types + a database
- [ ] ingestion-service pod is Running
- [ ] api-service pod is Running
- [ ] frontend pod is Running
- [ ] postgres pod is Running
Evidence: _____________

## Requirement 3: Each microservice implements a REST API
- [ ] `curl http://localhost:8000/healthz` returns 200 (ingestion-service)
- [ ] `curl http://localhost:8001/healthz` returns 200 (api-service)
- [ ] `curl http://localhost:8080/healthz` returns 200 (frontend)
- [ ] All services expose documented endpoints
Evidence: _____________

## Requirement 4: Accessible from outside Kubernetes
- [ ] Browser loads http://prispulsen.nu/ (or NodePort URL)
- [ ] Browser sees offer cards with price data
- [ ] `curl http://prispulsen.nu/api/products` returns JSON
Evidence: _____________

## Requirement 5: Independent horizontal scaling
- [ ] `kubectl scale deployment prispulsen-api --replicas=3 -n prispulsen` succeeds
- [ ] `kubectl scale deployment prispulsen-ingestion --replicas=2 -n prispulsen` succeeds
- [ ] `kubectl scale deployment prispulsen-frontend --replicas=3 -n prispulsen` succeeds
- [ ] `kubectl get hpa -n prispulsen` shows 3 independent HPAs
- [ ] PostgreSQL stays at 1 replica throughout
Evidence: _____________

## Requirement 6: Images pushed to Docker Hub
- [ ] `docker pull taylorbourne/prispulsen-ingestion:latest` succeeds
- [ ] `docker pull taylorbourne/prispulsen-api:latest` succeeds
- [ ] `docker pull taylorbourne/prispulsen-frontend:latest` succeeds
- [ ] Images have versioned tags (not only :latest)
Evidence: _____________

## Requirement 7: Database as a separate microservice
- [ ] `kubectl get statefulset postgres -n prispulsen` shows 1/1 Ready
- [ ] Postgres has its own Service (ClusterIP, not exposed externally)
- [ ] App services connect via DATABASE_URL env var
- [ ] No app pod embeds a database process
Evidence: _____________

## Requirement 8: Persistent storage across restarts
- [ ] `kubectl get pvc -n prispulsen` shows Bound PVC
- [ ] Note row count from `kubectl exec postgres-0 -n prispulsen -- psql -U prispulsen -c 'SELECT COUNT(*) FROM price_history;'`
- [ ] `kubectl delete pod postgres-0 -n prispulsen` and wait for restart
- [ ] Row count unchanged after restart
Evidence: _____________

## Requirement 9: Database doesn't need to scale
- [ ] `kubectl get statefulset postgres -n prispulsen -o jsonpath='{.spec.replicas}'` outputs `1`
- [ ] No HPA targets the postgres StatefulSet
- [ ] Only one postgres pod exists at all times
Evidence: _____________
