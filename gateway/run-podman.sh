#!/usr/bin/env bash
# Fallback for hosts where docker compose / podman-compose is unavailable.
# Oracle Linux 8 ships python3.6, which cannot run current podman-compose
# (SyntaxError on walrus operator). Plain podman has fewer moving parts.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p audit
sudo podman build -t phi-gateway .
sudo podman rm -f phi-gateway 2>/dev/null || true
sudo podman run -d --name phi-gateway \
  -p 4000:4000 \
  --env-file .env \
  -e POLICY_PATH=/app/policy.yaml \
  -e AUDIT_PATH=/app/audit/audit.jsonl \
  -v "$(pwd)/audit:/app/audit:Z" \
  localhost/phi-gateway:latest
sleep 20
sudo podman logs --tail 20 phi-gateway
echo
echo "Confirm the hook registered (look for PHILaneGuard):"
curl -s localhost:4000/health/readiness; echo
