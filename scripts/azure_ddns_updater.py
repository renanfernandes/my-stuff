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
"""

import sys
import os
import requests
from datetime import datetime
from azure.identity import DefaultAzureCredential
from azure.mgmt.dns import DnsManagementClient
from azure.mgmt.dns.models import RecordSet, ARecord
from azure.core.exceptions import ResourceNotFoundError

# Retrieve configuration from environment variables.
SUBSCRIPTION_ID = os.getenv('SUBSCRIPTION_ID')
RESOURCE_GROUP = os.getenv('RESOURCE_GROUP')
DNS_ZONE = os.getenv('DNS_ZONE')
RECORD_NAME = os.getenv('RECORD_NAME')
TTL = 300  # Time-to-live in seconds

# Validate that all required environment variables are set.
required_vars = {
    'SUBSCRIPTION_ID': SUBSCRIPTION_ID,
    'RESOURCE_GROUP': RESOURCE_GROUP,
    'DNS_ZONE': DNS_ZONE,
    'RECORD_NAME': RECORD_NAME,
}

for var_name, value in required_vars.items():
    if value is None:
        print(f"Error: The environment variable {var_name} is not set.")
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
        credential = DefaultAzureCredential()
    except Exception as e:
        log_message(f"Error creating default credentials: {e}")
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