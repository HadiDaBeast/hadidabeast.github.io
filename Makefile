DOCKER_USER   ?= hadidabeast
INGESTION_IMG  = $(DOCKER_USER)/prispulsen-ingestion:latest
API_IMG        = $(DOCKER_USER)/prispulsen-api:latest
FRONTEND_IMG   = $(DOCKER_USER)/prispulsen-frontend:latest
NAMESPACE      = prispulsen

.PHONY: help install-docker install-minikube install-kubectl \
        build push \
        up down restart wipe \
        migrate ingestion logs status open \
        all

# ---------------------------------------------------------------------------
# Default target
# ---------------------------------------------------------------------------

help:
	@echo ""
	@echo "Prispulsen — available targets:"
	@echo ""
	@echo "  Setup"
	@echo "    make install-docker      Install Docker Engine"
	@echo "    make install-minikube    Install minikube"
	@echo "    make install-kubectl     Install kubectl"
	@echo "    make install             Install all of the above"
	@echo ""
	@echo "  Images"
	@echo "    make build               Build all 3 Docker images"
	@echo "    make push                Push all 3 images to Docker Hub"
	@echo ""
	@echo "  Cluster"
	@echo "    make cluster-start       Start minikube + enable ingress addon"
	@echo "    make cluster-stop        Stop minikube"
	@echo ""
	@echo "  Deploy"
	@echo "    make up                  Deploy everything to Kubernetes"
	@echo "    make down                Delete the prispulsen namespace"
	@echo "    make restart             Restart all deployments"
	@echo "    make wipe                Wipe cluster completely and redeploy"
	@echo ""
	@echo "  Operations"
	@echo "    make migrate             Re-run DB migrations manually"
	@echo "    make ingestion           Trigger a manual ingestion run"
	@echo "    make open                Port-forward frontend to localhost:8080"
	@echo "    make logs                Tail logs from all services"
	@echo "    make status              Show all pods in the namespace"
	@echo ""
	@echo "  All-in-one"
	@echo "    make all                 build + push + up + open"
	@echo "    make start               up + ingestion + open"
	@echo ""

# ---------------------------------------------------------------------------
# Install dependencies
# ---------------------------------------------------------------------------

install-docker:
	@echo "==> Installing Docker Engine..."
	apt-get update -qq
	apt-get install -y -qq ca-certificates curl gnupg
	install -m 0755 -d /etc/apt/keyrings
	curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
	chmod a+r /etc/apt/keyrings/docker.gpg
	echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu noble stable" \
		| tee /etc/apt/sources.list.d/docker.list
	apt-get update -qq
	apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
	service docker start
	@echo "==> Docker installed."

install-minikube:
	@echo "==> Installing minikube..."
	curl -fsSL -o /tmp/minikube-linux-amd64 \
		https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
	install /tmp/minikube-linux-amd64 /usr/local/bin/minikube
	rm /tmp/minikube-linux-amd64
	@echo "==> minikube installed: $$(minikube version --short)"

install-kubectl:
	@echo "==> Installing kubectl..."
	curl -fsSL -o /tmp/kubectl \
		"https://dl.k8s.io/release/$$(curl -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
	install /tmp/kubectl /usr/local/bin/kubectl
	rm /tmp/kubectl
	@echo "==> kubectl installed: $$(kubectl version --client --short 2>/dev/null)"

install: install-docker install-minikube install-kubectl

# ---------------------------------------------------------------------------
# Cluster
# ---------------------------------------------------------------------------

cluster-start:
	@echo "==> Starting minikube..."
	minikube start --driver=docker --force
	@echo "==> Enabling ingress addon..."
	minikube addons enable ingress
	@echo "==> Cluster ready."

cluster-stop:
	minikube stop

# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

build:
	@echo "==> Building ingestion-service..."
	docker build -t $(INGESTION_IMG) -f ingestion-service/Dockerfile .
	@echo "==> Building api-service..."
	docker build -t $(API_IMG) -f api-service/Dockerfile api-service/
	@echo "==> Building frontend..."
	docker build -t $(FRONTEND_IMG) frontend/
	@echo "==> All images built."

push:
	docker push $(INGESTION_IMG)
	docker push $(API_IMG)
	docker push $(FRONTEND_IMG)
	@echo "==> All images pushed."

# ---------------------------------------------------------------------------
# Deploy
# ---------------------------------------------------------------------------

up:
	@echo "==> Deploying to Kubernetes..."
	kubectl apply -f k8s/prispulsen.yaml
	@echo "==> Waiting for PostgreSQL..."
	kubectl wait pod/postgres-0 --for=condition=Ready -n $(NAMESPACE) --timeout=180s
	@echo "==> Waiting for migration job..."
	kubectl wait job/db-migrate --for=condition=Complete -n $(NAMESPACE) --timeout=120s
	@echo "==> Waiting for deployments..."
	kubectl rollout status deployment/prispulsen-ingestion -n $(NAMESPACE) --timeout=120s
	kubectl rollout status deployment/prispulsen-api -n $(NAMESPACE) --timeout=120s
	kubectl rollout status deployment/prispulsen-frontend -n $(NAMESPACE) --timeout=120s
	@echo "==> Stack is up. Run 'make open' to access the app."

down:
	kubectl delete namespace $(NAMESPACE) --ignore-not-found

restart:
	kubectl rollout restart deployment/prispulsen-ingestion -n $(NAMESPACE)
	kubectl rollout restart deployment/prispulsen-api -n $(NAMESPACE)
	kubectl rollout restart deployment/prispulsen-frontend -n $(NAMESPACE)

wipe: down
	@echo "==> Waiting for namespace to be fully deleted..."
	kubectl wait namespace/$(NAMESPACE) --for=delete --timeout=60s 2>/dev/null || true
	$(MAKE) up

# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

migrate:
	@echo "==> Re-running migrations..."
	kubectl delete job db-migrate -n $(NAMESPACE) --ignore-not-found
	kubectl apply -f k8s/prispulsen.yaml
	kubectl wait job/db-migrate --for=condition=Complete -n $(NAMESPACE) --timeout=120s
	kubectl logs job/db-migrate -n $(NAMESPACE)

ingestion:
	@echo "==> Triggering ingestion..."
	kubectl exec -n $(NAMESPACE) deploy/prispulsen-ingestion -- \
		python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/fetch', data=b'').read().decode())"
	@echo ""
	@echo "Check status with: make ingestion-status"

ingestion-status:
	kubectl exec -n $(NAMESPACE) deploy/prispulsen-ingestion -- \
		python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/fetch/status').read().decode())"

open:
	@echo "==> Forwarding frontend to http://localhost:8080 (Ctrl+C to stop)"
	kubectl port-forward svc/prispulsen-frontend 8080:8080 -n $(NAMESPACE)

start: up ingestion open

logs:
	@echo "==> Streaming logs (Ctrl+C to stop)..."
	kubectl logs -n $(NAMESPACE) -l app=prispulsen-api --tail=50 -f &
	kubectl logs -n $(NAMESPACE) -l app=prispulsen-ingestion --tail=50 -f &
	kubectl logs -n $(NAMESPACE) -l app=prispulsen-frontend --tail=20 -f

status:
	kubectl get pods,svc,hpa -n $(NAMESPACE)

# ---------------------------------------------------------------------------
# All-in-one
# ---------------------------------------------------------------------------

all: build push up open
