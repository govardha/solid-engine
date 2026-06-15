# solid-engine

## Overview
Infrastructure-as-code repository for OCI (Oracle Cloud Infrastructure) provisioning and networking reference material.

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

```bash
# List existing
oci iam compartment list --profile myprofile --all \
  --query "data[?\"lifecycle-state\"=='ACTIVE'].[\"display-name\",id]" --output table

# Create (under root tenancy)
oci iam compartment create --profile myprofile \
  --name "my-infra" --description "Infrastructure compartment" \
  --compartment-id <tenancy-ocid>

# Extract ID
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

Use these exact values in your YAML config's `availability_domain` fields. **Do not copy AD values from another profile's config** — this causes `CannotParseRequest` on compute launch with no useful error message.

### 4. Copy and customize the YAML config

```bash
cp scripts/oci/oci_infra.yaml scripts/oci/oci_infra_myprofile.yaml
```

Update:
- `availability_domain` — use the prefix from step 3
- `image_id` — get a fresh one for the region:

```bash
oci compute image list --profile myprofile \
  --compartment-id <compartment-ocid> \
  --operating-system "Canonical Ubuntu" \
  --query 'data[?contains("display-name",`24.04`)] | [0].[id,"display-name"]' --output table
```

### 5. Run

```bash
# Dry run
python3 scripts/oci/oci_provision.py --config scripts/oci/oci_infra_myprofile.yaml \
  --profile myprofile --compartment-id <compartment-ocid> --dry-run

# Provision
python3 scripts/oci/oci_provision.py --config scripts/oci/oci_infra_myprofile.yaml \
  --profile myprofile --compartment-id <compartment-ocid>
```