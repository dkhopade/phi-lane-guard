#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 02 - Compute, fully unattended.
#
# Renders the cloud-init templates with your secrets, then launches both VMs.
# Nothing to SSH into afterwards: both hosts configure themselves and write a
# readiness marker. Poll with ./status.sh.
#
# *** BILLING ***
# The A10 bills per hour from launch until termination, running or not, and the
# boot volume bills alongside it. Set a teardown reminder now.
#
# *** SECRETS ***
# Cloud-init user-data is stored in instance metadata and is readable by anyone
# with access to the instance or permission to read its metadata. Acceptable
# for a PoC, NOT acceptable for production, where these belong in OCI Vault
# read via an instance principal. Stated plainly so nobody copies this by
# accident.
# ---------------------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
STATE="$HERE/.state"

: "${COMPARTMENT_OCID:?export COMPARTMENT_OCID first}"
: "${SSH_PUBKEY:=$HOME/.ssh/id_rsa.pub}"
: "${LITELLM_MASTER_KEY:?export LITELLM_MASTER_KEY (invent one, e.g. sk-poc-demo)}"
: "${ANTHROPIC_API_KEY:?export ANTHROPIC_API_KEY (or a placeholder to skip the frontier lane)}"
: "${HF_TOKEN:?export HF_TOKEN (needed for gated Llama repos)}"

REPO_URL="${REPO_URL:-https://github.com/dkhopade/phi-lane-guard.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
MODEL_NAME="${MODEL_NAME:-meta-llama/Llama-3.1-8B-Instruct}"
REGION="${OCI_REGION:-us-ashburn-1}"

# The model host is reached by VCN-internal DNS, never by IP. The IP changes on
# every rebuild; this name does not. Derived from the dns-labels in
# 01-network.sh: <hostname-label>.<subnet-dns-label>.<vcn-dns-label>
MODEL_FQDN="phi-model.private.phipoc.oraclevcn.com"

source "$STATE"
say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
save() { grep -v "^$1=" "$STATE" > "$STATE.tmp" 2>/dev/null || true
         mv "$STATE.tmp" "$STATE" 2>/dev/null || true; echo "$1=$2" >> "$STATE"; }

# --- guard: the private subnet needs egress for the weights pull -------------
CURRENT_RT=$(oci network subnet get --subnet-id "$SUBNET_PRIV" \
  --query 'data."route-table-id"' --raw-output)
if [ "$CURRENT_RT" = "$RT_SEALED" ]; then
  say "Private subnet is SEALED - unsealing so the model host can pull weights"
  oci network subnet update --subnet-id "$SUBNET_PRIV" \
    --route-table-id "$RT_BUILD" --force >/dev/null
  echo "    (re-seal with ./03-seal-subnet.sh seal once the model is ready)"
fi

AD=$(oci iam availability-domain list --compartment-id "$COMPARTMENT_OCID" \
      --query 'data[0].name' --raw-output)
say "Availability domain: $AD"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

export MODEL_NAME HF_TOKEN REPO_URL REPO_BRANCH LITELLM_MASTER_KEY \
       ANTHROPIC_API_KEY MODEL_FQDN REGION

render() {
  cp "$1" "$2"
  python3 - "$2" <<'PYEOF'
import os, sys
p = sys.argv[1]
s = open(p).read()
for k, env in {
    "__MODEL__":        "MODEL_NAME",
    "__HF_TOKEN__":     "HF_TOKEN",
    "__REPO__":         "REPO_URL",
    "__BRANCH__":       "REPO_BRANCH",
    "__MASTER_KEY__":   "LITELLM_MASTER_KEY",
    "__PROVIDER_KEY__": "ANTHROPIC_API_KEY",
    "__MODEL_FQDN__":   "MODEL_FQDN",
    "__REGION__":       "REGION",
}.items():
    s = s.replace(k, os.environ.get(env, ""))
open(p, "w").write(s)
PYEOF
}

render "$HERE/cloud-init/model.yaml"   "$TMP/model.yaml"
render "$HERE/cloud-init/gateway.yaml" "$TMP/gateway.yaml"
say "Cloud-init rendered (secrets injected at launch, never stored in the repo)"

IMAGE_ID=$(oci compute image list --compartment-id "$COMPARTMENT_OCID" \
  --operating-system "Oracle Linux" --operating-system-version "8" \
  --shape "VM.Standard.E5.Flex" --sort-by TIMECREATED \
  --query 'data[0].id' --raw-output)

say "Launching phi-gateway (self-configuring)"
GW_ID=$(oci compute instance launch \
  --compartment-id "$COMPARTMENT_OCID" --availability-domain "$AD" \
  --display-name phi-gateway --hostname-label phi-gateway \
  --image-id "$IMAGE_ID" --shape VM.Standard.E5.Flex \
  --shape-config '{"ocpus":2,"memoryInGBs":16}' \
  --subnet-id "$SUBNET_PUB" --assign-public-ip true \
  --ssh-authorized-keys-file "$SSH_PUBKEY" \
  --user-data-file "$TMP/gateway.yaml" \
  --wait-for-state RUNNING --query 'data.id' --raw-output)
save GW_ID "$GW_ID"
GW_IP=$(oci compute instance list-vnics --instance-id "$GW_ID" \
  --query 'data[0]."public-ip"' --raw-output)
save GW_IP "$GW_IP"

GPU_IMAGE_ID=$(oci compute image list --compartment-id "$COMPARTMENT_OCID" \
  --operating-system "Oracle Linux" --shape "VM.GPU.A10.1" \
  --sort-by TIMECREATED --query 'data[0].id' --raw-output)

say "Launching phi-model (self-configuring) - BILLING STARTS NOW"
GPU_ID=$(oci compute instance launch \
  --compartment-id "$COMPARTMENT_OCID" --availability-domain "$AD" \
  --display-name phi-model --hostname-label phi-model \
  --image-id "$GPU_IMAGE_ID" --shape VM.GPU.A10.1 \
  --subnet-id "$SUBNET_PRIV" --assign-public-ip false \
  --ssh-authorized-keys-file "$SSH_PUBKEY" \
  --boot-volume-size-in-gbs 200 \
  --user-data-file "$TMP/model.yaml" \
  --wait-for-state RUNNING --query 'data.id' --raw-output)
save GPU_ID "$GPU_ID"
GPU_IP=$(oci compute instance list-vnics --instance-id "$GPU_ID" \
  --query 'data[0]."private-ip"' --raw-output)
save GPU_IP "$GPU_IP"
save MODEL_FQDN "$MODEL_FQDN"

cat <<EOM

-----------------------------------------------------------------
  Both instances are RUNNING and configuring themselves.

  gateway : $GW_IP
  model   : $GPU_IP  (addressed as $MODEL_FQDN)

  Nothing to do by hand. Poll readiness:

      ./status.sh            once
      ./status.sh --watch    until both are ready

  Gateway typically ready in 6-9 min; model in 20-30 min on a cold
  boot volume, where the weights pull dominates.

  *** The GPU instance is billing. teardown.sh when done. ***
-----------------------------------------------------------------
EOM
