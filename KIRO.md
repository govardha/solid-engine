# solid-engine

## Overview
Infrastructure-as-code repository for OCI (Oracle Cloud Infrastructure) provisioning and networking reference material.

## Structure
```
scripts/oci/          – CLI-driven provisioning tooling
  oci_provision.py    – Idempotent OCI provisioner (create/destroy via OCI CLI)
  oci_infra.yml       – Declarative infra config (VCN, subnets, compute)
docs/oci/             – Setup guides (API key generation, etc.)
presentations/        – Technical presentations (TCP/IP foundations)
```

## Key Design Decisions
- No SDK dependency — provisioner shells out to `oci` CLI for portability and auditability.
- Idempotent — resources are checked before creation; re-runs are safe.
- Selective execution via `--only` blocks and `--dry-run` for safe iteration.
- YAML config is environment-agnostic — copy per environment, secrets passed via CLI flags.

## Usage
```bash
# Dry-run full provision
python3 scripts/oci/oci_provision.py --config scripts/oci/oci_infra.yml --dry-run

# Provision networking only
python3 scripts/oci/oci_provision.py --config scripts/oci/oci_infra.yml --only vcn,igw,routes,seclist,subnet

# Launch compute
python3 scripts/oci/oci_provision.py --config scripts/oci/oci_infra.yml --only compute

# Teardown
python3 scripts/oci/oci_provision.py --config scripts/oci/oci_infra.yml --destroy
```

## Prerequisites
- `oci` CLI installed and configured (`~/.oci/config`) — see `docs/oci/gen_api_key.md`
- Python 3.10+ with `pyyaml`
- SSH keypair at `~/.ssh/id_ed25519.pub`
