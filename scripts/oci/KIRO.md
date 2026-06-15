# OCI Provisioner — Agent Context

## What This Is

Idempotent OCI infra provisioner (`oci_provision.py`). Reads a YAML config, creates/updates VCN + subnet + IGW + routes + security list + compute instances via OCI CLI subprocess calls.

## Critical Knowledge

### Availability Domain Prefixes Are Tenancy-Specific

The AD prefix (e.g. `tIve:`, `UKly:`) differs **per tenancy** even in the same region. When setting up a new profile's YAML config, always query:

```bash
oci iam availability-domain list --profile <profile> \
  --compartment-id <compartment-ocid> \
  --query 'data[*].name' --output table
```

**Never copy `availability_domain` values from another profile's YAML.** Using the wrong prefix causes `CannotParseRequest` (400) on compute launch — OCI gives no useful error message.

### IPv6 Subnet Creation

- `ipv6cidr: "auto"` in YAML → script derives a /64 from the VCN's /56 prefix
- You cannot add IPv6 to an existing subnet — must destroy and recreate
- VCN must have `ipv6enabled: true` for subnet IPv6 to work

### Compartment Setup for New Profile

```bash
# List compartments
oci iam compartment list --profile <profile> --all \
  --query "data[?\"lifecycle-state\"=='ACTIVE'].[\"display-name\",id]" --output table

# Create compartment
oci iam compartment create --profile <profile> \
  --name "my-infra" --description "desc" --compartment-id <tenancy-ocid>

# Extract ID
oci iam compartment list --profile <profile> --all \
  --query "data[?\"display-name\"=='my-infra'].id | [0]" --raw-output
```

## File Layout

- `oci_provision.py` — main provisioner script
- `oci_infra.yaml` — base/template config (uses `UKly:` prefix for freestar tenancy)
- `oci_infra_alimbocl.yaml` — alimbocl profile config (uses `tIve:` prefix)

## Workflow

1. Copy `oci_infra.yaml` → `oci_infra_<profile>.yaml`
2. Query ADs for the new tenancy — update `availability_domain` fields
3. Query image list for region — update `image_id`
4. `--dry-run` first, then provision

## Valid --only Tokens

`vcn`, `igw`, `routes`, `seclist`, `subnet`, `compute`
