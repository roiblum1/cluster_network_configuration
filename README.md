# Cluster Network Configuration Scripts

Two Python scripts for managing cluster network configurations with VLAN allocation.

## Scripts Overview

### 1. `cluster_network_configurator.py` - Simple Single-Cluster Script
**Use for**: Manual configuration of individual clusters

**Usage**:
```bash
python cluster_network_configurator.py <cluster_name> <cluster_segment>
```

**Example**:
```bash
python cluster_network_configurator.py ocp4-roi-3 10.8.150.0/24
```

**Features**:
- ✅ Processes one cluster at a time
- ✅ Extracts MCE name from directory structure
- ✅ Makes API calls to VLAN manager (with graceful fallback)
- ✅ Adds Networks configuration to YAML bottom
- ✅ Skips files that already have Networks configured

---

### 2. `cluster_network_auto_configurator_final.py` - Production Batch Script
**Use for**: Automated processing of all clusters in infrastructure

**Usage**:
```bash
# Dry-run mode (test without changes)
python cluster_network_auto_configurator_final.py --dry-run

# Production mode
python cluster_network_auto_configurator_final.py

# With custom log level
python cluster_network_auto_configurator_final.py --log-level WARNING

# With custom API URL
python cluster_network_auto_configurator_final.py --api-url http://vlan-manager.company.com/api
```

**Features**:
- ✅ Scans all clusters automatically
- ✅ Allocates VLAN segments via API
- ✅ Adds vlanId and Networks to YAML files
- ✅ Respects `AutomaticAllocation: false` setting
- ✅ Production-ready error handling
- ✅ Optimized logging for large environments
- ✅ Dry-run mode for testing
- ✅ Automatic backup/restore on failures
- ✅ Skips already-configured files

---

## Directory Structure

The scripts expect this structure:
```
sites/
├── site1/
│   └── mce-tenant-clusters/
│       ├── mce-prod/
│       │   ├── ocp4-mce-site1/
│       │   │   └── ocp4-roi-3.yaml
│       │   └── mce-test-1/
│       │       └── ocp4-roi.yaml
│       └── mce-prep/
│           └── mce-test-2/
│               └── ocp4-roi-2.yaml
└── datacenter1/
    └── mce-tenant-clusters/
        └── mce-prod/
            └── mce-test-3/
                └── ocp4-mce-roi.yaml
```

**Naming rules**:
- Cluster YAML files: Must start with `ocp4-` and end with `.yaml`
- MCE directories: Can be named anything (e.g., `mce-test-1`, `ocp4-mce-site1`)
- MCE name is extracted from the **parent directory** of the YAML file
- Site name is extracted **4 levels up** from the YAML file

---

## How MCE and Site Extraction Works

For a file at: `sites/site1/mce-tenant-clusters/mce-prod/ocp4-mce-site1/ocp4-roi-3.yaml`

- **MCE name**: `ocp4-mce-site1` (parent directory)
- **Site name**: `site1` (4 levels up)
- **Cluster name**: `ocp4-roi-3` (filename without .yaml)

---

## What Gets Added to YAML Files

### Simple Script (`cluster_network_configurator.py`)
Adds only the Networks section:

```yaml
Networks:
- number: 1
  from:
    segment: <mce-segment>
  destention:
    segment: <cluster-segment>
  ports:
  - port: 80
    type: TCP
  - port: 8080
    type: UDP
- number: 2
  from:
    segment: <cluster-segment>
  destention:
    segment: <mce-segment>
  ports:
  - port: 80
    type: TCP
  - port: 8080
    type: UDP
```

### Production Script (`cluster_network_auto_configurator_final.py`)
Adds both vlanId and Networks:

```yaml
vlanId: 693
Networks:
- number: 1
  from:
    segment: <mce-segment>
  destention:
    segment: <cluster-segment>
  ports:
  - port: 80
    type: TCP
  - port: 8080
    type: UDP
- number: 2
  from:
    segment: <cluster-segment>
  destention:
    segment: <mce-segment>
  ports:
  - port: 80
    type: TCP
  - port: 8080
    type: UDP
```

---

## Skip Logic - Already Configured Files

Both scripts will **skip** files that already have:
- `Networks:` section
- `vlanId:` field

**Simple script**: Shows warning and exits with error
```
Warning: Networks section already exists in <file>
Skipping to avoid duplicates.
```

**Production script**: Logs warning and continues with other clusters
```
WARNING - Skipping ocp4-mce-roi.yaml: Already has Networks or vlanId configured
```

---

## AutomaticAllocation Control

Add this to any cluster YAML to prevent automatic configuration:

```yaml
clusterName: ocp4-roi-3
platform: agent
AutomaticAllocation: false  # ← This cluster will be skipped
...
```

The production script will skip these clusters and log:
```
⏭️ Skipping ocp4-roi-3: AutomaticAllocation is set to false
```

---

## API Integration

Both scripts integrate with the VLAN Manager API (from `~/Documents/scripts/segments_2`):

**Endpoints used**:
- `GET /health` - Check API availability
- `POST /allocate-vlan` - Allocate segment for cluster
- `GET /segments/search?q=<mce-name>` - Get MCE segment

**Graceful fallback**: When API is unavailable, scripts use deterministic mock allocations:
```
⚠️ VLAN Manager API unavailable at http://localhost:8000/api
Will use mock allocations for testing purposes
```

---

## Command-Line Options (Production Script Only)

```bash
--dry-run              # Test without making changes
--log-level LEVEL      # DEBUG, INFO, WARNING, ERROR
--api-url URL          # Custom VLAN manager API URL
```

---

## Exit Codes

**Simple script**:
- `0` - Success
- `1` - Failure (file not found, already configured, etc.)

**Production script**:
- `0` - All clusters processed successfully
- `1` - One or more clusters had errors
- `130` - User interrupted (Ctrl+C)

---

## Dependencies

```bash
pip install pyyaml requests
```

Or use the virtual environment from segments_2:
```bash
source ~/Documents/scripts/segments_2/.venv/bin/activate
```

---

## Examples

### Process single cluster manually
```bash
python cluster_network_configurator.py ocp4-roi-3 10.8.150.0/24
```

### Test production script without changes
```bash
python cluster_network_auto_configurator_final.py --dry-run
```

### Process all clusters in production
```bash
python cluster_network_auto_configurator_final.py
```

### Quiet mode (errors only)
```bash
python cluster_network_auto_configurator_final.py --log-level ERROR
```

### Verbose mode (debug everything)
```bash
python cluster_network_auto_configurator_final.py --log-level DEBUG
```

---

## Troubleshooting

**Problem**: "Networks section already exists"
**Solution**: Remove existing Networks/vlanId from YAML or use a fresh cluster

**Problem**: "Could not extract MCE name"
**Solution**: Verify directory structure matches: `sites/*/mce-tenant-clusters/*/MCE_NAME/ocp4-*.yaml`

**Problem**: "VLAN Manager API unavailable"
**Solution**: Normal - scripts will use mock allocations. Start the VLAN manager API for real allocations.

**Problem**: Cluster not found by production script
**Solution**: Ensure cluster YAML filename starts with `ocp4-` and is in correct directory structure
