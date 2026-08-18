#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Teardown. Run this the moment the demo is over — the GPU instance is the
# expensive item and it bills whether or not vLLM is running.
#
# Order matters: instances -> subnets -> route tables -> gateways -> VCN.
# ---------------------------------------------------------------------------
set -uo pipefail
STATE="$(dirname "$0")/.state"; source "$STATE"

say() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

say "Terminating GPU instance (stops the meter)"
oci compute instance terminate --instance-id "$GPU_ID" --force \
  --wait-for-state TERMINATED 2>/dev/null || echo "  already gone"

say "Terminating gateway instance"
oci compute instance terminate --instance-id "$GW_ID" --force \
  --wait-for-state TERMINATED 2>/dev/null || echo "  already gone"

say "Deleting subnets"
for s in "$SUBNET_PRIV" "$SUBNET_PUB"; do
  oci network subnet delete --subnet-id "$s" --force --wait-for-state TERMINATED 2>/dev/null || true
done

say "Deleting route tables"
for r in "$RT_BUILD" "$RT_SEALED" "$RT_PUB"; do
  oci network route-table delete --rt-id "$r" --force --wait-for-state TERMINATED 2>/dev/null || true
done

say "Deleting gateways"
oci network nat-gateway delete --nat-gateway-id "$NAT_ID" --force --wait-for-state TERMINATED 2>/dev/null || true
oci network internet-gateway delete --ig-id "$IGW_ID" --force --wait-for-state TERMINATED 2>/dev/null || true

say "Deleting security list"
oci network security-list delete --security-list-id "$SL_ID" --force --wait-for-state TERMINATED 2>/dev/null || true

say "Deleting VCN"
oci network vcn delete --vcn-id "$VCN_ID" --force --wait-for-state TERMINATED 2>/dev/null || true

cat <<'EOM'

-----------------------------------------------------------------
Teardown complete.

CHECK MANUALLY in the console:
  - Block Volumes  (boot volumes can survive instance termination)
  - Object Storage (the audit bucket is NOT deleted by this script)

If you applied a retention RULE with a LOCK to the audit bucket, the
bucket cannot be deleted until the retention period expires. This is
by design and it is why the setup script leaves the lock OFF.
-----------------------------------------------------------------
EOM
