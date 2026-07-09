import sys
from pathlib import Path
import unittest

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pihole_sync


class PiHoleSyncTests(unittest.TestCase):
    def test_describe_ssh_error_for_passwordless_sudo(self):
        message = pihole_sync.describe_ssh_error("sudo: a password is required")
        self.assertIn("passwordless sudo", message)

    def test_describe_ssh_error_for_unavailable_ssh_key(self):
        message = pihole_sync.describe_ssh_error("Permission denied (publickey,password)")
        self.assertIn("SSH key", message)

    def test_parse_toml_records(self):
        sample = '''[dns]
hosts = [ "192.168.50.2 router", "192.168.50.3 printer homeprinter" ]
'''
        records = pihole_sync.parse_toml_records(sample)
        self.assertEqual(records, [
            {"ip": "192.168.50.2", "names": ["router"]},
            {"ip": "192.168.50.3", "names": ["printer", "homeprinter"]},
        ])

    def test_parse_toml_records_multiline_hosts(self):
        sample = '''[dns]
hosts = [
  "192.168.50.2 router",
  "192.168.50.3 printer homeprinter",
]
'''
        records = pihole_sync.parse_toml_records(sample)
        self.assertEqual(records, [
            {"ip": "192.168.50.2", "names": ["router"]},
            {"ip": "192.168.50.3", "names": ["printer", "homeprinter"]},
        ])

    def test_parse_custom_list_records(self):
        sample = "192.168.50.2 router\n192.168.50.3 printer homeprinter\n"
        records = pihole_sync.parse_custom_list_records(sample)
        self.assertEqual(records, [
            {"ip": "192.168.50.2", "names": ["router"]},
            {"ip": "192.168.50.3", "names": ["printer", "homeprinter"]},
        ])

    def test_build_toml_hosts_value(self):
        records = [
            {"ip": "192.168.50.2", "names": ["router"]},
            {"ip": "192.168.50.3", "names": ["printer", "homeprinter"]},
        ]
        rendered = pihole_sync.render_toml_hosts_value(records)
        self.assertIn('[', rendered)
        self.assertIn('"192.168.50.2 router"', rendered)
        self.assertIn('"192.168.50.3 printer homeprinter"', rendered)


if __name__ == "__main__":
    unittest.main()
