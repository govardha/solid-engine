#!/bin/bash
set -euo pipefail

PROFILE="${1:-DEFAULT}"
OUTPUT_DIR="./oci_snapshot_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTPUT_DIR"

export OCI_CLI_PROFILE="$PROFILE"

# Pull tenancy OCID via API — reliable regardless of config formatting
TENANCY_OCID=$(oci iam compartment list \
  --include-root \
  --all \
  --query "data[?\"lifecycle-state\"=='ACTIVE'] | [0].id" \
  --raw-output)

if [[ -z "$TENANCY_OCID" ]]; then
  echo "ERROR: could not resolve tenancy OCID for profile [$PROFILE]"
  exit 1
fi

echo "Profile  : $PROFILE"
echo "Tenancy  : $TENANCY_OCID"

# Dump compartments
echo "→ Dumping compartments..."
oci iam compartment list \
  --compartment-id "$TENANCY_OCID" \
  --all \
  --output json >"$OUTPUT_DIR/compartments.json"

COMPARTMENTS=$(jq -r '.data[].id' "$OUTPUT_DIR/compartments.json" 2>/dev/null || echo "")

for COMP in $TENANCY_OCID $COMPARTMENTS; do
  SAFE=$(echo "$COMP" | tr '.' '_' | tail -c 20)

  echo "→ VCNs in $SAFE..."
  oci network vcn list \
    --compartment-id "$COMP" --all --output json \
    >"$OUTPUT_DIR/vcns_${SAFE}.json" 2>/dev/null || true

  echo "→ Subnets..."
  oci network subnet list \
    --compartment-id "$COMP" --all --output json \
    >"$OUTPUT_DIR/subnets_${SAFE}.json" 2>/dev/null || true

  echo "→ Security lists..."
  oci network security-list list \
    --compartment-id "$COMP" --all --output json \
    >"$OUTPUT_DIR/security_lists_${SAFE}.json" 2>/dev/null || true

  echo "→ Route tables..."
  oci network route-table list \
    --compartment-id "$COMP" --all --output json \
    >"$OUTPUT_DIR/route_tables_${SAFE}.json" 2>/dev/null || true

  echo "→ Internet gateways..."
  oci network internet-gateway list \
    --compartment-id "$COMP" --all --output json \
    >"$OUTPUT_DIR/igw_${SAFE}.json" 2>/dev/null || true

  echo "→ NAT gateways..."
  oci network nat-gateway list \
    --compartment-id "$COMP" --all --output json \
    >"$OUTPUT_DIR/nat_${SAFE}.json" 2>/dev/null || true

  echo "→ IPv6 prefixes (VCN-level)..."
  for VCN_ID in $(jq -r '.data[]?.id // empty' "$OUTPUT_DIR/vcns_${SAFE}.json" 2>/dev/null); do
    VCN_SHORT=$(echo "$VCN_ID" | tail -c 20)
    if VCN_JSON=$(oci network vcn get --vcn-id "$VCN_ID" --output json 2>/dev/null); then
      echo "$VCN_JSON" | jq '{ipv6_cidrs: .data."ipv6-cidr-blocks" // [], ipv6_public_cidrs: .data."ipv6-public-cidr-blocks" // []}' \
        >"$OUTPUT_DIR/ipv6_vcn_detail_${VCN_SHORT}.json" 2>/dev/null || true
    fi
  done

  echo "→ IPv6 prefixes (subnet-level)..."
  for SUBNET_ID in $(jq -r '.data[]?.id // empty' "$OUTPUT_DIR/subnets_${SAFE}.json" 2>/dev/null); do
    SUBNET_SHORT=$(echo "$SUBNET_ID" | tail -c 20)
    if SUBNET_JSON=$(oci network subnet get --subnet-id "$SUBNET_ID" --output json 2>/dev/null); then
      echo "$SUBNET_JSON" | jq '{ipv6_cidrs: .data."ipv6-cidr-blocks" // []}' \
        >"$OUTPUT_DIR/ipv6_subnet_${SUBNET_SHORT}.json" 2>/dev/null || true
    fi
  done

  echo "→ Compute instances..."
  oci compute instance list \
    --compartment-id "$COMP" --all --output json \
    >"$OUTPUT_DIR/instances_${SAFE}.json" 2>/dev/null || true

  echo "→ Block volumes..."
  oci bv volume list \
    --compartment-id "$COMP" --all --output json \
    >"$OUTPUT_DIR/volumes_${SAFE}.json" 2>/dev/null || true

  echo "→ Load balancers..."
  oci lb load-balancer list \
    --compartment-id "$COMP" --all --output json \
    >"$OUTPUT_DIR/load_balancers_${SAFE}.json" 2>/dev/null || true

done

echo ""
echo "✓ Snapshot complete: $OUTPUT_DIR"
find "$OUTPUT_DIR" -name "*.json" -size +2c | sort
