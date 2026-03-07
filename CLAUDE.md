# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This repo contains Python scripts to automate VLAN allocation and network configuration for OpenShift clusters managed by MCE (Multi-Cluster Engine). The scripts interact with a VLAN Manager API and update cluster YAML files with `vlanId` and `Networks` configuration.

## Running the Scripts

```bash
# Install dependencies
pip install requests pyyaml

# Run production configurator (requires VLAN Manager API)
python3 cluster_network_auto_configurator_production.py --api-url http://0.0.0.0:8000/api

# Dry run (preview changes, no file modifications)
python3 cluster_network_auto_configurator_production.py --api-url http://0.0.0.0:8000/api --dry-run

# Legacy per-cluster configurator (manual usage)
python3 cluster_network_configurator.py <cluster_name> <cluster_segment>
# Example: python3 cluster_network_configurator.py ocp4-roi 10.1.100.0/24
```

## Architecture

**Two scripts, two use cases:**

- `cluster_network_auto_configurator_production.py` — The main production script. Scans all clusters in the `sites/` directory tree, fetches segments from the VLAN Manager API, allocates VLANs, and updates YAML files. Requires a live API connection.
- `cluster_network_configurator.py` — Older, simpler script for manually processing a single cluster by name and segment. Has a placeholder fallback if the API is unavailable.

**Directory structure the scripts expect:**
```
sites/
└── <site-name>/
    └── mce-tenant-clusters/   (or mce-tenant-cluster)
        └── mce-prod/ or mce-prep/
            └── <mce-name>/
                └── ocp4-*.yaml
```

**Key constants to configure** (edit at the top of `cluster_network_auto_configurator_production.py`):
- `DEFAULT_VRF`, `DEFAULT_API_URL`, `DEFAULT_API_USERNAME`, `DEFAULT_API_PASSWORD`
- `DEFAULT_PORTS`, `DEFAULT_PORT_RANGES` — ports written into the Networks config
- `SKIP_MCES` — list of MCE names to skip during processing (intended to be emptied after initialization)
- `SITE_LEVEL_UP` — how many directory levels to traverse up from a YAML file to reach the site directory (currently 4)

**YAML update logic** (in `update_cluster_yaml_smart`): The script reads existing YAML, strips any existing `vlanId` and `Networks` sections by line-parsing, then appends freshly generated ones. This hybrid approach preserves all other YAML content while replacing only the managed fields.

**Idempotency**: If `vlanId` and segment info already match the API response, the file is left untouched. The `/allocate-vlan` endpoint returns the existing allocation if a VLAN is already assigned.

**Edge case**: If the MCE parent cluster has no allocated segment in the API, `vlanId` is still written but the `Networks` section is omitted. Set `AutomaticAllocation: false` in a cluster YAML to exclude it from processing entirely.

## VLAN Manager API

The scripts expect these endpoints at the configured `--api-url`:
- `GET /health` — health check
- `GET /segments?allocated=true` — returns a JSON list of allocated segment objects with `cluster_name` and `segment` fields
- `POST /allocate-vlan` — body: `{cluster_name, site, vrf}`, returns `{vlan_id, segment, ...}`

Authentication uses HTTP Basic Auth (`DEFAULT_API_USERNAME`/`DEFAULT_API_PASSWORD`).
