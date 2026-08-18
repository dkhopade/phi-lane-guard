#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 04 - Audit bucket with versioning + a time-bound retention rule.
#
# GOTCHA, read before running: OCI retention rules can be LOCKED, which makes
# them immutable — and a locked bucket CANNOT be deleted until the retention
# period elapses, not even by a tenancy admin. Locking also has a 14-day
# delay before it becomes permanent.
#
# For a PoC we create the rule UNLOCKED. It demonstrates the control and the
# console shows the rule exists. Do not lock it unless you are prepared to
# leave the bucket in place. Say this out loud in the readout — knowing why
# you did not lock it reads better than having locked it by accident.
# ---------------------------------------------------------------------------
set -euo pipefail
: "${COMPARTMENT_OCID:?export COMPARTMENT_OCID first}"
BUCKET="${BUCKET:-phi-poc-audit}"

NS=$(oci os ns get --query 'data' --raw-output)
echo "Namespace: $NS"

oci os bucket create --compartment-id "$COMPARTMENT_OCID" \
  --name "$BUCKET" --namespace "$NS" --versioning Enabled >/dev/null
echo "Bucket created: $BUCKET (versioning ON)"

oci os retention-rule create --bucket-name "$BUCKET" --namespace "$NS" \
  --display-name poc-retain-7d \
  --time-amount 7 --time-unit DAYS >/dev/null
echo "Retention rule: 7 days, UNLOCKED"

echo "$BUCKET" > "$(dirname "$0")/.bucket"
echo
echo "Push audit records with:  ./05-push-audit.sh"
