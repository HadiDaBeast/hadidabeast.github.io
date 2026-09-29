INGESTION_IMG = hadidabeast/prispulsen-ingestion:latest
API_IMG = hadidabeast/prispulsen-api:latest

.PHONY: build push cluster-start cluster-stop up down restart wipe migrate ingestion ingestion-status open logs status start

build:
	docker build -t $(INGESTION_IMG) -f ingestion-service/Dockerfile .
	docker build -t $(API_IMG) -f api-service/Dockerfile api-service/

cluster-start:
	minikube start --driver=docker --force
	minikube addons enable ingress

cluster-stop:
	minikube stop

push:
	docker push $(INGESTION_IMG)
	docker push $(API_IMG)

up:
	kubectl apply -f k8s/prispulsen.yaml
	kubectl wait pod/postgres-0 --for=condition=Ready -n prispulsen --timeout=180s
	kubectl wait job/db-migrate --for=condition=Complete -n prispulsen --timeout=120s
	kubectl rollout status deployment/prispulsen-ingestion -n prispulsen --timeout=120s
	kubectl rollout status deployment/prispulsen-api -n prispulsen --timeout=120s

down:
	kubectl delete namespace prispulsen --ignore-not-found

restart:
	kubectl rollout restart deployment/prispulsen-ingestion -n prispulsen
	kubectl rollout restart deployment/prispulsen-api -n prispulsen

wipe: down
	kubectl wait namespace/prispulsen --for=delete --timeout=60s 2>/dev/null || true
	$(MAKE) up

migrate:
	kubectl delete job db-migrate -n prispulsen --ignore-not-found
	kubectl apply -f k8s/prispulsen.yaml
	kubectl wait job/db-migrate --for=condition=Complete -n prispulsen --timeout=120s
	kubectl logs job/db-migrate -n prispulsen

ingestion:
	kubectl exec -n prispulsen deploy/prispulsen-ingestion -- \
		python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/fetch', data=b'').read().decode())"

ingestion-status:
	kubectl exec -n prispulsen deploy/prispulsen-ingestion -- \
		python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/fetch/status').read().decode())"

open:
	kubectl port-forward svc/prispulsen-api 8001:8001 -n prispulsen

start: up ingestion open

logs:
	kubectl logs -n prispulsen -l app=prispulsen-api --tail=50 -f &
	kubectl logs -n prispulsen -l app=prispulsen-ingestion --tail=50 -f

status:
	kubectl get pods,svc -n prispulsen
