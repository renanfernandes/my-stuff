# Lab Stuff
![commit](https://img.shields.io/github/last-commit/renanfernandes/my-stuff)

Welcome to my public repository. This is a collection of automation scripts, configuration files, templates, documentation, and notes to make my life easier. This is part of my own GitHub repository, so use the content and scripts here at your own risk :)

## 📁 Directory Structure

### 🔧 [scripts/](scripts/)
**Automation scripts for home services and monitoring**

Collection of Python automation scripts including:
- **Azure DNS updater**: Dynamic DNS management for Azure
- **IP change notifier**: Monitor and notify on external IP changes
- **Blink integration**: Download videos from Blink cameras
- **NZBGet transfers**: Automated SFTP transfers for completed downloads
- **Torrent monitor**: Check Transmission for completed torrents
- **Home Assistant**: ESPHome configurations for smart home devices

See [scripts/README.md](scripts/README.md) for detailed documentation.

### 📚 [notebooks/](notebooks/)
**Jupyter notebooks for data analysis and exploration**

- `usd_brl_transfer_analysis.ipynb`: Analysis of USD/BRL transfer rates

### 💬 [simple-budget-chat/](simple-budget-chat/)
**Simple chat application for budget discussion**

Stand-alone chat application with minimal dependencies.

### 🏠 [homepage/](homepage/)
**Home Assistant service configurations**

Service configurations for Home Assistant automations and integrations.

### 💰 [actual-budget/](actual-budget/)
**Actual Budget application integration**

Systemd service and configurations for Actual Budget financial management application.

### 📖 [Documentation Files](/)
- [stuff.md](stuff.md) - Random tips and useful information
- [SENTINEL.md](SENTINEL.md) - Guide for integrating Sentinel with pfSense
- [UDM_Sentinel.md](UDM_Sentinel.md) - Sentinel integration for UDM devices

## 🚀 Quick Start

### Prerequisites
- Python 3.6+
- pip for package management
- Optional: Docker, Home Assistant, NZBGet, Transmission

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/renanfernandes/my-stuff.git
   cd my-stuff
   ```

2. **Install script dependencies:**
   ```bash
   pip install -r scripts/requirements.txt
   ```
   Or install specific dependencies based on which scripts you need.

3. **Configure environment variables:**
   ```bash
   export PUSHOVER_USER_KEY="your-key"
   export PUSHOVER_API_TOKEN="your-token"
   # ... see scripts/README.md for full list
   ```

### Running Scripts

See [scripts/README.md](scripts/README.md) for detailed instructions on each script.

Example:
```bash
cd scripts
python3 ip_changer_notifier.py
```

## 📝 Notes

- All sensitive credentials should be stored in environment variables, never hardcoded
- Most scripts are designed to be run via cron jobs for periodic automation
- Ensure appropriate file permissions before running systemd services
- Check individual README files in subdirectories for specific setup instructions

## ⚠️ Disclaimer

This repository contains personal automation scripts and configurations. Use at your own risk and always review code before running it on your system.
