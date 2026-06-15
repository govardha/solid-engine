#!/usr/bin/env python3
"""
oci_provision.py — idempotent OCI provisioner
Reads a YAML config, creates resources only if they don't exist.
No SDK — pure OCI CLI subprocess calls.

Usage:
    python3 oci_provision.py --config myenv.yaml               # provision all
    python3 oci_provision.py --config myenv.yaml --dry-run     # print, no execute
    python3 oci_provision.py --config myenv.yaml --destroy     # teardown all

    # Selective blocks — mix and match
    python3 oci_provision.py --config myenv.yaml --only vcn
    python3 oci_provision.py --config myenv.yaml --only vcn,igw,subnet
    python3 oci_provision.py --config myenv.yaml --only compute
    python3 oci_provision.py --config myenv.yaml --only compute --destroy

    Valid --only tokens: vcn, igw, routes, seclist, subnet, compute
"""

import argparse
import json
import os
import subprocess
import sys

import yaml

VALID_BLOCKS = ["vcn", "igw", "routes", "seclist", "subnet", "compute"]


# ── CLI wrapper ───────────────────────────────────────────────────────────────


def oci(args: list, profile: str, dry_run=False, capture_error=False) -> dict:
    """
    Run an OCI CLI command.
    capture_error=True  → return error details as dict instead of raising.
    capture_error=False → print error and sys.exit (default for network resources).
    """
    cmd = ["oci"] + args + ["--profile", profile]
    if dry_run:
        print(f"  [dry-run] {' '.join(cmd)}")
        return {}
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        if capture_error:
            # OCI CLI may return JSON error body or plain text — handle both
            try:
                err = json.loads(result.stderr)
            except Exception:
                # Plain text error — wrap it so caller always gets same shape
                err = {
                    "code": "UnknownError",
                    "message": result.stderr.strip()
                    or result.stdout.strip()
                    or "no output",
                }
            return {"__error__": True, "__detail__": err}
        # Hard failure — print full OCI error and exit
        try:
            err = json.loads(result.stderr)
            print(
                f"\n  ERROR [{err.get('code', '?')}]: {err.get('message', '')}",
                file=sys.stderr,
            )
            print(f"  request-id: {err.get('opc-request-id', '')}", file=sys.stderr)
        except Exception:
            print(f"\n  ERROR: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def is_error(result: dict) -> bool:
    return result.get("__error__", False)


# ── helpers ───────────────────────────────────────────────────────────────────


def find_resource(response: dict, name_field: str, name: str) -> dict | None:
    for item in response.get("data", []):
        if item.get(name_field) == name and item.get("lifecycle-state") not in (
            "TERMINATED",
            "TERMINATING",
        ):
            return item
    return None


def banner(msg: str):
    print(f"\n{'─' * 60}\n  {msg}\n{'─' * 60}")


def load_ssh_keys(inst_cfg: dict) -> str:
    """
    Resolve SSH keys — supports inline strings or paths to pubkey files.
    Falls back to global ssh_keys from top-level config if not set per-instance.
    """
    keys = inst_cfg.get("ssh_keys", [])
    resolved = []
    for k in keys:
        k = k.strip()
        if k.startswith("/") or k.startswith("~"):
            path = os.path.expanduser(k)
            with open(path) as f:
                resolved.append(f.read().strip())
        else:
            resolved.append(k)
    return "\n".join(resolved)


# ── provision functions ───────────────────────────────────────────────────────


def ensure_vcn(vcn_cfg: dict, compartment_id: str, profile: str, dry_run: bool) -> str:
    name = vcn_cfg["name"]
    banner(f"VCN: {name}")
    existing = oci(
        ["network", "vcn", "list", "--compartment-id", compartment_id, "--all"],
        profile,
        dry_run,
    )
    found = find_resource(existing, "display-name", name)
    if found:
        print(f"  ✓ exists  {found['id']}")
        return found["id"]
    cmd = [
        "network",
        "vcn",
        "create",
        "--compartment-id",
        compartment_id,
        "--cidr-block",
        vcn_cfg["cidr"],
        "--display-name",
        name,
        "--freeform-tags",
        json.dumps(vcn_cfg.get("tags", {})),
        "--wait-for-state",
        "AVAILABLE",
    ]
    if vcn_cfg.get("ipv6enabled"):
        cmd += ["--is-ipv6-enabled", "true"]
        if vcn_cfg.get("ipv6cidr"):
            cmd += ["--ipv6-cidr-blocks", json.dumps([vcn_cfg["ipv6cidr"]])]
    print(f"  + creating {name} ({vcn_cfg['cidr']}) ipv6={vcn_cfg.get('ipv6enabled', False)}")
    result = oci(cmd, profile, dry_run)
    vcn_id = result.get("data", {}).get("id", "<dry-run>")
    print(f"  ✓ created {vcn_id}")
    return vcn_id


def ensure_igw(
    igw_cfg: dict, vcn_id: str, compartment_id: str, profile: str, dry_run: bool
) -> str:
    name = igw_cfg["name"]
    banner(f"Internet Gateway: {name}")
    existing = oci(
        [
            "network",
            "internet-gateway",
            "list",
            "--compartment-id",
            compartment_id,
            "--vcn-id",
            vcn_id,
            "--all",
        ],
        profile,
        dry_run,
    )
    found = find_resource(existing, "display-name", name)
    if found:
        print(f"  ✓ exists  {found['id']}")
        return found["id"]
    print(f"  + creating {name}")
    result = oci(
        [
            "network",
            "internet-gateway",
            "create",
            "--compartment-id",
            compartment_id,
            "--vcn-id",
            vcn_id,
            "--display-name",
            name,
            "--is-enabled",
            str(igw_cfg.get("enabled", True)).lower(),
            "--wait-for-state",
            "AVAILABLE",
        ],
        profile,
        dry_run,
    )
    igw_id = result.get("data", {}).get("id", "<dry-run>")
    print(f"  ✓ created {igw_id}")
    return igw_id


def ensure_route_table(
    vcn_id: str,
    igw_id: str,
    rules_cfg: list,
    compartment_id: str,
    profile: str,
    dry_run: bool,
) -> str:
    banner("Default Route Table")
    vcn_detail = oci(["network", "vcn", "get", "--vcn-id", vcn_id], profile, dry_run)
    rt_id = vcn_detail.get("data", {}).get("default-route-table-id", "<dry-run>")
    print(f"  route table: {rt_id}")
    route_rules = []
    for rule in rules_cfg:
        entity_id = igw_id if rule["via"] == "internet_gateway" else rule["via"]
        dest = rule["destination"]
        dest_type = rule.get("destination_type", "CIDR_BLOCK")
        route_rules.append(
            {
                "destination": dest,
                "destinationType": dest_type,
                "networkEntityId": entity_id,
            }
        )
    print(f"  + updating with {len(route_rules)} rule(s)")
    oci(
        [
            "network",
            "route-table",
            "update",
            "--rt-id",
            rt_id,
            "--route-rules",
            json.dumps(route_rules),
            "--force",
        ],
        profile,
        dry_run,
    )
    print(f"  ✓ updated")
    return rt_id


def ensure_security_list(
    sl_cfg: dict, vcn_id: str, compartment_id: str, profile: str, dry_run: bool
) -> str:
    banner(f"Security List: {sl_cfg['name']}")
    vcn_detail = oci(["network", "vcn", "get", "--vcn-id", vcn_id], profile, dry_run)
    sl_id = vcn_detail.get("data", {}).get("default-security-list-id", "<dry-run>")
    print(f"  security list: {sl_id}")

    ingress_rules = []
    for rule in sl_cfg.get("ingress", []):
        source = rule["source"]
        source_type = "CIDR_BLOCK"
        entry = {
            "protocol": rule["protocol"],
            "source": source,
            "sourceType": source_type,
            "isStateless": False,
            "description": rule.get("description", ""),
        }
        if "port" in rule:
            entry["tcpOptions"] = {
                "destinationPortRange": {"min": rule["port"], "max": rule["port"]}
            }
        if "port_range" in rule:
            entry["tcpOptions"] = {
                "destinationPortRange": {
                    "min": rule["port_range"]["min"],
                    "max": rule["port_range"]["max"],
                }
            }
        ingress_rules.append(entry)

    egress_rules = []
    for rule in sl_cfg.get("egress", []):
        dest = rule["destination"]
        dest_type = "CIDR_BLOCK"
        egress_rules.append(
            {
                "protocol": rule["protocol"],
                "destination": dest,
                "destinationType": dest_type,
                "isStateless": False,
                "description": rule.get("description", ""),
            }
        )

    print(f"  + updating: {len(ingress_rules)} ingress / {len(egress_rules)} egress")
    oci(
        [
            "network",
            "security-list",
            "update",
            "--security-list-id",
            sl_id,
            "--ingress-security-rules",
            json.dumps(ingress_rules),
            "--egress-security-rules",
            json.dumps(egress_rules),
            "--force",
        ],
        profile,
        dry_run,
    )
    print(f"  ✓ updated")
    return sl_id


def _derive_ipv6_subnet_cidr(vcn_id: str, profile: str, dry_run: bool) -> str | None:
    """Derive a /64 from the VCN's /56 prefix for subnet auto-assignment."""
    if dry_run:
        return "<auto-ipv6>/64"
    vcn_detail = oci(["network", "vcn", "get", "--vcn-id", vcn_id], profile, dry_run)
    prefixes = vcn_detail.get("data", {}).get("ipv6-cidr-blocks", [])
    if not prefixes:
        return None
    # VCN prefix is /56 — derive first available /64 by appending 00
    # e.g. 2603:c020:401c:1f00::/56 → 2603:c020:401c:1f00::/64
    vcn_prefix = prefixes[0]  # e.g. "2603:c020:401c:1f00::/56"
    base = vcn_prefix.split("/")[0]
    return f"{base}/64"


def ensure_subnet(
    subnet_cfg: dict,
    vcn_id: str,
    rt_id: str,
    sl_id: str,
    compartment_id: str,
    profile: str,
    dry_run: bool,
) -> str:
    name = subnet_cfg["name"]
    banner(f"Subnet: {name}")
    existing = oci(
        [
            "network",
            "subnet",
            "list",
            "--compartment-id",
            compartment_id,
            "--vcn-id",
            vcn_id,
            "--all",
        ],
        profile,
        dry_run,
    )
    found = find_resource(existing, "display-name", name)
    if found:
        print(f"  ✓ exists  {found['id']}")
        return found["id"]
    public = subnet_cfg.get("public", True)
    print(f"  + creating {name} ({subnet_cfg['cidr']}) public={public}")
    cmd = [
        "network",
        "subnet",
        "create",
        "--compartment-id",
        compartment_id,
        "--vcn-id",
        vcn_id,
        "--cidr-block",
        subnet_cfg["cidr"],
        "--display-name",
        name,
        "--route-table-id",
        rt_id,
        "--security-list-ids",
        json.dumps([sl_id]),
        "--prohibit-public-ip-on-vnic",
        str(not public).lower(),
        "--wait-for-state",
        "AVAILABLE",
    ]
    ipv6cidr = subnet_cfg.get("ipv6cidr")
    if ipv6cidr and ipv6cidr != "auto":
        cmd += ["--ipv6-cidr-block", ipv6cidr]
    elif ipv6cidr == "auto":
        derived = _derive_ipv6_subnet_cidr(vcn_id, profile, dry_run)
        if derived:
            cmd += ["--ipv6-cidr-block", derived]
            print(f"    IPv6: {derived} (derived from VCN prefix)")
        else:
            print("    ⚠ VCN has no IPv6 prefix — skipping IPv6 on subnet")
    result = oci(cmd, profile, dry_run)
    subnet_id = result.get("data", {}).get("id", "<dry-run>")
    print(f"  ✓ created {subnet_id}")
    return subnet_id


def ensure_instance(
    inst_cfg: dict, subnet_map: dict, compartment_id: str, profile: str, dry_run: bool
) -> str | None:
    name = inst_cfg["name"]
    banner(f"Compute Instance: {name}")

    existing = oci(
        [
            "compute",
            "instance",
            "list",
            "--compartment-id",
            compartment_id,
            "--display-name",
            name,
            "--all",
        ],
        profile,
        dry_run,
    )
    found = find_resource(existing, "display-name", name)
    if found:
        state = found.get("lifecycle-state")
        print(f"  ✓ exists [{state}]  {found['id']}")
        return found["id"]

    subnet_name = inst_cfg["subnet"]
    subnet_id = subnet_map.get(subnet_name)
    if not subnet_id and not dry_run:
        print(
            f"  ERROR: subnet '{subnet_name}' not in subnet_map — run --only subnet first",
            file=sys.stderr,
        )
        return None

    ssh_keys = load_ssh_keys(inst_cfg)
    shape_config = {"ocpus": float(inst_cfg["ocpus"]), "memoryInGBs": float(inst_cfg["memory_gb"])}
    # Boot volume — size and performance tier
    # OCI default is 50GB / 10 VPUs if omitted — always be explicit
    boot_vol_size = inst_cfg.get("boot_volume_size_gb", 50)
    boot_vol_vpus = inst_cfg.get("boot_volume_vpus", 10)
    source_details = {
        "sourceType": "image",
        "imageId": inst_cfg["image_id"],
        "bootVolumeSizeInGBs": boot_vol_size,
        "bootVolumeVpusPerGB": boot_vol_vpus,
    }

    print(
        f"  + launching {name} | {inst_cfg['shape']} "
        f"{inst_cfg['ocpus']}cpu / {inst_cfg['memory_gb']}gb / "
        f"{boot_vol_size}gb boot @ {boot_vol_vpus} VPUs"
    )
    print(f"    AD: {inst_cfg['availability_domain']}  FD: {inst_cfg['fault_domain']}")

    assign_public_ip = str(inst_cfg.get("assign_public_ip", True)).lower()

    # capture_error=True — compute capacity errors should show up, not abort
    result = oci(
        [
            "compute",
            "instance",
            "launch",
            "--compartment-id",
            compartment_id,
            "--availability-domain",
            inst_cfg["availability_domain"],
            "--fault-domain",
            inst_cfg["fault_domain"],
            "--display-name",
            name,
            "--shape",
            inst_cfg["shape"],
            "--shape-config",
            json.dumps(shape_config),
            "--source-details",
            json.dumps(source_details),
            "--subnet-id",
            subnet_id or "<dry-run>",
            "--assign-public-ip",
            assign_public_ip,
            "--metadata",
            json.dumps({"ssh_authorized_keys": ssh_keys}),
            "--freeform-tags",
            json.dumps(inst_cfg.get("tags", {})),
            "--wait-for-state",
            "RUNNING",
        ],
        profile,
        dry_run,
        capture_error=True,
    )

    if is_error(result):
        detail = result["__detail__"]
        code = detail.get("code", "UnknownError")
        msg = detail.get("message", "no detail")
        # Capacity errors are expected — surface them clearly, continue to next instance
        if code in ("InternalError", "LimitExceeded", "OutOfCapacity", "QuotaExceeded"):
            print(f"\n  ✗ CAPACITY ERROR [{code}]: {msg}")
            print(f"    → try a different AD/FD or shape in the YAML")
        else:
            print(f"\n  ✗ LAUNCH FAILED [{code}]: {msg}")
        return None

    instance_id = result.get("data", {}).get("id", "<dry-run>")
    print(f"  ✓ running  {instance_id}")

    # Assign IPv6 address to primary VNIC if requested
    if inst_cfg.get("assign_ipv6") and instance_id != "<dry-run>":
        _assign_ipv6_to_instance(instance_id, compartment_id, profile, dry_run)

    return instance_id


def _assign_ipv6_to_instance(
    instance_id: str, compartment_id: str, profile: str, dry_run: bool
) -> None:
    """Assign an IPv6 address to the primary VNIC of an instance."""
    attachments = oci(
        [
            "compute",
            "vnic-attachment",
            "list",
            "--compartment-id",
            compartment_id,
            "--instance-id",
            instance_id,
        ],
        profile,
        dry_run,
        capture_error=True,
    )
    if is_error(attachments) or dry_run:
        return
    vnic_id = None
    for att in attachments.get("data", []):
        if att.get("lifecycle-state") == "ATTACHED":
            vnic_id = att.get("vnic-id")
            break
    if not vnic_id:
        print("  ⚠ could not find primary VNIC for IPv6 assignment")
        return
    # Check if IPv6 already assigned
    vnic_detail = oci(
        ["network", "vnic", "get", "--vnic-id", vnic_id],
        profile,
        dry_run,
        capture_error=True,
    )
    if not is_error(vnic_detail):
        existing_ipv6 = vnic_detail.get("data", {}).get("ipv6-addresses", [])
        if existing_ipv6:
            print(f"  ✓ IPv6 already assigned: {existing_ipv6[0]}")
            return
    # Assign IPv6
    result = oci(
        [
            "network",
            "ipv6",
            "create",
            "--vnic-id",
            vnic_id,
        ],
        profile,
        dry_run,
        capture_error=True,
    )
    if is_error(result):
        detail = result["__detail__"]
        print(f"  ⚠ IPv6 assignment failed: {detail.get('message', 'unknown')}")
    else:
        ip = result.get("data", {}).get("ip-address", "?")
        print(f"  ✓ IPv6 assigned: {ip}")


# ── destroy functions ─────────────────────────────────────────────────────────


def destroy_instance(inst_cfg: dict, compartment_id: str, profile: str, dry_run: bool):
    name = inst_cfg["name"]
    banner(f"DESTROY Instance: {name}")
    existing = oci(
        [
            "compute",
            "instance",
            "list",
            "--compartment-id",
            compartment_id,
            "--display-name",
            name,
            "--all",
        ],
        profile,
        dry_run,
    )
    found = find_resource(existing, "display-name", name)
    if not found:
        print(f"  ✓ already gone")
        return
    instance_id = found["id"]
    print(f"  - terminating {instance_id}")
    oci(
        [
            "compute",
            "instance",
            "terminate",
            "--instance-id",
            instance_id,
            "--wait-for-state",
            "TERMINATED",
            "--force",
        ],
        profile,
        dry_run,
    )
    print(f"  ✓ terminated")


def destroy_subnet(
    subnet_cfg: dict, vcn_id: str, compartment_id: str, profile: str, dry_run: bool
):
    name = subnet_cfg["name"]
    banner(f"DESTROY Subnet: {name}")
    existing = oci(
        [
            "network",
            "subnet",
            "list",
            "--compartment-id",
            compartment_id,
            "--vcn-id",
            vcn_id,
            "--all",
        ],
        profile,
        dry_run,
    )
    found = find_resource(existing, "display-name", name)
    if not found:
        print(f"  ✓ already gone")
        return
    subnet_id = found["id"]
    print(f"  - deleting {subnet_id}")
    oci(
        [
            "network",
            "subnet",
            "delete",
            "--subnet-id",
            subnet_id,
            "--wait-for-state",
            "TERMINATED",
            "--force",
        ],
        profile,
        dry_run,
    )
    print(f"  ✓ deleted")


def destroy_route_rules(vcn_id: str, profile: str, dry_run: bool):
    banner("DESTROY Route Rules (clear default RT)")
    vcn_detail = oci(["network", "vcn", "get", "--vcn-id", vcn_id], profile, dry_run)
    rt_id = vcn_detail.get("data", {}).get("default-route-table-id")
    if not rt_id:
        print("  ✓ no default route table found")
        return
    print(f"  - clearing rules on {rt_id}")
    oci(
        ["network", "route-table", "update", "--rt-id", rt_id, "--route-rules", "[]", "--force"],
        profile,
        dry_run,
    )
    print("  ✓ cleared")


def destroy_igw(
    igw_cfg: dict, vcn_id: str, compartment_id: str, profile: str, dry_run: bool
):
    name = igw_cfg["name"]
    banner(f"DESTROY IGW: {name}")
    existing = oci(
        [
            "network",
            "internet-gateway",
            "list",
            "--compartment-id",
            compartment_id,
            "--vcn-id",
            vcn_id,
            "--all",
        ],
        profile,
        dry_run,
    )
    found = find_resource(existing, "display-name", name)
    if not found:
        print(f"  ✓ already gone")
        return
    igw_id = found["id"]
    print(f"  - deleting {igw_id}")
    oci(
        [
            "network",
            "internet-gateway",
            "delete",
            "--ig-id",
            igw_id,
            "--wait-for-state",
            "TERMINATED",
            "--force",
        ],
        profile,
        dry_run,
    )
    print(f"  ✓ deleted")


def destroy_vcn(vcn_cfg: dict, compartment_id: str, profile: str, dry_run: bool):
    name = vcn_cfg["name"]
    banner(f"DESTROY VCN: {name}")
    existing = oci(
        ["network", "vcn", "list", "--compartment-id", compartment_id, "--all"],
        profile,
        dry_run,
    )
    found = find_resource(existing, "display-name", name)
    if not found:
        print(f"  ✓ already gone")
        return
    vcn_id = found["id"]
    for subnet_cfg in vcn_cfg.get("subnets", []):
        destroy_subnet(subnet_cfg, vcn_id, compartment_id, profile, dry_run)
    if "internet_gateway" in vcn_cfg:
        destroy_igw(
            vcn_cfg["internet_gateway"], vcn_id, compartment_id, profile, dry_run
        )
    print(f"  - deleting VCN {vcn_id}")
    oci(
        [
            "network",
            "vcn",
            "delete",
            "--vcn-id",
            vcn_id,
            "--wait-for-state",
            "TERMINATED",
            "--force",
        ],
        profile,
        dry_run,
    )
    print(f"  ✓ deleted")


# ── subnet resolver — needed when --only compute runs standalone ──────────────


def resolve_subnet_map(
    vcns: list, compartment_id: str, profile: str, dry_run: bool
) -> dict:
    """Query OCI for existing subnets and return name → OCID map."""
    subnet_map = {}
    for vcn_cfg in vcns:
        existing_vcns = oci(
            ["network", "vcn", "list", "--compartment-id", compartment_id, "--all"],
            profile,
            dry_run,
        )
        vcn = find_resource(existing_vcns, "display-name", vcn_cfg["name"])
        if not vcn:
            continue
        vcn_id = vcn["id"]
        existing_subnets = oci(
            [
                "network",
                "subnet",
                "list",
                "--compartment-id",
                compartment_id,
                "--vcn-id",
                vcn_id,
                "--all",
            ],
            profile,
            dry_run,
        )
        for s in existing_subnets.get("data", []):
            if s.get("lifecycle-state") not in ("TERMINATED", "TERMINATING"):
                subnet_map[s["display-name"]] = s["id"]
    return subnet_map


# ── main ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Idempotent OCI provisioner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"--only tokens: {', '.join(VALID_BLOCKS)}",
    )
    parser.add_argument(
        "--config",
        default="oci_infra.yaml",
        help="Path to YAML config (default: oci_infra.yaml)",
    )
    parser.add_argument(
        "--profile",
        default="DEFAULT",
        help="OCI CLI profile from ~/.oci/config (default: DEFAULT)",
    )
    parser.add_argument(
        "--compartment-id",
        default=None,
        dest="compartment_id",
        help="Compartment OCID — overrides value in YAML if set",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print commands, make no changes"
    )
    parser.add_argument(
        "--destroy", action="store_true", help="Tear down resources (reverse order)"
    )
    parser.add_argument(
        "--only",
        default="",
        help=f"Comma-separated subset of blocks to run: {','.join(VALID_BLOCKS)}",
    )
    args = parser.parse_args()

    # Validate --only tokens
    only = [t.strip() for t in args.only.split(",") if t.strip()]
    invalid = [t for t in only if t not in VALID_BLOCKS]
    if invalid:
        print(
            f"ERROR: unknown --only token(s): {invalid}\nValid: {VALID_BLOCKS}",
            file=sys.stderr,
        )
        sys.exit(1)
    run_all = not only

    def should_run(block: str) -> bool:
        return run_all or block in only

    # Load config
    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    # --profile arg wins; fallback is DEFAULT (Oracle's out-of-box section name)
    profile = args.profile

    # --compartment-id arg wins; fallback is YAML value; missing = hard fail
    compartment_id = args.compartment_id or cfg.get("compartment_id")
    if not compartment_id:
        print(
            "ERROR: compartment_id required — pass --compartment-id or set in YAML",
            file=sys.stderr,
        )
        sys.exit(1)

    dry_run = args.dry_run
    os.environ["OCI_CLI_PROFILE"] = profile

    print(f"profile        : {profile}")
    print(f"compartment_id : {compartment_id}")
    print(f"config         : {args.config}")
    print(f"dry_run        : {dry_run}")
    print(f"destroy        : {args.destroy}")
    print(f"blocks         : {only if only else 'all'}")

    # ── DESTROY ──────────────────────────────────────────────────────────────
    if args.destroy:
        if should_run("compute"):
            for inst_cfg in cfg.get("instances", []):
                destroy_instance(inst_cfg, compartment_id, profile, dry_run)
        if should_run("subnet"):
            for vcn_cfg in cfg.get("vcns", []):
                existing = oci(
                    [
                        "network",
                        "vcn",
                        "list",
                        "--compartment-id",
                        compartment_id,
                        "--all",
                    ],
                    profile,
                    dry_run,
                )
                vcn = find_resource(existing, "display-name", vcn_cfg["name"])
                if vcn:
                    for subnet_cfg in vcn_cfg.get("subnets", []):
                        destroy_subnet(
                            subnet_cfg, vcn["id"], compartment_id, profile, dry_run
                        )
        if should_run("routes") or should_run("igw"):
            for vcn_cfg in cfg.get("vcns", []):
                existing = oci(
                    [
                        "network",
                        "vcn",
                        "list",
                        "--compartment-id",
                        compartment_id,
                        "--all",
                    ],
                    profile,
                    dry_run,
                )
                vcn = find_resource(existing, "display-name", vcn_cfg["name"])
                if vcn:
                    destroy_route_rules(vcn["id"], profile, dry_run)
        if should_run("igw"):
            for vcn_cfg in cfg.get("vcns", []):
                existing = oci(
                    [
                        "network",
                        "vcn",
                        "list",
                        "--compartment-id",
                        compartment_id,
                        "--all",
                    ],
                    profile,
                    dry_run,
                )
                vcn = find_resource(existing, "display-name", vcn_cfg["name"])
                if vcn and "internet_gateway" in vcn_cfg:
                    destroy_igw(
                        vcn_cfg["internet_gateway"],
                        vcn["id"],
                        compartment_id,
                        profile,
                        dry_run,
                    )
        if should_run("vcn"):
            for vcn_cfg in cfg.get("vcns", []):
                destroy_vcn(vcn_cfg, compartment_id, profile, dry_run)
        print("\n✓ done")
        return

    # ── PROVISION ────────────────────────────────────────────────────────────
    subnet_map = {}

    for vcn_cfg in cfg.get("vcns", []):
        vcn_id = None

        if should_run("vcn"):
            vcn_id = ensure_vcn(vcn_cfg, compartment_id, profile, dry_run)

        # IGW, routes, seclist all need vcn_id — resolve it if not just created
        if vcn_id is None:
            existing = oci(
                ["network", "vcn", "list", "--compartment-id", compartment_id, "--all"],
                profile,
                dry_run,
            )
            vcn = find_resource(existing, "display-name", vcn_cfg["name"])
            vcn_id = vcn["id"] if vcn else "<not-found>"

        igw_id = None
        if should_run("igw") and "internet_gateway" in vcn_cfg:
            igw_id = ensure_igw(
                vcn_cfg["internet_gateway"], vcn_id, compartment_id, profile, dry_run
            )

        rt_id = None
        if should_run("routes") and "route_rules" in vcn_cfg:
            if igw_id is None:
                # resolve existing IGW OCID if not just created
                existing_igws = oci(
                    [
                        "network",
                        "internet-gateway",
                        "list",
                        "--compartment-id",
                        compartment_id,
                        "--vcn-id",
                        vcn_id,
                        "--all",
                    ],
                    profile,
                    dry_run,
                )
                igw = find_resource(
                    existing_igws, "display-name", vcn_cfg["internet_gateway"]["name"]
                )
                igw_id = igw["id"] if igw else "<not-found>"
            rt_id = ensure_route_table(
                vcn_id, igw_id, vcn_cfg["route_rules"], compartment_id, profile, dry_run
            )

        sl_id = None
        if should_run("seclist") and "security_list" in vcn_cfg:
            sl_id = ensure_security_list(
                vcn_cfg["security_list"], vcn_id, compartment_id, profile, dry_run
            )

        if should_run("subnet"):
            # Resolve rt_id and sl_id from OCI if not set in this run
            if rt_id is None:
                vcn_detail = oci(
                    ["network", "vcn", "get", "--vcn-id", vcn_id], profile, dry_run
                )
                rt_id = vcn_detail.get("data", {}).get(
                    "default-route-table-id", "<dry-run>"
                )
            if sl_id is None:
                vcn_detail = oci(
                    ["network", "vcn", "get", "--vcn-id", vcn_id], profile, dry_run
                )
                sl_id = vcn_detail.get("data", {}).get(
                    "default-security-list-id", "<dry-run>"
                )
            for subnet_cfg in vcn_cfg.get("subnets", []):
                sid = ensure_subnet(
                    subnet_cfg, vcn_id, rt_id, sl_id, compartment_id, profile, dry_run
                )
                subnet_map[subnet_cfg["name"]] = sid

    if should_run("compute") and cfg.get("instances"):
        # If subnets weren't provisioned in this run, look them up live
        if not subnet_map:
            subnet_map = resolve_subnet_map(
                cfg.get("vcns", []), compartment_id, profile, dry_run
            )
        failed = []
        for inst_cfg in cfg.get("instances", []):
            result = ensure_instance(
                inst_cfg, subnet_map, compartment_id, profile, dry_run
            )
            if result is None:
                failed.append(inst_cfg["name"])
        if failed:
            print(f"\n  ⚠ compute errors on: {failed}")
            print(f"    check AD/FD availability or try a different shape")

    print("\n✓ done")


if __name__ == "__main__":
    main()
