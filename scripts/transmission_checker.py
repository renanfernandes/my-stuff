#!/usr/bin/python3

"""Check if torrent download has completed"""

from transmission_rpc import Client
import pushover


def read_last_notification():
    try:
        with open("last_notification.txt", "r") as file:
            return file.read().strip()
    except FileNotFoundError:
        return None

def write_last_notification(notification):
    with open("last_notification.txt", "a") as file:
        file.write(notification + "\n")

def main():
    # Initialize the pushover and transmission-rpc objects
    notification = pushover.Client()
    c = Client(username='transmission', password='transmission')

    for t in c.get_torrents():
        if t.progress == 100.0:
            print("Finished " + t.name)
            last_notification = read_last_notification()
            print(last_notification)
            if t.name not in last_notification:
                notification.send_message("Torrent completed: " + t.name)
                write_last_notification(t.name)
        # else:
        #     print("Not finished " + t.name)

if __name__ == '__main__':
    main()
