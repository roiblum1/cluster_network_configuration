# Cluster Network Auto Configurator

Automated VLAN allocation and network configuration tool for OpenShift clusters managed by MCE (Multi-Cluster Engine).

## Overview

This tool automatically:
- Allocates VLANs for clusters via VLAN Manager API
- Updates cluster YAML files with `vlanId` and `Networks` configuration
- Creates bidirectional network rules between clusters and their MCE instances
- Maintains idempotency (safe to run multiple times)

## Features

- **Production-ready**: Direct integration with VLAN Manager API
- **Idempotent**: Running multiple times produces the same result
- **Smart updates**: Only updates YAML when values change
- **Segment caching**: Fetches all segments once to minimize API calls
- **Dry-run mode**: Preview changes before applying
- **Edge case handling**: Continues with vlanId insertion even if MCE segment is missing
- **Authentication**: HTTP Basic Auth support

## Requirements

- Python 3.7+
- `requests` library
- `PyYAML` library
- Access to VLAN Manager API

## Installation

```bash
pip install requests pyyaml
```

## Usage

### Basic Usage

```bash
python3 cluster_network_auto_configurator_production.py --api-url http://0.0.0.0:8000/api
```

### Dry Run Mode

Preview changes without modifying files:

```bash
python3 cluster_network_auto_configurator_production.py --api-url http://0.0.0.0:8000/api --dry-run
```

### Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--api-url` | VLAN Manager API URL | `http://0.0.0.0:8000/api` |
| `--dry-run` | Preview changes without modifying files | `False` |

## Configuration

### Constants (edit in script)

```python
# API Configuration
DEFAULT_VRF = "Network1"
DEFAULT_API_URL = "http://0.0.0.0:8000/api"
DEFAULT_API_USERNAME = "admin"
DEFAULT_API_PASSWORD = "admin"

# Network Configuration
DEFAULT_DOMAIN = "default"
DEFAULT_PORTS = [
    {"type": "port", "number": 80, "protocol": "TCP"},
    {"type": "port", "number": 8080, "protocol": "TCP"}
]

# Path Navigation
MCE_TENANT_DIR_VARIANTS = ["mce-tenant-clusters", "mce-tenant-cluster"]
MCE_ENVIRONMENTS = ["mce-prod", "mce-prep"]
```

## Directory Structure

The script expects the following directory structure:

```
sites/
├── site1/
│   └── mce-tenant-clusters/  (or mce-tenant-cluster)
│       ├── mce-prod/
│       │   └── mce-name/
│       │       └── ocp4-cluster-1.yaml
│       └── mce-prep/
│           └── mce-name/
│               └── ocp4-cluster-2.yaml
├── site2/
│   └── mce-tenant-clusters/
│       └── ...
└── site3/
    └── mce-tenant-clusters/
        └── ...
```

## YAML Output Format

The script updates cluster YAML files with:

```yaml
vlanId: 120
Networks:
  - number: 1
    domain: default
    from:
      segment: 192.168.110.0/24
      system-name: mce-site1-prod
    destination:
      segment: 192.168.120.0/24
      system-name: ocp4-cluster-1
    ports:
      - type: port
        number: 80
        protocol: TCP
      - type: port
        number: 8080
        protocol: TCP
  - number: 2
    domain: default
    from:
      segment: 192.168.120.0/24
      system-name: ocp4-cluster-1
    destination:
      segment: 192.168.110.0/24
      system-name: mce-site1-prod
    ports:
      - type: port
        number: 80
        protocol: TCP
      - type: port
        number: 8080
        protocol: TCP
```

## AutomaticAllocation Control

To exclude a cluster from automatic processing, add this to the cluster YAML:

```yaml
AutomaticAllocation: false
```

## VLAN Manager API Integration

### Required Endpoints

- `POST /auth/login` - Authentication (optional, uses Basic Auth)
- `GET /health` - Health check
- `GET /segments?allocated=true` - Fetch allocated segments
- `POST /allocate-vlan` - Allocate new VLAN

### Allocation Request Format

```json
{
  "cluster_name": "ocp4-cluster-1",
  "site": "site1",
  "vrf": "Network1"
}
```

### Allocation Response Format

```json
{
  "vlan_id": 120,
  "cluster_name": "ocp4-cluster-1",
  "site": "site1",
  "segment": "192.168.120.0/24",
  "epg_name": "backend-servers",
  "vrf": "Network1",
  "allocated_at": "2025-12-26T12:00:00Z"
}
```

## Error Handling

### Common Issues

**Issue**: HTTP 503 error on `/allocate-vlan`
- **Cause**: No available segments for the site/VRF combination
- **Solution**: Add more unallocated segments in VLAN Manager for the site

**Issue**: MCE segment not found
- **Behavior**: Script continues and inserts vlanId only (Networks section skipped)
- **Solution**: Allocate a VLAN segment for the MCE instance

**Issue**: Authentication failed
- **Cause**: Incorrect username/password
- **Solution**: Verify `DEFAULT_API_USERNAME` and `DEFAULT_API_PASSWORD` match VLAN Manager credentials

## Edge Cases Handled

1. **MCE without allocated segment**: Script inserts `vlanId` only, skips `Networks` section
2. **Multiple directory variants**: Supports both `mce-tenant-clusters` and `mce-tenant-cluster`
3. **Cluster already allocated**: Returns existing allocation (idempotent)
4. **AutomaticAllocation disabled**: Skips cluster processing
5. **YAML anchors/aliases**: Uses deep copy to prevent YAML anchor creation

## Output

The script provides detailed logging:

```
23:36:27 - INFO - 🔍 Scanning for clusters in all sites...
23:36:27 - INFO - Found 4 cluster(s)
23:36:27 - INFO - ✅ VLAN Manager API is available at http://0.0.0.0:8000/api
23:36:27 - INFO - 📦 Fetching all segments from VLAN manager...
23:36:27 - INFO - ✅ Cached 10 segments
23:36:27 - INFO - Processing cluster: ocp4-cluster-site2-1 (MCE: mce-site2-prod, Site: site2)
23:36:27 - INFO - 🔄 ocp4-cluster-site2-1.yaml: Updated (vlanId=120)
```

## Development

### Running Tests

```bash
# Dry run to preview changes
python3 cluster_network_auto_configurator_production.py --dry-run

# Run on specific site (modify script to filter)
python3 cluster_network_auto_configurator_production.py
```

### Logging

Logs include:
- Cluster scanning and discovery
- API authentication status
- VLAN allocation details
- YAML update actions
- Errors and warnings

## Files

- `cluster_network_auto_configurator_production.py` - Main production script
- `README.md` - This documentation
- `.gitignore` - Git ignore rules

## License

Internal use only.

## Support

For issues or questions, contact the infrastructure team.
