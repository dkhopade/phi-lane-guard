#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 02 - Compute. Two instances:
#     gateway  : VM.Standard.E5.Flex in the PUBLIC subnet
#     model    : VM.GPU.A10.1        in the PRIVATE subnet
#
# *** BILLING WARNING ***
# The A10 GPU instance is the expensive resource in this PoC and is billed
# per hour while it exists, running or not (boot volume also bills).
# Check current pricing for your region before launching. Run teardown.sh
# the moment you finish demoing.
# ---------------------------------------------------------------------------
set -euo pipefail

: "${COMPARTMENT_OCID:?export COMPARTMENT_OCID first}"
: "${SSH_PUBKEY:=$HOME/.ssh/id_rsa.pub}"
STATE="$(dirname "$0")/.state"
source "$STATE"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
save() { echo "$1=$2" >> "$STATE"; }

AD=$(oci iam availability-domain list --compartment-id "$COMPARTMENT_OCID" \
      --query 'data[0].name' --raw-output)
say "Availability domain: $AD"

# Oracle Linux 8 image (adjust if your region differs)
IMAGE_ID=$(oci compute image list --compartment-id "$COMPARTMENT_OCID" \
  --operating-system "Oracle Linux" --operating-system-version "8" \
  --shape "VM.Standard.E5.Flex" --sort-by TIMECREATED \
  --query 'data[0].id' --raw-output)
say "Base image: $IMAGE_ID"

say "Launching GATEWAY instance (public subnet)"
GW_ID=$(oci compute instance launch \
  --compartment-id "$COMPARTMENT_OCID" --availability-domain "$AD" \
  --display-name phi-gateway --image-id "$IMAGE_ID" \
  --shape VM.Standard.E5.Flex \
  --shape-config '{"ocpus":2,"memoryInGBs":16}' \
  --subnet-id "$SUBNET_PUB" --assign-public-ip true \
  --ssh-authorized-keys-file "$SSH_PUBKEY" \
  --wait-for-state RUNNING --query 'data.id' --raw-output)
save GW_ID "$GW_ID"

GW_IP=$(oci compute instance list-vnics --instance-id "$GW_ID" \
  --query 'data[0]."public-ip"' --raw-output)
save GW_IP "$GW_IP"
echo "Gateway public IP: $GW_IP"

# GPU image — must be a GPU-enabled build for the driver stack
GPU_IMAGE_ID=$(oci compute image list --compartment-id "$COMPARTMENT_OCID" \
  --operating-system "Oracle Linux" --shape "VM.GPU.A10.1" \
  --sort-by TIMECREATED --query 'data[0].id' --raw-output)

say "Launching GPU MODEL instance (private subnet) — BILLING STARTS NOW"
GPU_ID=$(oci compute instance launch \
  --compartment-id "$COMPARTMENT_OCID" --availability-domain "$AD" \
  --display-name phi-model --image-id "$GPU_IMAGE_ID" \
  --shape VM.GPU.A10.1 \
  --subnet-id "$SUBNET_PRIV" --assign-public-ip false \
  --ssh-authorized-keys-file "$SSH_PUBKEY" \
  --boot-volume-size-in-gbs 200 \
  --wait-for-state RUNNING --query 'data.id' --raw-output)
save GPU_ID "$GPU_ID"

GPU_IP=$(oci compute instance list-vnics --instance-id "$GPU_ID" \
  --query 'data[0]."private-ip"' --raw-output)
save GPU_IP "$GPU_IP"

cat <<EOM

-----------------------------------------------------------------
  gateway : $GW_IP        (ssh opc@$GW_IP)
  model   : $GPU_IP  (private only — ssh via gateway as jump host)

  ssh -J opc@$GW_IP opc@$GPU_IP

  *** The GPU instance is now billing. teardown.sh when done. ***
-----------------------------------------------------------------
EOM
