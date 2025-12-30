#!/usr/bin/env python3
"""
Cluster Network Auto Configurator - Production Version

Production-only version without mock data support.
Requires active VLAN Manager API connection.

Features:
- Queries API for actual vlanId and segments
- Compares with existing YAML configuration
- Updates/replaces if different (no duplicates)
- Idempotent: running multiple times = same result
- Segment caching: Fetches all segments once to avoid API spam
- Compatible with VLAN Manager API from ~/Documents/scripts/segments_2
- PRODUCTION ONLY: No mock data fallback - fails if API unavailable

Compatibility Notes:
- VLAN Manager API endpoints used:
  * GET /segments?allocated=true - Fetch all allocated segments
  * POST /allocate-vlan - Allocate new VLAN (requires: cluster_name, site, vrf)
  * GET /health - Health check
- Authentication: Currently bypassed (API should allow unauthenticated reads)
- Response format: /segments returns List directly, not wrapped in dict
"""

import os
import sys
import yaml
import requests
import logging
import argparse
import time
import copy
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass
from enum import Enum


# ============================================================================
# CONSTANTS
# ============================================================================

# API Configuration
DEFAULT_VRF = "Network1"
DEFAULT_API_URL = "http://0.0.0.0:8000/api"
DEFAULT_API_USERNAME = "admin"
DEFAULT_API_PASSWORD = "admin"
API_TIMEOUT = (5, 10)  # Connection timeout, read timeout

# Network Configuration
DEFAULT_DOMAIN = "default"
DEFAULT_PORTS = [
    {"type": "port", "number": 80, "protocol": "TCP"},
    {"type": "port", "number": 8080, "protocol": "TCP"}
]
DEFAULT_PORT_RANGES = [
    {"type": "range", "start": 30000, "end": 36000, "protocol": "TCP"}
]

# Path Navigation
SITE_LEVEL_UP = 4  # Levels from YAML file to site directory
MCE_TENANT_DIR_VARIANTS = ["mce-tenant-clusters", "mce-tenant-cluster"]
MCE_ENVIRONMENTS = ["mce-prod", "mce-prep"]
CLUSTER_FILE_PATTERN = "ocp4-*.yaml"

# Skip MCEs - Easy to remove after initialization
# Add MCE names here to skip them during processing (useful for prod clusters during initial setup)
# REMOVE THIS LIST AFTER INITIALIZATION to process all clusters
SKIP_MCES = [
    # "mce-site1-prod",  # Example: uncomment to skip
    # "ocp4-mce-site1",  # Example: uncomment to skip
]

# YAML Keys
KEY_VLAN_ID = "vlanId"
KEY_NETWORKS = "Networks"
KEY_FROM = "from"
KEY_DESTINATION = "destination"
KEY_DESTENTION = "destention"  # Legacy typo support
KEY_SEGMENT = "segment"
KEY_SYSTEM_NAME = "system-name"
KEY_CLUSTER_NAME = "cluster_name"


# ============================================================================
# DATA STRUCTURES AND ENUMS
# ============================================================================

class ProcessingStatus(Enum):
    """Enumeration for processing status"""
    SUCCESS = "success"
    UPDATED = "updated"
    SKIPPED = "skipped"
    ERROR = "error"
    API_UNAVAILABLE = "api_unavailable"


@dataclass
class APIContext:
    """API context to reduce parameter passing"""
    vlan_manager_url: str
    session: requests.Session
    api_available: bool
    segments_cache: Dict[str, str]
    logger: logging.Logger


@dataclass
class ClusterResult:
    """Data class for storing cluster processing results"""
    cluster_name: str
    status: ProcessingStatus
    vlan_id: Optional[str] = None
    cluster_segment: Optional[str] = None
    mce_segment: Optional[str] = None
    error_message: Optional[str] = None
    processing_time: float = 0.0


class APIException(Exception):
    """Custom exception for API-related errors"""
    pass


# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

def setup_logging() -> logging.Logger:
    """Setup optimized logging for production use"""
    logger = logging.getLogger(__name__)

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    return logger


# ============================================================================
# FILE SYSTEM OPERATIONS
# ============================================================================

def scan_all_clusters(sites_dir: Path, logger: logging.Logger) -> List[Tuple[str, Path]]:
    """Scan all sites and find all cluster YAML files"""
    if not sites_dir.exists():
        raise FileNotFoundError(f"Sites directory '{sites_dir}' not found")

    clusters = []

    try:
        for site_dir in sites_dir.iterdir():
            if not site_dir.is_dir():
                continue

            # Try both directory variants
            mce_tenant_dir = None
            for variant in MCE_TENANT_DIR_VARIANTS:
                potential_dir = site_dir / variant
                if potential_dir.exists():
                    mce_tenant_dir = potential_dir
                    break

            if not mce_tenant_dir:
                continue

            # Only check specific MCE environments
            for env_name in MCE_ENVIRONMENTS:
                env_dir = mce_tenant_dir / env_name
                if not env_dir.exists():
                    continue

                for mce_dir in env_dir.iterdir():
                    if not mce_dir.is_dir():
                        continue

                    # Skip MCEs in the skip list
                    mce_name = mce_dir.name
                    if mce_name in SKIP_MCES:
                        logger.info(f"⏭️  Skipping MCE: {mce_name} (in SKIP_MCES list)")
                        continue

                    for yaml_file in mce_dir.glob(CLUSTER_FILE_PATTERN):
                        cluster_name = yaml_file.stem
                        clusters.append((cluster_name, yaml_file))

    except Exception as e:
        logger.error(f"Error scanning directories: {e}")
        raise

    return clusters


def extract_mce_name_from_path(cluster_path: Path) -> Optional[str]:
    """Extract MCE name (parent directory)"""
    try:
        return cluster_path.parent.name
    except Exception:
        return None


def extract_site_from_path(cluster_path: Path) -> Optional[str]:
    """Extract site name (navigate up directory tree)"""
    try:
        current = cluster_path
        for _ in range(SITE_LEVEL_UP):
            current = current.parent
        return current.name
    except Exception:
        return None


def check_automatic_allocation_enabled(cluster_path: Path, logger: logging.Logger) -> bool:
    """Check if AutomaticAllocation is enabled"""
    try:
        with open(cluster_path, 'r', encoding='utf-8') as file:
            cluster_config = yaml.safe_load(file)

        if cluster_config is None:
            return True

        auto_allocation = cluster_config.get('AutomaticAllocation', True)
        return auto_allocation is not False

    except Exception as e:
        logger.warning(f"Error reading {cluster_path}: {e}")
        return True


# ============================================================================
# API COMMUNICATION
# ============================================================================

def check_api_availability(vlan_manager_url: str, session: requests.Session, logger: logging.Logger) -> bool:
    """Check if the VLAN manager API is available"""
    try:
        make_api_call("/health", vlan_manager_url, session, timeout=3)
        logger.info(f"✅ VLAN Manager API is available at {vlan_manager_url}")
        return True
    except Exception as e:
        logger.error(f"❌ VLAN Manager API unavailable at {vlan_manager_url}: {e}")
        logger.error("PRODUCTION MODE: Cannot proceed without API access")
        return False


def make_api_call(endpoint: str, vlan_manager_url: str, session: requests.Session,
                 method: str = "GET", data: Optional[Dict] = None, timeout: int = 10) -> Optional[Dict[Any, Any]]:
    """Make API call with error handling"""
    url = f"{vlan_manager_url.rstrip('/')}/{endpoint.lstrip('/')}"

    try:
        if method.upper() == "GET":
            response = session.get(url, timeout=timeout)
        elif method.upper() == "POST":
            headers = {"Content-Type": "application/json"}
            response = session.post(url, json=data, headers=headers, timeout=timeout)
        else:
            raise APIException(f"Unsupported HTTP method: {method}")

        response.raise_for_status()
        return response.json()

    except requests.exceptions.Timeout:
        raise APIException(f"API call timeout for {endpoint}")
    except requests.exceptions.ConnectionError:
        raise APIException(f"Connection error for {endpoint}")
    except requests.exceptions.HTTPError as e:
        raise APIException(f"HTTP {e.response.status_code} error for {endpoint}: {e}")
    except Exception as e:
        raise APIException(f"Unexpected error for {endpoint}: {e}")


# ============================================================================
# VLAN ALLOCATION
# ============================================================================

def allocate_vlan_segment(cluster_name: str, site: str, api_ctx: APIContext) -> Tuple[Optional[str], Optional[str]]:
    """Allocate VLAN segment via API (production-only)"""
    if not api_ctx.api_available:
        api_ctx.logger.error(f"Cannot allocate VLAN for {cluster_name}: API unavailable")
        return None, None

    try:
        allocation_request = {
            "cluster_name": cluster_name,
            "site": site,
            "vrf": DEFAULT_VRF
        }

        response = make_api_call("/allocate-vlan", api_ctx.vlan_manager_url, api_ctx.session, "POST", allocation_request)

        if response:
            segment = response.get('segment')
            vlan_id = response.get('vlan_id')

            if segment and vlan_id:
                return segment, str(vlan_id)
            else:
                api_ctx.logger.error(f"Invalid API response for {cluster_name}: missing segment or vlan_id")
                return None, None

        return None, None

    except APIException as e:
        api_ctx.logger.error(f"API error for {cluster_name}: {e}")
        return None, None
    except Exception as e:
        api_ctx.logger.error(f"Unexpected error allocating VLAN for {cluster_name}: {e}")
        return None, None


def fetch_all_segments(vlan_manager_url: str, session: requests.Session,
                      api_available: bool, logger: logging.Logger) -> Dict[str, str]:
    """
    Fetch all segments from VLAN manager once and cache them.
    Returns a dictionary mapping cluster_name -> segment.
    """
    if not api_available:
        logger.debug("API not available, returning empty segment cache")
        return {}

    try:
        # Get all allocated segments (only those with cluster_name assigned)
        search_result = make_api_call("/segments?allocated=true", vlan_manager_url, session)

        if search_result:
            segments_cache = {}
            # VLAN manager returns a list directly, not wrapped in 'segments' key
            segments_list = search_result if isinstance(search_result, list) else []

            for segment_info in segments_list:
                cluster_name = segment_info.get(KEY_CLUSTER_NAME)
                segment = segment_info.get(KEY_SEGMENT)
                if cluster_name and segment:
                    segments_cache[cluster_name] = segment

            logger.debug(f"Cached {len(segments_cache)} segments from VLAN manager")
            return segments_cache

    except APIException as e:
        logger.warning(f"Failed to fetch segments from API: {e}")
    except Exception as e:
        logger.warning(f"Unexpected error fetching segments: {e}")

    return {}


def get_mce_segment(mce_name: str, api_ctx: APIContext) -> Optional[str]:
    """Get MCE segment from cache (production-only)"""
    if mce_name in api_ctx.segments_cache:
        api_ctx.logger.debug(f"Found {mce_name} in segment cache: {api_ctx.segments_cache[mce_name]}")
        return api_ctx.segments_cache[mce_name]

    # If not in cache, it doesn't exist
    api_ctx.logger.error(f"MCE {mce_name} not found in segment cache - no allocation exists")
    return None


# ============================================================================
# YAML UPDATE/REPLACE LOGIC
# ============================================================================

def get_default_ports_config() -> List[Dict]:
    """Get combined ports configuration (single ports + ranges)"""
    return copy.deepcopy(DEFAULT_PORTS) + copy.deepcopy(DEFAULT_PORT_RANGES)


def _create_network_rule(number: int, from_segment: str, from_name: str,
                         to_segment: str, to_name: str) -> Dict:
    """Create a single network rule (DRY helper)"""
    return {
        "number": number,
        "domain": DEFAULT_DOMAIN,
        KEY_FROM: {
            KEY_SEGMENT: from_segment,
            KEY_SYSTEM_NAME: from_name
        },
        KEY_DESTINATION: {
            KEY_SEGMENT: to_segment,
            KEY_SYSTEM_NAME: to_name
        },
        "ports": get_default_ports_config()
    }


def create_network_config(mce_segment: str, cluster_segment: str, mce_name: str, cluster_name: str) -> List[Dict]:
    """Create bidirectional network configuration structure"""
    return [
        _create_network_rule(1, mce_segment, mce_name, cluster_segment, cluster_name),
        _create_network_rule(2, cluster_segment, cluster_name, mce_segment, mce_name)
    ]


def update_cluster_yaml_smart(cluster_path: Path, vlan_id: str, mce_segment: Optional[str],
                              cluster_segment: str, mce_name: str, cluster_name: str,
                              dry_run: bool, logger: logging.Logger) -> Tuple[bool, str]:
    """
    Smart update: Parse YAML, compare, and update/replace only if different.

    If mce_segment is None, only vlanId is inserted (Networks section is skipped).

    Returns:
        (success: bool, action: str) where action is "no_change", "updated", or "added"
    """
    try:
        # Read and parse existing YAML
        with open(cluster_path, 'r', encoding='utf-8') as file:
            yaml_content = yaml.safe_load(file) or {}

        # Get existing values
        existing_vlan_id = yaml_content.get(KEY_VLAN_ID)
        existing_networks = yaml_content.get(KEY_NETWORKS, [])

        # Create new network configuration (only if MCE segment is available)
        new_networks = None
        if mce_segment:
            new_networks = create_network_config(mce_segment, cluster_segment, mce_name, cluster_name)

        # Check if vlanId matches
        vlan_matches = str(existing_vlan_id) == str(vlan_id) if existing_vlan_id else False

        # Check if segments match (bidirectional) - support both old and new format
        segments_match = False
        if mce_segment and existing_networks:
            for net in existing_networks:
                # Support both "destination" and legacy "destention" (typo)
                from_seg = net.get(KEY_FROM, {}).get(KEY_SEGMENT, '')
                dest_seg = (net.get(KEY_DESTINATION, {}).get(KEY_SEGMENT, '') or
                           net.get(KEY_DESTENTION, {}).get(KEY_SEGMENT, ''))

                if ((from_seg == mce_segment and dest_seg == cluster_segment) or
                    (from_seg == cluster_segment and dest_seg == mce_segment)):
                    segments_match = True
                    break
        elif not mce_segment:
            # If no MCE segment, don't check Networks - only vlanId matters
            segments_match = True

        # Determine action
        if vlan_matches and segments_match:
            logger.info(f"✅ {cluster_path.name}: Already up-to-date (vlanId={vlan_id})")
            return True, "no_change"

        # Need to update
        action = "updated" if (existing_vlan_id or existing_networks) else "added"

        if dry_run:
            logger.info(f"DRY-RUN: Would {action} {cluster_path} with VLAN {vlan_id}")
            return True, action

        # Hybrid approach: Read original file, remove vlanId/Networks, then append new sections
        with open(cluster_path, 'r', encoding='utf-8') as file:
            original_lines = file.readlines()

        # Remove existing vlanId and Networks sections
        new_content = []
        skip_networks = False
        for line in original_lines:
            # Skip existing vlanId line
            if line.strip().startswith('vlanId:'):
                continue
            # Skip existing Networks section
            if line.strip().startswith('Networks:'):
                skip_networks = True
                continue
            if skip_networks:
                # Check if we're still in the Networks section (indented or dash lines)
                if line.startswith((' ', '-')) or line.strip() == '':
                    continue
                else:
                    skip_networks = False
            new_content.append(line)

        # Ensure last line ends with newline
        if new_content and not new_content[-1].endswith('\n'):
            new_content[-1] += '\n'

        # Generate new sections using yaml.dump() for clean formatting
        new_sections = {KEY_VLAN_ID: int(vlan_id)}
        if mce_segment and new_networks:
            new_sections[KEY_NETWORKS] = new_networks
        elif not mce_segment:
            logger.warning(f"⚠️  MCE segment not found - will insert vlanId only")

        # Dump new sections to YAML
        new_yaml = yaml.dump(new_sections, default_flow_style=False, indent=2, sort_keys=False)

        # Write back: original content + new sections
        with open(cluster_path, 'w', encoding='utf-8') as file:
            file.writelines(new_content)
            file.write(new_yaml)

        logger.info(f"{'🔄' if action == 'updated' else '➕'} {cluster_path.name}: {action.capitalize()} (vlanId={vlan_id})")
        return True, action

    except Exception as e:
        logger.error(f"Failed to update {cluster_path}: {e}")
        return False, "error"


# ============================================================================
# CLUSTER PROCESSING
# ============================================================================

def process_single_cluster(cluster_name: str, cluster_path: Path, api_ctx: APIContext, dry_run: bool) -> ClusterResult:
    """Process a single cluster"""
    start_time = time.time()

    try:
        # Check AutomaticAllocation
        if not check_automatic_allocation_enabled(cluster_path, api_ctx.logger):
            return ClusterResult(
                cluster_name=cluster_name,
                status=ProcessingStatus.SKIPPED,
                error_message="AutomaticAllocation set to false",
                processing_time=time.time() - start_time
            )

        # Extract MCE and site
        mce_name = extract_mce_name_from_path(cluster_path)
        site = extract_site_from_path(cluster_path)

        if not mce_name:
            return ClusterResult(
                cluster_name=cluster_name,
                status=ProcessingStatus.ERROR,
                error_message="Could not extract MCE name",
                processing_time=time.time() - start_time
            )

        if not site:
            return ClusterResult(
                cluster_name=cluster_name,
                status=ProcessingStatus.ERROR,
                error_message="Could not extract site",
                processing_time=time.time() - start_time
            )

        api_ctx.logger.info(f"Processing cluster: {cluster_name} (MCE: {mce_name}, Site: {site})")

        # Allocate VLAN segment for cluster
        cluster_segment, vlan_id = allocate_vlan_segment(cluster_name, site, api_ctx)

        if not cluster_segment or not vlan_id:
            return ClusterResult(
                cluster_name=cluster_name,
                status=ProcessingStatus.ERROR,
                error_message="VLAN allocation failed",
                processing_time=time.time() - start_time
            )

        # Update cache with newly allocated cluster segment
        api_ctx.segments_cache[cluster_name] = cluster_segment

        # Get MCE segment from cache (optional - edge case)
        mce_segment = get_mce_segment(mce_name, api_ctx)

        if not mce_segment:
            api_ctx.logger.warning(f"⚠️  MCE segment not found for {mce_name} - will insert vlanId only")

        # Smart update YAML (mce_segment can be None - will insert vlanId only)
        success, action = update_cluster_yaml_smart(
            cluster_path, vlan_id, mce_segment, cluster_segment, mce_name, cluster_name, dry_run, api_ctx.logger
        )

        if success:
            if action == "no_change":
                status = ProcessingStatus.SUCCESS
            elif action == "updated":
                status = ProcessingStatus.UPDATED
            else:
                status = ProcessingStatus.API_UNAVAILABLE if not api_ctx.api_available else ProcessingStatus.SUCCESS

            return ClusterResult(
                cluster_name=cluster_name,
                status=status,
                vlan_id=vlan_id,
                cluster_segment=cluster_segment,
                mce_segment=mce_segment,
                processing_time=time.time() - start_time
            )
        else:
            return ClusterResult(
                cluster_name=cluster_name,
                status=ProcessingStatus.ERROR,
                error_message="Failed to update YAML",
                processing_time=time.time() - start_time
            )

    except Exception as e:
        return ClusterResult(
            cluster_name=cluster_name,
            status=ProcessingStatus.ERROR,
            error_message=str(e),
            processing_time=time.time() - start_time
        )


def process_all_clusters(sites_dir: Path, vlan_manager_url: str, dry_run: bool,
                        logger: logging.Logger) -> Dict[str, Any]:
    """Process all clusters"""
    logger.info("🔍 Scanning for clusters in all sites...")

    try:
        clusters = scan_all_clusters(sites_dir, logger)
    except Exception as e:
        logger.error(f"Failed to scan clusters: {e}")
        return {"error": str(e)}

    if not clusters:
        logger.warning("No clusters found")
        return {"processed": 0, "updated": 0, "skipped": 0, "errors": 0, "api_unavailable": 0}

    logger.info(f"Found {len(clusters)} cluster(s)")

    # Setup session with Basic Auth (like curl -u admin:admin)
    session = requests.Session()
    session.auth = (DEFAULT_API_USERNAME, DEFAULT_API_PASSWORD)
    session.timeout = API_TIMEOUT

    # Check API availability
    api_available = check_api_availability(vlan_manager_url, session, logger)

    # Fetch all segments once to avoid spamming the API
    logger.info("📦 Fetching all segments from VLAN manager...")
    segments_cache = fetch_all_segments(vlan_manager_url, session, api_available, logger)
    if segments_cache:
        logger.info(f"✅ Cached {len(segments_cache)} segments")
    elif api_available:
        logger.warning("⚠️  No segments found in VLAN manager")
    else:
        logger.error("❌ Cannot fetch segments - API unavailable")

    # Create API context to reduce parameter passing
    api_ctx = APIContext(
        vlan_manager_url=vlan_manager_url,
        session=session,
        api_available=api_available,
        segments_cache=segments_cache,
        logger=logger
    )

    results = []
    stats = {"processed": 0, "updated": 0, "skipped": 0, "errors": 0, "api_unavailable": 0}

    for i, (cluster_name, cluster_path) in enumerate(clusters, 1):
        if i % 10 == 0 or i == len(clusters):
            logger.info(f"Processing cluster {i}/{len(clusters)}")

        result = process_single_cluster(cluster_name, cluster_path, api_ctx, dry_run)
        results.append(result)

        # Update statistics
        if result.status == ProcessingStatus.SUCCESS:
            stats["processed"] += 1
        elif result.status == ProcessingStatus.UPDATED:
            stats["updated"] += 1
        elif result.status == ProcessingStatus.SKIPPED:
            stats["skipped"] += 1
        elif result.status == ProcessingStatus.API_UNAVAILABLE:
            stats["api_unavailable"] += 1
        else:
            stats["errors"] += 1

    return {"results": results, "stats": stats}


# ============================================================================
# REPORTING
# ============================================================================

def print_processing_summary(processing_results: Dict[str, Any], logger: logging.Logger) -> None:
    """Print processing summary"""
    if "error" in processing_results:
        logger.error(f"Processing failed: {processing_results['error']}")
        return

    stats = processing_results["stats"]
    results = processing_results["results"]

    logger.info("="*60)
    logger.info("📊 PROCESSING SUMMARY")
    logger.info("="*60)
    logger.info(f"✅ Already up-to-date: {stats['processed']}")
    logger.info(f"🔄 Updated: {stats['updated']}")
    logger.info(f"⏭️  Skipped: {stats['skipped']}")
    logger.info(f"❌ Errors: {stats['errors']}")
    if stats['api_unavailable'] > 0:
        logger.warning(f"⚠️  Failed due to API unavailable: {stats['api_unavailable']}")
    logger.info(f"📋 Total clusters: {len(results)}")

    # Show errors only
    error_results = [r for r in results if r.status == ProcessingStatus.ERROR]
    if error_results:
        logger.info("\n❌ ERROR DETAILS:")
        for result in error_results:
            logger.info(f"  {result.cluster_name}: {result.error_message}")

    # Performance summary
    total_time = sum(r.processing_time for r in results)
    avg_time = total_time / len(results) if results else 0
    logger.info(f"\n⏱️  Total: {total_time:.2f}s, Avg: {avg_time:.3f}s/cluster")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def parse_command_line_arguments() -> argparse.Namespace:
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Cluster Network Auto Configurator - Production Version"
    )
    parser.add_argument("--dry-run", action="store_true", help="Test without making changes")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                       default="INFO", help="Set logging level")
    parser.add_argument("--api-url", default=DEFAULT_API_URL,
                       help="VLAN Manager API base URL")

    return parser.parse_args()


def main():
    """Main entry point"""
    args = parse_command_line_arguments()

    logger = setup_logging()
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    print("🚀 Cluster Network Auto Configurator - Production Version")
    print("=" * 50)
    print("⚠️  Production Mode: Requires VLAN Manager API")
    print("=" * 50)

    if args.dry_run:
        logger.info("Running in DRY-RUN mode")

    try:
        sites_dir = Path("sites")
        results = process_all_clusters(sites_dir, args.api_url, args.dry_run, logger)
        print_processing_summary(results, logger)

        if "error" in results or results["stats"]["errors"] > 0:
            sys.exit(1)
        else:
            logger.info("🎉 Processing completed successfully!")
            sys.exit(0)

    except KeyboardInterrupt:
        print("\n\n⏹️  Interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()