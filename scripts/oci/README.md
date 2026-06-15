# OCI Provisioner

Idempotent OCI infrastructure provisioner using the OCI CLI (no SDK).

## Prerequisites

- OCI CLI installed and configured (`~/.oci/config`)
- Python 3.10+ with `pyyaml`

## Quick Start

```bash
python3 oci_provision.py --config oci_infra_alimbocl.yaml --profile alimbocl \
  --compartment-id ocid1.compartment.oc1..aaaa...
```

## Setting Up a New Profile / Tenancy

### 1. Create OCI CLI profile

Add a section to `~/.oci/config`:

```ini
[myprofile]
user=ocid1.user.oc1..aaaa...
fingerprint=xx:xx:xx:...
tenancy=ocid1.tenancy.oc1..aaaa...
region=us-ashburn-1
key_file=~/.oci/oci_api_key.pem
```

### 2. Get or create a compartment

List existing compartments:

```bash
oci iam compartment list --profile myprofile --all \
  --query "data[?\"lifecycle-state\"=='ACTIVE'].[\"display-name\",id]" --output table
```

Create a new compartment (under root tenancy):

```bash
oci iam compartment create --profile myprofile \
  --name "my-infra" \
  --description "Infrastructure compartment" \
  --compartment-id <tenancy-ocid>
```

Extract the compartment ID:

```bash
oci iam compartment list --profile myprofile --all \
  --query "data[?\"display-name\"=='my-infra'].id | [0]" --raw-output
```

### 3. Get the availability domain prefix (CRITICAL)

**AD prefixes are tenancy-specific.** The prefix (e.g. `tIve:`, `UKly:`) differs per tenancy even in the same region. You MUST query this for each new profile:

```bash
oci iam availability-domain list --profile myprofile \
  --compartment-id <compartment-ocid> \
  --query 'data[*].name' --output table
```

Example output:

```
tIve:US-ASHBURN-AD-1
tIve:US-ASHBURN-AD-2
tIve:US-ASHBURN-AD-3
```

Use these exact values in your YAML config's `availability_domain` fields. **Do not copy AD values from another profile's config.**

### 4. Copy and customize the YAML config

```bash
cp oci_infra.yaml oci_infra_myprofile.yaml
```

Update in the new file:
- `availability_domain` — use the prefix from step 3
- `image_id` — region-specific, get a fresh one:

```bash
oci compute image list --profile myprofile \
  --compartment-id <compartment-ocid> \
  --operating-system "Canonical Ubuntu" \
  --query 'data[?contains("display-name",`24.04`)] | [0].[id,"display-name"]' --output table
```

### 5. Run

```bash
# Dry run first
python3 oci_provision.py --config oci_infra_myprofile.yaml --profile myprofile \
  --compartment-id <compartment-ocid> --dry-run

# Provision
python3 oci_provision.py --config oci_infra_myprofile.yaml --profile myprofile \
  --compartment-id <compartment-ocid>
```

## Usage

```bash
# Provision all
python3 oci_provision.py --config myenv.yaml --profile myprofile --compartment-id ocid1...

# Selective blocks
python3 oci_provision.py --config myenv.yaml --only vcn,igw,subnet

# Destroy
python3 oci_provision.py --config myenv.yaml --destroy --only compute
```

Valid `--only` tokens: `vcn`, `igw`, `routes`, `seclist`, `subnet`, `compute`

## Common Pitfalls

| Symptom | Cause |
|---------|-------|
| `CannotParseRequest` on compute launch | Wrong `availability_domain` prefix — run step 3 above |
| Subnet created without IPv6 | VCN must have `ipv6enabled: true` and subnet needs `ipv6cidr: "auto"` or explicit /64 |
| `OutOfHostCapacity` | Try a different AD/FD combo in the YAML |
