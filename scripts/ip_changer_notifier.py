#!/usr/bin/env python3

import requests
import os
from datetime import datetime

# Configuration
PUSHOVER_USER_KEY = os.getenv('PUSHOVER_USER_KEY')
PUSHOVER_API_TOKEN = os.getenv('PUSHOVER_API_TOKEN')
IP_FILE = 'last_ip.txt'

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
        log_message("Pushover credentials are not set in environment variables.")
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