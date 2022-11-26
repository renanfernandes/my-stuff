#!/usr/bin/python3

"""Check if torrent download has completed"""

from transmission_rpc import Client
import pushover


def main():
    notification = pushover.Client()
    c = Client(username='transmission', password='transmission')
    for t in c.get_torrents():
        if t.is_finished:
            notification.send_message("Torrent completed: " + t.name)


if __name__ == '__main__':
    main()