#!/usr/bin/env bash
# teardown.sh — Delete the entire prispulsen namespace and all its resources
# Run `chmod +x scripts/*.sh` once to make all scripts executable.
set -euo pipefail
echo "Deleting namespace prispulsen (this will delete all resources)..."
kubectl delete namespace prispulsen --ignore-not-found
echo "Done."
