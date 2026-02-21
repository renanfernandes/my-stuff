# Scripts
![commit](https://img.shields.io/github/last-commit/renanfernandes/my-stuff)

A collection of Python automation scripts for various home services and monitoring tasks.

## Overview

This directory contains utility scripts and configurations for automating common tasks including:
- Dynamic DNS management for Azure
- IP address change monitoring and notifications
- NZBGet download transfers
- Torrent completion notifications
- Home Assistant ESPHome configurations

## Contents

### Python Scripts
Standalone automation scripts for various home services and monitoring tasks.

### Home Assistant
Home Assistant integrations and ESPHome device configurations.

## Scripts

### 🔄 `azure_ddns_updater.py`
**Dynamic DNS updater for Azure DNS**

Updates Azure DNS A records with your current public IP address when it changes.

**Features:**
- Retrieves current public IP address
- Compares with existing Azure DNS record
- Updates record only if IP has changed
- Timestamped logging
- Environment variable configuration for security

**Requirements:**
- Azure SDK: `azure-identity`, `azure-mgmt-dns`
- Environment variables:
  - `SUBSCRIPTION_ID`: Azure subscription ID
  - `RESOURCE_GROUP`: Azure resource group name
  - `DNS_ZONE`: Azure DNS zone name
  - `RECORD_NAME`: DNS record name to update

**Usage:**
```bash
export SUBSCRIPTION_ID="your-subscription-id"
export RESOURCE_GROUP="your-resource-group"
export DNS_ZONE="your-zone.com"
export RECORD_NAME="subdomain"
python3 azure_ddns_updater.py
```

---

### 📡 `ip_changer_notifier.py`
**External IP change monitoring with Pushover notifications**

Monitors your external IP address and sends notifications when changes occur.

**Features:**
- Checks external IP regularly
- Stores last known IP locally
- Sends notifications via Pushover
- Timestamped logging

**Requirements:**
- `requests` package
- Pushover account with API token
- Environment variables:
  - `PUSHOVER_USER_KEY`: Your Pushover user key
  - `PUSHOVER_API_TOKEN`: Your Pushover API token

**Usage:**
```bash
export PUSHOVER_USER_KEY="your-user-key"
export PUSHOVER_API_TOKEN="your-api-token"
python3 ip_changer_notifier.py
```

**Configuration:**
Can be scheduled via cron job to run periodically:
```bash
*/15 * * * * cd /path/to/scripts && python3 ip_changer_notifier.py
```

---

### 📦 `nzbgget_sftp_transfer.py` & `SFTPTransfer.py`
**NZBGet post-processing SFTP transfer script**

Automatically transfers completed NZBGet downloads to a remote server via SFTP.

**Features:**
- NZBGet post-processing integration
- Category-based routing (Movies, Series, General)
- Optional automatic cleanup after transfer
- Pushover notifications support
- Configurable destination paths

**Requirements:**
- `paramiko` package (for SFTP)
- `pushover` package (optional, for notifications)
- NZBGet post-processing script configuration
- Remote SFTP server access

**Configuration:**
Set these environment variables or edit the script:
- `WINDOWS_SERVER_HOST`: SFTP server hostname
- `WINDOWS_SERVER_PORT`: SFTP server port (default: 22)
- `WINDOWS_SERVER_USERNAME`: SFTP username
- `WINDOWS_SERVER_PASSWORD`: SFTP password
- `WINDOWS_DESTINATION_PATH`: Default destination path
- `MOVIES_DESTINATION_PATH`: Movies category destination
- `SERIES_DESTINATION_PATH`: Series/TV shows destination
- `AUTO_CLEANUP_LOCAL_FILES`: Delete local files after transfer (yes/no)
- `PUSHOVER_ENABLED`: Enable notifications (yes/no)

**NZBGet Setup:**
1. Copy the script to NZBGet's scripts directory
2. Configure in NZBGet settings as a post-processing script
3. Set required environment variables in NZBGet configuration

---

### 🎬 `transmission_checker.py`
**Torrent completion monitor with Pushover notifications**

Monitors Transmission torrent client for completed downloads and sends notifications.

**Features:**
- Checks completed torrents
- Tracks notifications to avoid duplicates
- Sends Pushover notifications for new completions
- Maintains notification history

**Requirements:**
- `transmission-rpc` package
- `pushover` package
- Transmission daemon running with credentials
- Pushover account configured

**Usage:**
```bash
python3 transmission_checker.py
```

**Configuration:**
Edit the script to configure Transmission credentials:
```python
c = Client(username='transmission', password='transmission')
```

Can be scheduled via cron job:
```bash
*/5 * * * * python3 /path/to/transmission_checker.py
```

---

### 📋 `pushoverrc`
**Pushover configuration file template**

Template for Pushover API credentials. This file should be placed at `~/.pushoverrc`.

**Format:**
```
[Default]
api_token=your-api-token
user_key=your-user-key
```

---

## Home Assistant

### 🏠 `home-assistant/s31.yaml`
**ESPHome configuration for Sonoff S31 Power Outlet**

ESPHome configuration for integrating a Sonoff S31 smart power outlet with Home Assistant.

**Features:**
- GPIO relay control (GPIO12)
- Physical power button support (GPIO0)
- UART communication at 4800 baud (EVEN parity)
- WiFi connectivity
- Home Assistant API integration
- Web server interface
- OTA (Over-the-Air) updates

**Device:** Sonoff S31 Power Outlet (ESP8266 - ESP01_1M board)

**Setup:**
1. Flash ESPHome firmware to the S31 device
2. Configure WiFi credentials in `secrets.yaml`:
   ```yaml
   wifi_ssid: "your-network-name"
   wifi_password: "your-network-password"
   ```
3. Deploy to device via ESPHome
4. Add to Home Assistant via Home Assistant API

**Configuration Details:**
- Relay is set to `ALWAYS_ON` restore mode on startup
- Physical button provides manual power control
- Web server accessible on port 80
- Can receive updates over-the-air

---

## Common Dependencies

Install common dependencies using pip:
```bash
pip install requests blinkpy aiohttp paramiko pushover transmission-rpc azure-identity azure-mgmt-dns
```

Or install only what you need based on which scripts you're using.

## Environment Variables

For security, sensitive credentials should be set as environment variables rather than hardcoded:

```bash
# Pushover
export PUSHOVER_USER_KEY="..."
export PUSHOVER_API_TOKEN="..."

# Azure
export SUBSCRIPTION_ID="..."
export RESOURCE_GROUP="..."
export DNS_ZONE="..."
export RECORD_NAME="..."

# SFTP Transfer
export WINDOWS_SERVER_HOST="..."
export WINDOWS_SERVER_PORT="22"
export WINDOWS_SERVER_USERNAME="..."
export WINDOWS_SERVER_PASSWORD="..."
```

## Scheduling

Many of these scripts are designed to run periodically. Use cron jobs to schedule them:

```bash
# Edit crontab
crontab -e

# Example entries:
# Check IP changes every 15 minutes
*/15 * * * * cd /path/to/scripts && python3 ip_changer_notifier.py

# Check torrent completions every 5 minutes
*/5 * * * * python3 /path/to/scripts/transmission_checker.py

# Update Azure DNS hourly
0 * * * * python3 /path/to/scripts/azure_ddns_updater.py
```

## Notes

- Most scripts use timestamped logging for better tracking
- Credentials should always be stored in environment variables, not in the scripts
- Some scripts maintain local state files (e.g., `last_ip.txt`, `last_notification.txt`)
- Ensure appropriate file permissions and Python 3.6+ installed
