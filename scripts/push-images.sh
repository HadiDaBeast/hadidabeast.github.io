#!/usr/bin/env bash
# push-images.sh — Push all Prispulsen Docker images to Docker Hub
# Usage: VERSION=v1.2.3 ./scripts/push-images.sh
# Run `chmod +x scripts/*.sh` once to make scripts executable.
set -euo pipefail

DOCKERHUB_USER="taylorbourne"
VERSION="${VERSION:-$(git describe --tags --always --dirty 2>/dev/null || echo 'dev')}"
COMMIT="${COMMIT:-$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')}"
TAG="${VERSION}-${COMMIT}"

echo "Pushing images with tag: ${TAG}"

for SERVICE in ingestion api frontend; do
    IMAGE="${DOCKERHUB_USER}/prispulsen-${SERVICE}"
    docker push "${IMAGE}:${TAG}"
    docker push "${IMAGE}:latest"
    echo "Pushed ${IMAGE}:${TAG}"
done
