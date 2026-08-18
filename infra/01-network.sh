#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 01 - Network. This script IS the security control.
#
# Creates:
#   - VCN 10.0.0.0/16
#   - public subnet  10.0.1.0/24  -> gateway VM, routes to Internet Gateway
#   - private subnet 10.0.2.0/24  -> GPU model host
#   - TWO route tables for the private subnet:
#       rt-private-build : has a NAT route (needed to pull model weights)
#       rt-private-sealed: no internet route at all (the demo state)
#
# The demo beat is flipping the private subnet from -build to -sealed and
# showing that the GPU host can no longer reach the internet, while the
# gateway can still reach it over the VCN.
# ---------------------------------------------------------------------------
set -euo pipefail

: "${COMPARTMENT_OCID:?export COMPARTMENT_OCID first}"
STATE="$(dirname "$0")/.state"
touch "$STATE"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
save() { grep -v "^$1=" "$STATE" > "$STATE.tmp" 2>/dev/null || true; mv "$STATE.tmp" "$STATE" 2>/dev/null || true; echo "$1=$2" >> "$STATE"; }

say "Creating VCN"
VCN_ID=$(oci network vcn create \
  --compartment-id "$COMPARTMENT_OCID" \
  --display-name phi-poc-vcn \
  --cidr-blocks '["10.0.0.0/16"]' \
  --dns-label phipoc \
  --wait-for-state AVAILABLE \
  --query 'data.id' --raw-output)
save VCN_ID "$VCN_ID"
echo "VCN: $VCN_ID"

say "Creating Internet Gateway"
IGW_ID=$(oci network internet-gateway create \
  --compartment-id "$COMPARTMENT_OCID" --vcn-id "$VCN_ID" \
  --is-enabled true --display-name phi-poc-igw \
  --wait-for-state AVAILABLE --query 'data.id' --raw-output)
save IGW_ID "$IGW_ID"

say "Creating NAT Gateway (build-time only)"
NAT_ID=$(oci network nat-gateway create \
  --compartment-id "$COMPARTMENT_OCID" --vcn-id "$VCN_ID" \
  --display-name phi-poc-nat \
  --wait-for-state AVAILABLE --query 'data.id' --raw-output)
save NAT_ID "$NAT_ID"

say "Route table: public (-> IGW)"
RT_PUB=$(oci network route-table create \
  --compartment-id "$COMPARTMENT_OCID" --vcn-id "$VCN_ID" \
  --display-name rt-public \
  --route-rules "[{\"destination\":\"0.0.0.0/0\",\"destinationType\":\"CIDR_BLOCK\",\"networkEntityId\":\"$IGW_ID\"}]" \
  --wait-for-state AVAILABLE --query 'data.id' --raw-output)
save RT_PUB "$RT_PUB"

say "Route table: private BUILD (-> NAT, temporary)"
RT_BUILD=$(oci network route-table create \
  --compartment-id "$COMPARTMENT_OCID" --vcn-id "$VCN_ID" \
  --display-name rt-private-build \
  --route-rules "[{\"destination\":\"0.0.0.0/0\",\"destinationType\":\"CIDR_BLOCK\",\"networkEntityId\":\"$NAT_ID\"}]" \
  --wait-for-state AVAILABLE --query 'data.id' --raw-output)
save RT_BUILD "$RT_BUILD"

say "Route table: private SEALED (no internet route)"
RT_SEALED=$(oci network route-table create \
  --compartment-id "$COMPARTMENT_OCID" --vcn-id "$VCN_ID" \
  --display-name rt-private-sealed \
  --route-rules '[]' \
  --wait-for-state AVAILABLE --query 'data.id' --raw-output)
save RT_SEALED "$RT_SEALED"

say "Security list: allow VCN-internal + SSH"
SL_ID=$(oci network security-list create \
  --compartment-id "$COMPARTMENT_OCID" --vcn-id "$VCN_ID" \
  --display-name sl-phi-poc \
  --egress-security-rules '[{"destination":"0.0.0.0/0","protocol":"all","isStateless":false}]' \
  --ingress-security-rules '[
     {"source":"10.0.0.0/16","protocol":"all","isStateless":false},
     {"source":"0.0.0.0/0","protocol":"6","isStateless":false,
      "tcpOptions":{"destinationPortRange":{"min":22,"max":22}}},
     {"source":"0.0.0.0/0","protocol":"6","isStateless":false,
      "tcpOptions":{"destinationPortRange":{"min":4000,"max":4000}}}
   ]' \
  --wait-for-state AVAILABLE --query 'data.id' --raw-output)
save SL_ID "$SL_ID"

say "Public subnet 10.0.1.0/24"
SUBNET_PUB=$(oci network subnet create \
  --compartment-id "$COMPARTMENT_OCID" --vcn-id "$VCN_ID" \
  --display-name sn-public --cidr-block 10.0.1.0/24 --dns-label public \
  --route-table-id "$RT_PUB" --security-list-ids "[\"$SL_ID\"]" \
  --wait-for-state AVAILABLE --query 'data.id' --raw-output)
save SUBNET_PUB "$SUBNET_PUB"

say "Private subnet 10.0.2.0/24 (starts in BUILD state)"
SUBNET_PRIV=$(oci network subnet create \
  --compartment-id "$COMPARTMENT_OCID" --vcn-id "$VCN_ID" \
  --display-name sn-private --cidr-block 10.0.2.0/24 --dns-label private \
  --route-table-id "$RT_BUILD" --security-list-ids "[\"$SL_ID\"]" \
  --prohibit-public-ip-on-vnic true \
  --wait-for-state AVAILABLE --query 'data.id' --raw-output)
save SUBNET_PRIV "$SUBNET_PRIV"

cat <<EOF

-----------------------------------------------------------------
Network ready. State written to $STATE

  public  subnet : $SUBNET_PUB   (gateway VM)
  private subnet : $SUBNET_PRIV  (GPU host, currently NAT-enabled)

The private subnet is intentionally NOT sealed yet — the GPU host
needs egress to pull model weights. You will seal it in Step 8.
-----------------------------------------------------------------
EOF
