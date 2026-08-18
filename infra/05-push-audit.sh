#!/usr/bin/env bash
# Ship the local audit log to Object Storage. Run after a demo pass.
set -euo pipefail
BUCKET=$(cat "$(dirname "$0")/.bucket")
NS=$(oci os ns get --query 'data' --raw-output)
STATE="$(dirname "$0")/.state"; source "$STATE"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
scp opc@"$GW_IP":~/phi-lane-poc/gateway/audit/audit.jsonl "/tmp/audit-$STAMP.jsonl"

oci os object put --bucket-name "$BUCKET" --namespace "$NS" \
  --file "/tmp/audit-$STAMP.jsonl" \
  --name "audit/$STAMP.jsonl" --force >/dev/null

echo "Uploaded audit/$STAMP.jsonl to $BUCKET"
echo "Records: $(wc -l < "/tmp/audit-$STAMP.jsonl")"
