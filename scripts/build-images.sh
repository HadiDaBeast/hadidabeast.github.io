#!/usr/bin/env bash
# build-images.sh — Build all Prispulsen Docker images
# Usage: VERSION=v1.2.3 ./scripts/build-images.sh
# Run `chmod +x scripts/*.sh` once to make scripts executable.
set -euo pipefail

DOCKERHUB_USER="hadidabeast"
VERSION="${VERSION:-$(git describe --tags --always --dirty 2>/dev/null || echo 'dev')}"
COMMIT="${COMMIT:-$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')}"
TAG="${VERSION}-${COMMIT}"

echo "Building images with tag: ${TAG}"

docker build -t "${DOCKERHUB_USER}/prispulsen-ingestion:${TAG}" \
             -t "${DOCKERHUB_USER}/prispulsen-ingestion:latest" \
             --build-arg APP_VERSION="${TAG}" \
             ./ingestion-service

docker build -t "${DOCKERHUB_USER}/prispulsen-api:${TAG}" \
             -t "${DOCKERHUB_USER}/prispulsen-api:latest" \
             --build-arg APP_VERSION="${TAG}" \
             ./api-service

docker build -t "${DOCKERHUB_USER}/prispulsen-frontend:${TAG}" \
             -t "${DOCKERHUB_USER}/prispulsen-frontend:latest" \
             --build-arg APP_VERSION="${TAG}" \
             ./frontend

echo "Built images:"
echo "  ${DOCKERHUB_USER}/prispulsen-ingestion:${TAG}"
echo "  ${DOCKERHUB_USER}/prispulsen-api:${TAG}"
echo "  ${DOCKERHUB_USER}/prispulsen-frontend:${TAG}"
