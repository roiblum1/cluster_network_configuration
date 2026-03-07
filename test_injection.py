#!/usr/bin/env python3
"""
Local test for VLAN injection without a live VLAN Manager API.
Patches make_api_call with realistic mock responses and runs both scenarios:
  1. AutomaticAllocation: false  -> cluster must be skipped
  2. Normal run                  -> vlanId + Networks injected into YAML
"""

import sys
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

from cluster_network_auto_configurator_production import (
    process_all_clusters,
    setup_logging,
)

# ── Mock values (edit to match expected real allocations) ─────────────────────
MOCK_MCE_SEGMENT    = "192.168.10.0/24"
MOCK_CLUSTER_SEGMENT = "192.168.100.0/24"
MOCK_VLAN_ID        = 100
MCE_NAME            = "ocp4-mce-roi"   # must match directory name
# ─────────────────────────────────────────────────────────────────────────────

YAML_PATH = Path("sites/five/mces/ocp4-mce-roi/hostedClusters/ocp4-didosh.yaml")
API_URL   = "http://mock-api/api"


def fake_api(endpoint, vlan_manager_url, session, method="GET", data=None, timeout=10):
    """Drop-in replacement for make_api_call during tests."""
    if "health" in endpoint:
        return {"status": "ok"}

    if "segments" in endpoint:
        # Return the MCE's pre-allocated segment so Networks section is built
        return [{"cluster_name": MCE_NAME, "segment": MOCK_MCE_SEGMENT, "vlan_id": 10}]

    if "allocate-vlan" in endpoint:
        cluster_name = (data or {}).get("cluster_name", "unknown")
        return {
            "cluster_name": cluster_name,
            "segment": MOCK_CLUSTER_SEGMENT,
            "vlan_id": MOCK_VLAN_ID,
        }

    return None


def run(dry_run=False):
    logger = setup_logging()
    with patch("cluster_network_auto_configurator_production.make_api_call", side_effect=fake_api):
        return process_all_clusters(Path("sites"), API_URL, dry_run=dry_run, logger=logger)


def separator(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def main():
    if not YAML_PATH.exists():
        print(f"ERROR: {YAML_PATH} not found. Run from repo root.")
        sys.exit(1)

    # Keep original so we can restore at the end
    backup = YAML_PATH.with_suffix(".yaml.bak")
    shutil.copy(YAML_PATH, backup)

    try:
        # ── TEST 1: AutomaticAllocation: false ───────────────────────────────
        separator("TEST 1 — AutomaticAllocation: false  (expect: SKIPPED)")

        original = YAML_PATH.read_text()
        YAML_PATH.write_text(original.rstrip() + "\nAutomaticAllocation: false\n")

        results = run()
        stats = results.get("stats", {})
        skipped = stats.get("skipped", 0)

        if skipped == 1:
            print("\n✅ PASS — cluster was correctly skipped")
        else:
            print(f"\n❌ FAIL — expected 1 skipped, got stats={stats}")

        # Restore to original before next test
        shutil.copy(backup, YAML_PATH)

        # ── TEST 2: Normal injection ─────────────────────────────────────────
        separator("TEST 2 — Normal injection (expect: vlanId + Networks written)")

        results = run(dry_run=False)
        stats = results.get("stats", {})

        errors = stats.get("errors", 0)
        if errors:
            print(f"\n❌ FAIL — errors reported: {stats}")
        else:
            print(f"\n✅ PASS — stats: {stats}")

        print(f"\n── Resulting YAML ({YAML_PATH}) ─────────────────────────────")
        print(YAML_PATH.read_text())

        print("── git diff (injected lines) ────────────────────────────────")
        subprocess.run(["git", "diff", "--", str(YAML_PATH)])

    finally:
        # ── Restore original ─────────────────────────────────────────────────
        shutil.copy(backup, YAML_PATH)
        backup.unlink()
        separator("Restored original YAML — repo is clean")


if __name__ == "__main__":
    main()
