#!/usr/bin/env bash
# deploy-local.sh — Deploy Prispulsen to a local Kubernetes cluster (minikube/kind)
# Run `chmod +x scripts/*.sh` once to make all scripts executable.
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log() { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err() { echo -e "${RED}[error]${NC} $*" >&2; }

# 1. Namespace
log "Creating namespace..."
kubectl apply -f k8s/namespace.yaml

# 2. ConfigMap
log "Applying ConfigMap..."
kubectl apply -f k8s/configmap.yaml

# 3. Secrets — prompt if CHANGEME placeholder is still present
if grep -q 'CHANGEME_REPLACE_BEFORE_DEPLOY' k8s/secret.yaml; then
    warn "secret.yaml still contains placeholder values."
    warn "Please edit k8s/secret.yaml with real credentials before deploying."
    read -p "Continue anyway? (y/N) " -n 1 -r; echo
    [[ $REPLY =~ ^[Yy]$ ]] || { err "Aborted."; exit 1; }
fi
kubectl apply -f k8s/secret.yaml

# 4. PostgreSQL
log "Deploying PostgreSQL..."
kubectl apply -f k8s/postgres-statefulset.yaml
kubectl apply -f k8s/postgres-service.yaml

log "Waiting for PostgreSQL to be ready..."
kubectl rollout status statefulset/postgres -n prispulsen --timeout=120s

# 5. Run migrations
log "Running database migrations..."
kubectl run migrate --image=hadidabeast/prispulsen-ingestion:latest \
  --restart=Never --namespace=prispulsen \
  --env="DATABASE_URL=$(kubectl get secret prispulsen-secrets -n prispulsen -o jsonpath='{.data.DATABASE_URL}' | base64 -d)" \
  --command -- python /app/migrations/run_migrations.py 2>/dev/null || true

# 6. Service account
kubectl apply -f k8s/ingestion-serviceaccount.yaml

# 7. App deployments
log "Deploying services..."
kubectl apply -f k8s/ingestion-deployment.yaml
kubectl apply -f k8s/ingestion-service.yaml
kubectl apply -f k8s/ingestion-hpa.yaml

kubectl apply -f k8s/api-deployment.yaml
kubectl apply -f k8s/api-service.yaml
kubectl apply -f k8s/api-hpa.yaml

kubectl apply -f k8s/frontend-deployment.yaml
kubectl apply -f k8s/frontend-service.yaml
kubectl apply -f k8s/frontend-hpa.yaml

# 8. CronJob
kubectl apply -f k8s/ingestion-cronjob.yaml

# 9. Ingress
kubectl apply -f k8s/ingress.yaml

log "Waiting for deployments..."
kubectl rollout status deployment/prispulsen-ingestion -n prispulsen --timeout=120s
kubectl rollout status deployment/prispulsen-api -n prispulsen --timeout=120s
kubectl rollout status deployment/prispulsen-frontend -n prispulsen --timeout=120s

log "=== Deployment complete ==="
log "Get the ingress address: kubectl get ingress -n prispulsen"
log "Port-forward frontend: kubectl port-forward svc/prispulsen-frontend 8080:8080 -n prispulsen"
