#!/usr/bin/env python3
"""
pihole_sync.py: keep two Pi-hole instances aligned for local DNS records.

This script is intentionally conservative:
- it treats the primary Pi-hole as the source of truth
- it reads local DNS host entries from the Pi-hole FTL config / dnsmasq-style hosts entries
- it compares them against the secondary instance
- it applies only the missing or changed records to the secondary
- it reloads Pi-hole on the secondary after changes

It is designed to be run from a management machine with SSH access to both Pi-hole hosts.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shlex
import subprocess
import sys
import textwrap
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    print("Error: Missing dependency 'pyyaml'. Install with: pip install pyyaml")
    sys.exit(1)

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "pihole_sync_config.yaml"
DEFAULT_CONFIG_TEMPLATE = SCRIPT_DIR / "pihole_sync_config.yaml.example"


def log(message: str) -> None:
    print(message)


def load_config(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. Copy {DEFAULT_CONFIG_TEMPLATE.name} to {path.name} and edit it."
        )

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if not isinstance(data, dict):
        raise ValueError("Configuration file must contain a YAML mapping/object.")

    return data


def parse_toml_records(content: str) -> list[dict[str, Any]]:
    """Parse Pi-hole pihole.toml dns.hosts entries."""
    records: list[dict[str, Any]] = []
    in_dns_section = False
    collecting_hosts = False
    hosts_parts: list[str] = []

    def bracket_balance(value: str) -> int:
        return value.count("[") - value.count("]")

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "[dns]":
            in_dns_section = True
            continue
        if stripped.startswith("[") and stripped != "[dns]":
            in_dns_section = False
            continue
        if not in_dns_section:
            continue

        if not collecting_hosts:
            if not re.match(r'^\s*hosts\s*=', line):
                continue
            rhs = line.split("=", 1)[1].strip()
            if not rhs.startswith("["):
                continue
            hosts_parts = [rhs]
            if bracket_balance(rhs) <= 0:
                collecting_hosts = False
                break
            collecting_hosts = True
            continue

        hosts_parts.append(stripped)
        if bracket_balance(" ".join(hosts_parts)) <= 0:
            break

    if not hosts_parts:
        return []

    hosts_blob = " ".join(hosts_parts)
    start = hosts_blob.find("[")
    end = hosts_blob.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []

    body = hosts_blob[start + 1:end].strip()
    if not body:
        return []

    for entry in re.findall(r'"([^"]+)"', body):
        parts = entry.split()
        if len(parts) < 2:
            continue
        records.append({"ip": parts[0], "names": parts[1:]})

    return records


def parse_custom_list_records(content: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        records.append({"ip": parts[0], "names": parts[1:]})
    return records


def render_custom_list(records: list[dict[str, Any]]) -> str:
    lines = []
    for record in records:
        names = " ".join(record.get("names", []))
        lines.append(f'{record["ip"]} {names}')
    return "\n".join(lines)


def render_toml_hosts_value(records: list[dict[str, Any]]) -> str:
    entries = [f'"{record["ip"]} {" ".join(record.get("names", []))}"' for record in records]
    return "[ " + ", ".join(entries) + " ]"


def normalize_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for record in records:
        names = [name for name in record.get("names", []) if name]
        if not names:
            continue
        normalized.append({"ip": str(record.get("ip", "")).strip(), "names": sorted(names)})
    return sorted(normalized, key=lambda item: (item["ip"], tuple(item["names"])))


def build_record_signature(record: dict[str, Any]) -> str:
    return f"{record['ip']} {' '.join(record['names'])}"


def describe_ssh_error(output: str) -> str:
    lowered = output.lower()
    if "sudo: a password is required" in lowered or "a password is required" in lowered:
        return (
            "SSH succeeded, but sudo on the remote Pi-hole requires a password. "
            "Enable passwordless sudo for the Pi user, for example by adding an entry to sudoers."
        )
    if "permission denied" in lowered and "publickey" in lowered:
        return (
            "SSH authentication failed. Make sure your SSH key is installed on the Pi-hole host "
            "and that the remote user can log in without a password prompt."
        )
    if "host key verification failed" in lowered:
        return "SSH host key verification failed. Remove the stale host key from ~/.ssh/known_hosts and try again."
    return output.strip() or "SSH command failed."


def run_command(command: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, capture_output=True, timeout=timeout)


def ssh_command(host: str, user: str, port: int, command: str, strict_host_key: bool = True) -> list[str]:
    parts = ["ssh", "-tt", "-p", str(port), "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR"]
    if not strict_host_key:
        parts.extend(["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"])
    if user:
        parts.append(f"{user}@{host}")
    else:
        parts.append(host)
    parts.append(command)
    return parts


def run_interactive_ssh(command: list[str], stdout_path: pathlib.Path | None = None, timeout: int = 30) -> int:
    stdout_handle = None
    try:
        if stdout_path is not None:
            stdout_handle = stdout_path.open("w", encoding="utf-8")
        proc = subprocess.Popen(
            command,
            stdin=sys.stdin,
            stdout=stdout_handle if stdout_handle is not None else sys.stdout,
            stderr=sys.stderr,
            text=True,
        )
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
            raise
    finally:
        if stdout_handle is not None:
            stdout_handle.close()


def read_remote_records(host_cfg: dict[str, Any], global_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    host = str(host_cfg.get("host"))
    ssh_users = [host_cfg.get("ssh_user") or global_cfg.get("ssh_user") or ""]
    port = int(host_cfg.get("ssh_port") or global_cfg.get("ssh_port") or 22)
    strict_host_key = bool(host_cfg.get("strict_host_key_checking", global_cfg.get("strict_host_key_checking", True)))
    remote_path = str(host_cfg.get("remote_dns_file") or global_cfg.get("remote_dns_file") or "/etc/pihole/pihole.toml")

    last_error: str | None = None
    for user in ssh_users:
        output_path = pathlib.Path("/tmp") / f"pihole-sync-{host}.toml"
        log(f"[{host}] Waiting for sudo password prompt (if required)...")
        returncode = run_interactive_ssh(
            ssh_command(host, user, port, f"sudo cat {shlex.quote(remote_path)}", strict_host_key=strict_host_key),
            stdout_path=output_path,
            timeout=60,
        )
        if returncode == 0:
            output = output_path.read_text(encoding="utf-8").strip()
            return normalize_records(parse_toml_records(output))
        last_error = f"SSH command failed with exit code {returncode}"

    raise RuntimeError(f"Could not query remote records from {host}: {describe_ssh_error(last_error or 'SSH command failed')}")


def write_remote_records(host_cfg: dict[str, Any], global_cfg: dict[str, Any], records: list[dict[str, Any]]) -> None:
    ssh_users = [host_cfg.get("ssh_user") or global_cfg.get("ssh_user") or ""]
    port = int(host_cfg.get("ssh_port") or global_cfg.get("ssh_port") or 22)
    strict_host_key = bool(host_cfg.get("strict_host_key_checking", global_cfg.get("strict_host_key_checking", True)))
    host = str(host_cfg.get("host"))
    remote_path = str(host_cfg.get("remote_dns_file") or global_cfg.get("remote_dns_file") or "/etc/pihole/pihole.toml")
    rendered = render_toml_hosts_value(records)

    remote_python = "\n".join(
        [
            "from pathlib import Path",
            "import re",
            "",
            f"path = Path({remote_path!r})",
            f"new_hosts = {rendered!r}",
            "pattern = re.compile(r'^(\\s*hosts\\s*=\\s*)\\[.*\\](\\s*)$')",
            "lines = path.read_text(encoding='utf-8').splitlines()",
            "output = []",
            "in_dns = False",
            "replaced = False",
            "",
            "for line in lines:",
            "    stripped = line.strip()",
            "    if stripped == '[dns]':",
            "        in_dns = True",
            "        output.append(line)",
            "        continue",
            "    if stripped.startswith('[') and stripped != '[dns]':",
            "        if in_dns and not replaced:",
            "            output.append(f'hosts = {new_hosts}')",
            "            replaced = True",
            "        in_dns = False",
            "        output.append(line)",
            "        continue",
            "    if in_dns:",
            "        match = pattern.match(line)",
            "        if match:",
            "            output.append(f'{match.group(1)}{new_hosts}{match.group(2)}')",
            "            replaced = True",
            "            continue",
            "    output.append(line)",
            "",
            "if in_dns and not replaced:",
            "    output.append(f'hosts = {new_hosts}')",
            "",
            "path.write_text('\\n'.join(output) + '\\n', encoding='utf-8')",
        ]
    )
    remote_script = "\n".join(
        [
            "sudo python3 - <<'PY'",
            remote_python,
            "PY",
            "# Pi-hole command variants differ across versions; try common reload paths.",
            "sudo pihole reloaddns || sudo pihole reloadlists || sudo systemctl restart pihole-FTL",
        ]
    )

    last_error: str | None = None
    for user in ssh_users:
        log(f"[{host}] Waiting for sudo password prompt (if required)...")
        returncode = run_interactive_ssh(
            ssh_command(host, user, port, remote_script, strict_host_key=strict_host_key),
            timeout=120,
        )
        if returncode == 0:
            return
        last_error = f"SSH command failed with exit code {returncode}"

    raise RuntimeError(f"Failed to apply records to {host}: {describe_ssh_error(last_error or 'SSH command failed')}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synchronize Pi-hole local DNS records across two instances.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help=f"Path to YAML config (default: {DEFAULT_CONFIG_PATH})")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without applying it")
    parser.add_argument("--show-diff", action="store_true", help="Print source-only and target-only local DNS records")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Do not apply changes; exit with non-zero status when drift is detected",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = pathlib.Path(args.config).expanduser().resolve()

    try:
        cfg = load_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        log(f"Error: {exc}")
        return 1

    primary = cfg.get("primary")
    secondary = cfg.get("secondary")
    if not isinstance(primary, dict) or not isinstance(secondary, dict):
        log("Error: config must define 'primary' and 'secondary' host maps.")
        return 1

    try:
        source_records = normalize_records(read_remote_records(primary, cfg))
        target_records = normalize_records(read_remote_records(secondary, cfg))
    except Exception as exc:  # noqa: BLE001
        log(f"Error while reading remote Pi-hole state: {exc}")
        return 1

    source_signatures = {build_record_signature(record) for record in source_records}
    target_signatures = {build_record_signature(record) for record in target_records}

    missing = [record for record in source_records if build_record_signature(record) not in target_signatures]
    extra = [record for record in target_records if build_record_signature(record) not in source_signatures]

    if args.show_diff:
        if missing:
            log(f"Source-only records ({len(missing)}):")
            for record in missing:
                log(f" + {build_record_signature(record)}")
        if extra:
            log(f"Target-only records ({len(extra)}):")
            for record in extra:
                log(f" - {build_record_signature(record)}")
        if not missing and not extra:
            log("No source/target differences found.")

    if not missing and not extra:
        log("No missing local DNS records found; both instances already match.")
        return 0

    if args.check_only:
        log("Drift detected and --check-only was requested; no changes were applied.")
        return 2

    log(f"Found {len(missing)} record(s) to apply to {secondary.get('name', 'secondary')}:")
    for record in missing:
        log(f" - {build_record_signature(record)}")

    if args.dry_run:
        log("Dry run enabled; no changes were applied.")
        return 0

    try:
        write_remote_records(secondary, cfg, source_records)
    except Exception as exc:  # noqa: BLE001
        log(f"Error applying records: {exc}")
        return 1

    log("Pi-hole sync completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
