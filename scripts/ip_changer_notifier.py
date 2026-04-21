#!/usr/bin/env python3
"""
ip_changer_notifier.py: Script to monitor external IP address changes and send notifications via Pushover.

Changelog:
- 2025-02-02 
    - Initial version created.
    - Added timestamped logging for better tracking of events.
- 2025-02-03: Moved Pushover credentials to environment variables for enhanced security.
- 2026-04-21: Replaced environment variables with YAML config file (ip_changer_notifier_config.yaml).
"""

import sys
import requests
import os
import yaml
from datetime import datetime

# Load configuration from YAML file.
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ip_changer_notifier_config.yaml")

try:
    with open(CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)
except FileNotFoundError:
    print(f"Error: Config file not found at {CONFIG_PATH}")
    sys.exit(1)
except yaml.YAMLError as e:
    print(f"Error: Failed to parse config file: {e}")
    sys.exit(1)

# Configuration
PUSHOVER_USER_KEY = config.get('pushover_user_key')
PUSHOVER_API_TOKEN = config.get('pushover_api_token')
IP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'last_ip.txt')

def log_message(message):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}")

def get_external_ip():
    try:
        response = requests.get('https://api.ipify.org?format=text')
        response.raise_for_status()
        return response.text.strip()
    except requests.RequestException as e:
        log_message(f"Error fetching IP: {e}")
        return None

def send_pushover_notification(message):
    if not PUSHOVER_USER_KEY or not PUSHOVER_API_TOKEN:
        log_message("Pushover credentials are not set in config file.")
        return

    payload = {
        'token': PUSHOVER_API_TOKEN,
        'user': PUSHOVER_USER_KEY,
        'message': message
    }
    try:
        response = requests.post('https://api.pushover.net/1/messages.json', data=payload)
        response.raise_for_status()
        log_message("Notification sent successfully.")
    except requests.RequestException as e:
        log_message(f"Error sending notification: {e}")

def load_last_ip():
    if os.path.exists(IP_FILE):
        with open(IP_FILE, 'r') as file:
            return file.read().strip()
    return None

def save_current_ip(ip):
    with open(IP_FILE, 'w') as file:
        file.write(ip)

def main():
    current_ip = get_external_ip()
    if current_ip is None:
        return

    last_ip = load_last_ip()
    if current_ip != last_ip:
        message = f"External IP has changed to: {current_ip}"
        send_pushover_notification(message)
        save_current_ip(current_ip)
    else:
        log_message("IP has not changed.")

if __name__ == '__main__':
    main()