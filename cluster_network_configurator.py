#!/usr/bin/env python3
"""
Cluster Network Configurator

This script processes cluster YAML files and adds network configuration.
It extracts the MCE name from cluster paths, makes API calls to VLAN manager,
and appends network configuration to cluster YAML files.

Usage:
    python cluster_network_configurator.py <cluster_name> <cluster_segment>
    
Example:
    python cluster_network_configurator.py ocp4-roi 10.1.100.0/24
"""

import os
import sys
import yaml
import requests
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import urljoin


class ClusterNetworkConfigurator:
    def __init__(self, vlan_manager_base_url: Optional[str] = None):
        """Initialize the configurator with VLAN manager URL"""
        # You can set this URL based on the VLAN manager from segments_2
        # Since the API is not up, we'll use a placeholder
        self.vlan_manager_url = vlan_manager_base_url or "http://localhost:8000/api"
        self.sites_dir = Path("sites")
        
    def extract_mce_name_from_path(self, cluster_name: str) -> Optional[str]:
        """
        Extract MCE name from cluster path structure.
        The MCE name is simply the parent directory of the cluster YAML file.

        Structure: sites/*/mce-tenant-clusters/*/MCE_NAME/cluster.yaml

        Args:
            cluster_name: Name of the cluster (e.g., 'ocp4-roi')

        Returns:
            MCE name if found, None otherwise
        """
        cluster_file = f"{cluster_name}.yaml"

        # Search for the cluster file in the sites directory
        for root, dirs, files in os.walk(self.sites_dir):
            if cluster_file in files:
                # The parent directory is the MCE name
                return Path(root).name

        return None
    
    def get_cluster_file_path(self, cluster_name: str) -> Optional[Path]:
        """Find the full path to the cluster YAML file"""
        cluster_file = f"{cluster_name}.yaml"
        
        for root, dirs, files in os.walk(self.sites_dir):
            if cluster_file in files:
                return Path(root) / cluster_file
        
        return None
    
    def make_vlan_manager_api_call(self, endpoint: str, method: str = "GET", data: Optional[Dict] = None) -> Optional[Dict[Any, Any]]:
        """
        Make API call to VLAN manager.
        
        This is based on the routes found in segments_2/src/api/routes.py
        Common endpoints:
        - /segments - GET segments
        - /segments/search - Search segments
        - /allocate-vlan - POST to allocate VLAN
        - /sites - GET available sites
        - /vrfs - GET available VRFs
        
        Args:
            endpoint: API endpoint (e.g., '/segments')
            method: HTTP method ('GET', 'POST', etc.)
            data: Data to send with POST requests
            
        Returns:
            Response JSON if successful, None if failed
        """
        url = urljoin(self.vlan_manager_url, endpoint.lstrip('/'))
        
        try:
            if method.upper() == "GET":
                response = requests.get(url, timeout=10)
            elif method.upper() == "POST":
                headers = {"Content-Type": "application/json"}
                response = requests.post(url, json=data, headers=headers, timeout=10)
            else:
                print(f"Unsupported HTTP method: {method}")
                return None
                
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            print(f"API call failed: {e}")
            print(f"Note: VLAN manager API might not be running at {url}")
            return None
    
    def get_mce_segment(self, mce_name: str) -> Optional[str]:
        """
        Get the segment assigned to the MCE from VLAN manager.
        
        Args:
            mce_name: MCE name (e.g., 'mce-test-1')
            
        Returns:
            MCE segment if found, placeholder if API is down
        """
        # Try to search for segments assigned to this MCE
        search_result = self.make_vlan_manager_api_call(
            f"/segments/search?q={mce_name}"
        )
        
        if search_result and search_result.get('segments'):
            # Find segment allocated to this MCE
            for segment in search_result['segments']:
                if segment.get('cluster_name') == mce_name:
                    return segment.get('segment')
        
        # If API is down or no segment found, return a placeholder
        print(f"Warning: Could not retrieve MCE segment for {mce_name} from VLAN manager")
        print("Using placeholder segment. Update manually when VLAN manager is available.")
        return "10.0.50.0/24"  # Placeholder MCE segment
    
    def create_network_config(self, mce_segment: str, cluster_segment: str) -> Dict[str, Any]:
        """
        Create the network configuration to append to YAML.
        
        Args:
            mce_segment: MCE segment (e.g., '10.0.50.0/24')
            cluster_segment: Cluster segment (e.g., '10.1.100.0/24')
            
        Returns:
            Network configuration dictionary
        """
        return {
            "Networks": [
                {
                    "number": 1,
                    "from": {
                        "segment": mce_segment
                    },
                    "destention": {  # Note: keeping "destention" as in user's request
                        "segment": cluster_segment
                    },
                    "ports": [
                        {
                            "port": 80,
                            "type": "TCP"
                        },
                        {
                            "port": 8080,
                            "type": "UDP"
                        }
                    ]
                },
                {
                    "number": 2,
                    "from": {
                        "segment": cluster_segment
                    },
                    "destention": {  # Note: keeping "destention" as in user's request
                        "segment": mce_segment
                    },
                    "ports": [
                        {
                            "port": 80,
                            "type": "TCP"
                        },
                        {
                            "port": 8080,
                            "type": "UDP"
                        }
                    ]
                }
            ]
        }
    
    def append_network_config_to_yaml(self, cluster_name: str, cluster_segment: str) -> bool:
        """
        Main function to process a cluster and append network configuration.
        
        Args:
            cluster_name: Name of the cluster (e.g., 'ocp4-roi')
            cluster_segment: Cluster segment (e.g., '10.1.100.0/24')
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Step 1: Extract MCE name from cluster path
            mce_name = self.extract_mce_name_from_path(cluster_name)
            if not mce_name:
                print(f"Error: Could not extract MCE name for cluster '{cluster_name}'")
                return False
            
            print(f"Extracted MCE name: {mce_name}")
            
            # Step 2: Get MCE segment from VLAN manager API
            mce_segment = self.get_mce_segment(mce_name)
            print(f"MCE segment: {mce_segment}")
            
            # Step 3: Find cluster YAML file
            cluster_file_path = self.get_cluster_file_path(cluster_name)
            if not cluster_file_path:
                print(f"Error: Could not find YAML file for cluster '{cluster_name}'")
                return False
            
            print(f"Found cluster file: {cluster_file_path}")
            
            # Step 4: Check if Networks already exists and handle appropriately
            with open(cluster_file_path, 'r') as file:
                existing_content = file.read()

            # Parse existing YAML to check current configuration
            try:
                existing_yaml = yaml.safe_load(existing_content)
                existing_networks = existing_yaml.get('Networks', []) if existing_yaml else []

                # Check if configuration already matches
                if existing_networks:
                    # Check if the segments match (bidirectional check)
                    matches_config = False
                    for net in existing_networks:
                        from_seg = net.get('from', {}).get('segment', '')
                        dest_seg = net.get('destention', {}).get('segment', '')
                        if ((from_seg == mce_segment and dest_seg == cluster_segment) or
                            (from_seg == cluster_segment and dest_seg == mce_segment)):
                            matches_config = True
                            break

                    if matches_config:
                        print(f"✅ Configuration already exists with same segments in {cluster_file_path}")
                        print(f"   MCE segment: {mce_segment}, Cluster segment: {cluster_segment}")
                        print("No changes needed.")
                        return True
                    else:
                        print(f"⚠️  Different network configuration exists in {cluster_file_path}")
                        print("Appending new configuration to support multiple networks.")
            except:
                # If YAML parsing fails, proceed with simple check
                pass

            # Step 5: Create network configuration YAML string
            network_config = self.create_network_config(mce_segment, cluster_segment)
            network_yaml = yaml.dump(network_config, default_flow_style=False, indent=2, sort_keys=False)

            # Step 6: Append network configuration to the end of the file
            updated_content = existing_content.rstrip() + '\n' + network_yaml

            # Step 7: Write back to file
            with open(cluster_file_path, 'w') as file:
                file.write(updated_content)
            
            print(f"Successfully added network configuration to {cluster_file_path}")
            print(f"Network 1: {mce_segment} -> {cluster_segment}")
            print(f"Network 2: {cluster_segment} -> {mce_segment}")
            
            return True
            
        except Exception as e:
            print(f"Error processing cluster '{cluster_name}': {e}")
            return False


def main():
    """Main entry point"""
    if len(sys.argv) != 3:
        print("Usage: python cluster_network_configurator.py <cluster_name> <cluster_segment>")
        print("Example: python cluster_network_configurator.py ocp4-roi 10.1.100.0/24")
        sys.exit(1)
    
    cluster_name = sys.argv[1]
    cluster_segment = sys.argv[2]
    
    # Initialize configurator
    configurator = ClusterNetworkConfigurator()
    
    # Process the cluster
    success = configurator.append_network_config_to_yaml(cluster_name, cluster_segment)
    
    if success:
        print("\n✅ Configuration completed successfully!")
    else:
        print("\n❌ Configuration failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()