# Scripts
![commit](https://img.shields.io/github/last-commit/renanfernandes/my-stuff)

A collection of Python automation scripts for various home services and monitoring tasks.

## Quick Reference

| Script | Purpose | Setup Time |
|--------|---------|-----------|
| `azure_ddns_updater.py` | Dynamic DNS for Azure | ⏱️ 5 min |
| `ip_changer_notifier.py` | Monitor IP changes | ⏱️ 3 min |
| `nzbgget_sftp_transfer.py` | Auto-transfer downloads | ⏱️ 10 min |
| `transmission_checker.py` | Torrent completion alerts | ⏱️ 5 min |
| `s31.yaml` | Sonoff S31 smart outlet | ⏱️ 15 min |

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Network & DNS](#network--dns)
  - [azure_ddns_updater.py](#-azure_ddns_updaterpy)
  - [ip_changer_notifier.py](#-ip_changer_notifierpy)
- [Download & Transfer](#download--transfer)
  - [nzbgget_sftp_transfer.py & SFTPTransfer.py](#-nzbgget_sftp_transferpy--sftptransferpy)
  - [transmission_checker.py](#-transmission_checkerpy)
- [Home Automation](#home-automation)
  - [living-room-mirror.yaml](#-home-assistantliving-room-mirroryaml)
- [Configuration](#configuration)
- [Scheduling](#scheduling)
- [Troubleshooting](#troubleshooting)
- [Notes](#notes)

## Prerequisites

Before running any scripts:
- **Python 3.6+** installed and in your PATH
- **pip** package manager available
- Basic knowledge of environment variables
- For download scripts: appropriate service accounts (Azure, etc.)
- For scheduling: cron access (macOS/Linux) or Task Scheduler (Windows)

## Quick Start

1. **Clone and navigate to scripts:**
   ```bash
   git clone https://github.com/renanfernandes/my-stuff.git
   cd my-stuff/scripts
   ```

2. **Install all dependencies:**
   ```bash
   pip install requests paramiko pushover transmission-rpc azure-identity azure-mgmt-dns
   ```
   Or install selectively based on which scripts you need.

3. **Set environment variables:**
   ```bash
   export PUSHOVER_USER_KEY="your-key"
   export PUSHOVER_API_TOKEN="your-token"
   ```

4. **Run a test script:**
   ```bash
   python3 ip_changer_notifier.py
   ```

## Network & DNS

### 🔄 `azure_ddns_updater.py`
**Dynamic DNS updater for Azure DNS**

Updates Azure DNS A records with your current public IP address when it changes.

**Features:**
- Retrieves current public IP address
- Compares with existing Azure DNS record
- Updates record only if IP has changed
- Timestamped logging
- YAML config file for all settings and credentials

**Requirements:**
- Azure SDK: `azure-identity`, `azure-mgmt-dns`
- `pyyaml` package
- Config file: `azure_ddns_updater_config.yaml` (next to the script)

**Setup:**

1. **Create a Service Principal** with DNS access:
   ```bash
   az login
   az ad sp create-for-rbac --name "ddns-updater" --role "DNS Zone Contributor" \
     --scopes /subscriptions/<SUBSCRIPTION_ID>/resourceGroups/<RESOURCE_GROUP>/providers/Microsoft.Network/dnszones/<DNS_ZONE>
   ```
   Save the output — you'll need `appId`, `password`, and `tenant`.

2. **Create the config file** by copying the example:
   ```bash
   cp azure_ddns_updater_config.yaml.example azure_ddns_updater_config.yaml
   ```

3. **Fill in your values** in `azure_ddns_updater_config.yaml`:
   ```yaml
   subscription_id: "your-subscription-id"
   resource_group: "your-resource-group"
   dns_zone: "example.com"
   record_name: "subdomain"
   ttl: 300
   azure_tenant_id: "tenant from step 1"
   azure_client_id: "appId from step 1"
   azure_client_secret: "password from step 1"
   ```

> **Note:** The azure_ddns_updater_config.yaml.example is an example you can use.

**Usage:**
```bash
python3 azure_ddns_updater.py
```

**Scheduling (cron):**
```bash
0 * * * * /usr/bin/env python3 /path/to/azure_ddns_updater.py >> /tmp/azure_ddns_updater.log 2>&1
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
- `requests`, `pyyaml` packages
- Pushover account with API token
- Config file: `ip_changer_notifier_config.yaml` (next to the script)

**Setup:**

1. **Copy the example config:**
   ```bash
   cp ip_changer_notifier_config.yaml.example ip_changer_notifier_config.yaml
   ```

2. **Fill in your values** in `ip_changer_notifier_config.yaml`:
   ```yaml
   pushover_user_key: "your-pushover-user-key"
   pushover_api_token: "your-pushover-api-token"
   ```

> **Note:** The ip_changer_notifier_config.yaml.example is an example you can use.

**Usage:**
```bash
python3 ip_changer_notifier.py
```

**Scheduling (cron):**
```bash
*/15 * * * * /usr/bin/env python3 /path/to/ip_changer_notifier.py >> /tmp/ip_changer_notifier.log 2>&1
```

---

## Download & Transfer

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

## Camera & Media

> **Deprecated:** The Blink camera scripts (`blink.py` and `download_blink_videos.py`) have been moved to a dedicated project: [watchman](https://github.com/renanfernandes/watchman).

---

## Home Automation

### 🏠 `home-assistant/s31.yaml`
**ESPHome configuration for Sonoff S31 Power Outlet**

ESPHome configuration for integrating a Sonoff S31 smart power outlet with Home Assistant, used to control the living room bar outlet.

**Features:**
- GPIO relay control (GPIO12)
- Physical power button support (GPIO0)
- UART communication at 4800 baud (EVEN parity)
- CSE7766 power monitoring (current, voltage, power)
- WiFi signal strength monitoring
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

## Configuration

### Environment Variables

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

# Transmission
export TRANSMISSION_USER="transmission"
export TRANSMISSION_PASSWORD="transmission"
export TRANSMISSION_HOST="localhost"
export TRANSMISSION_PORT="6969"
```

### Common Dependencies

Install common dependencies using pip:
```bash
pip install requests paramiko pushover transmission-rpc azure-identity azure-mgmt-dns
```

Or install only what you need based on which scripts you're using.

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

## Troubleshooting

### Common Issues

**ImportError: No module named 'requests' / 'paramiko' / etc.**
- Install missing dependencies: `pip install requests` (or the missing package)
- Ensure you're using the same Python version for both installation and running scripts

**Permission denied when running script**
- Make script executable: `chmod +x script_name.py`
- Check file permissions: `ls -l script_name.py`

**Environment variables not being recognized**
- Verify they're set: `echo $PUSHOVER_USER_KEY`
- Make sure you exported them in the same shell session
- For persistent variables, add to `~/.bashrc` or `~/.zshrc`

**Script timeout or hangs**
- Check network connectivity (for Azure, Blink, NZBGet scripts)
- Verify credentials are correct
- Check if external services are running (Transmission daemon, NZBGet, etc.)

**Cron jobs not running**
- Check cron is working: `crontab -l`
- Ensure full paths are used in cron commands
- Check cron logs: `log stream --predicate 'process == "cron"'` (macOS)
- Environment variables may not be available in cron - export them in script or use full paths

### Getting Help

- Check individual script documentation in this README
- Review script source code for inline comments
- Enable verbose logging if the script supports it
- Check system logs for error messages

## Notes

- Most scripts use timestamped logging for better tracking
- Credentials should always be stored in environment variables, not in the scripts
- Some scripts maintain local state files (e.g., `last_ip.txt`, `last_notification.txt`)
- Ensure appropriate file permissions and Python 3.6+ installed
