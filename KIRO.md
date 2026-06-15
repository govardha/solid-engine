# solid-engine

## Overview
Infrastructure-as-code repository for OCI (Oracle Cloud Infrastructure) provisioning and networking reference material.

## Structure
```
scripts/oci/          – CLI-driven provisioning tooling
  oci_provision.py    – Idempotent OCI provisioner (create/destroy via OCI CLI)
  oci_infra.yaml      – Declarative infra config (VCN, subnets, compute)
docs/oci/             – Setup guides (API key generation, etc.)
presentations/        – Technical presentations (TCP/IP foundations)
```

## Key Design Decisions
- No SDK dependency — provisioner shells out to `oci` CLI for portability and auditability.
- Idempotent — resources are checked before creation; re-runs are safe.
- Selective execution via `--only` blocks and `--dry-run` for safe iteration.
- YAML config is environment-agnostic — copy per environment, secrets passed via CLI flags.

## Critical: Setting Up a New Profile / Tenancy

### AD Prefixes Are Tenancy-Specific
The availability domain prefix (e.g. `tIve:`, `UKly:`) differs **per tenancy** even in the same region. When creating a new profile's YAML config, always query:

```bash
oci iam availability-domain list --profile <profile> \
  --compartment-id <compartment-ocid> \
  --query 'data[*].name' --output table
```

**Never copy `availability_domain` values between profiles.** Wrong prefix → `CannotParseRequest` (400) on compute launch with no useful error message.

### Compartment Setup
```bash
# List existing
oci iam compartment list --profile <profile> --all \
  --query "data[?\"lifecycle-state\"=='ACTIVE'].[\"display-name\",id]" --output table

# Create
oci iam compartment create --profile <profile> \
  --name "my-infra" --description "desc" --compartment-id <tenancy-ocid>

# Extract ID
oci iam compartment list --profile <profile> --all \
  --query "data[?\"display-name\"=='my-infra'].id | [0]" --raw-output
```

### IPv6 Subnet
- `ipv6cidr: "auto"` in YAML → script derives a /64 from the VCN's /56 prefix
- Cannot add IPv6 to an existing subnet — must destroy and recreate
- VCN must have `ipv6enabled: true`

### New Profile Workflow
1. Add profile to `~/.oci/config`
2. Create/identify compartment, extract OCID
3. Query ADs — update `availability_domain` in YAML
4. Query images — update `image_id` in YAML
5. `--dry-run` then provision

## Usage
```bash
# Dry-run full provision
python3 scripts/oci/oci_provision.py --config scripts/oci/oci_infra.yaml --dry-run

# Provision networking only
python3 scripts/oci/oci_provision.py --config scripts/oci/oci_infra.yaml --only vcn,igw,routes,seclist,subnet

# Launch compute
python3 scripts/oci/oci_provision.py --config scripts/oci/oci_infra.yaml --only compute

# Teardown
python3 scripts/oci/oci_provision.py --config scripts/oci/oci_infra.yaml --destroy
```

## Prerequisites
- `oci` CLI installed and configured (`~/.oci/config`) — see `docs/oci/gen_api_key.md`
- Python 3.10+ with `pyyaml`
- SSH keypair at `~/.ssh/id_ed25519.pub`
