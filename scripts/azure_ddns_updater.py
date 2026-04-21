#!/usr/bin/env python3
"""
azure_ddns_updater.py: A dynamic DNS updater for Azure DNS.

This script retrieves your current public IP address and updates
the specified Azure DNS A record if it has changed.

Changelog:
- 2025-02-03:
    - Initial version created.
    - Fixed property name from `arecords` to `a_records` for compatibility with the current Azure SDK.
    - Added configuration via environment variables for sensitive details (SUBSCRIPTION_ID, RESOURCE_GROUP, DNS_ZONE, RECORD_NAME).
    - Implemented validation for required environment variables.
    - Enhanced error handling and logging.
    - Added log_message() function to include timestamps in log messages.
- 2026-04-21:
    - Replaced environment variables with YAML config file (azure_ddns_updater_config.yaml).
    - Added Service Principal authentication via config (azure_tenant_id, azure_client_id, azure_client_secret).
"""

import sys
import os
import yaml
import requests
from datetime import datetime
from azure.identity import ClientSecretCredential
from azure.mgmt.dns import DnsManagementClient
from azure.mgmt.dns.models import RecordSet, ARecord
from azure.core.exceptions import ResourceNotFoundError

# Load configuration from YAML file.
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "azure_ddns_updater_config.yaml")

try:
    with open(CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)
except FileNotFoundError:
    print(f"Error: Config file not found at {CONFIG_PATH}")
    sys.exit(1)
except yaml.YAMLError as e:
    print(f"Error: Failed to parse config file: {e}")
    sys.exit(1)

SUBSCRIPTION_ID = config.get('subscription_id')
RESOURCE_GROUP = config.get('resource_group')
DNS_ZONE = config.get('dns_zone')
RECORD_NAME = config.get('record_name')
TTL = config.get('ttl', 300)
AZURE_TENANT_ID = config.get('azure_tenant_id')
AZURE_CLIENT_ID = config.get('azure_client_id')
AZURE_CLIENT_SECRET = config.get('azure_client_secret')

# Validate that all required config values are set.
required_vars = {
    'subscription_id': SUBSCRIPTION_ID,
    'resource_group': RESOURCE_GROUP,
    'dns_zone': DNS_ZONE,
    'record_name': RECORD_NAME,
    'azure_tenant_id': AZURE_TENANT_ID,
    'azure_client_id': AZURE_CLIENT_ID,
    'azure_client_secret': AZURE_CLIENT_SECRET,
}

for var_name, value in required_vars.items():
    if not value:
        print(f"Error: '{var_name}' is missing or empty in {CONFIG_PATH}")
        sys.exit(1)

def log_message(message):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}")

def get_public_ip():
    """Retrieve the current public IP address."""
    try:
        response = requests.get("https://api.ipify.org")
        response.raise_for_status()
        ip = response.text.strip()
        if not ip:
            raise ValueError("Empty IP address received.")
        return ip
    except Exception as e:
        log_message(f"Error retrieving public IP: {e}")
        sys.exit(1)

def main():
    # Step 1. Retrieve the current public IP.
    current_ip = get_public_ip()
    log_message(f"Current public IP: {current_ip}")

    # Step 2. Create Azure credentials and a DNS management client.
    try:
        credential = ClientSecretCredential(
            tenant_id=AZURE_TENANT_ID,
            client_id=AZURE_CLIENT_ID,
            client_secret=AZURE_CLIENT_SECRET,
        )
    except Exception as e:
        log_message(f"Error creating credentials: {e}")
        sys.exit(1)

    dns_client = DnsManagementClient(credential, SUBSCRIPTION_ID)

    # Step 3. Attempt to retrieve the existing A record set.
    try:
        record_set = dns_client.record_sets.get(
            RESOURCE_GROUP,
            DNS_ZONE,
            RECORD_NAME,
            "A"
        )
        # Extract existing IP addresses from the record set.
        existing_ips = [record.ipv4_address for record in record_set.a_records] if record_set.a_records else []
        if current_ip in existing_ips:
            log_message("IP addresses match. No update needed.")
            sys.exit(0)
        else:
            log_message(f"Existing DNS A record IPs: {existing_ips}")
            log_message("IP addresses differ. Updating record.")
    except ResourceNotFoundError:
        log_message("No existing A record found. A new record set will be created.")

    # Step 4. Update (or create) the DNS record with the current public IP.
    new_a_record = ARecord(ipv4_address=current_ip)
    record_set_params = RecordSet(
        ttl=TTL,
        a_records=[new_a_record]
    )

    try:
        dns_client.record_sets.create_or_update(
            RESOURCE_GROUP,
            DNS_ZONE,
            RECORD_NAME,
            "A",
            record_set_params
        )
        log_message(f"DNS record updated successfully to IP: {current_ip}")
    except Exception as e:
        log_message(f"Error updating DNS record: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()