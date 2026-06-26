#!/usr/bin/env python3
"""
raspi_sd_backup.py: Create full Raspberry Pi SD card image backups over SSH and store them locally (e.g., iCloud Drive).

How it works:
- Reads host and backup settings from raspi_sd_backup_config.yaml
- Connects to each Raspberry Pi via SSH
- Streams a compressed image of the SD device to this Mac
- Saves each backup under backup_root/<host_name>/
- Writes a SHA256 checksum file
- Prunes old backups based on retention_count

Notes:
- This performs a live image backup. For the most consistent image, stop write-heavy services beforehand.
- Remote user must be able to run `sudo -n dd` without interactive password prompts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import pathlib
import shlex
import subprocess
import sys
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    print("Error: Missing dependency 'requests'. Install with: pip install requests")
    sys.exit(1)

try:
    import yaml
except ImportError:  # pragma: no cover
    print("Error: Missing dependency 'pyyaml'. Install with: pip install pyyaml")
    sys.exit(1)

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "raspi_sd_backup_config.yaml"
DEFAULT_LOG_PATH = SCRIPT_DIR / "raspi_sd_backup.log"


def log(message: str) -> None:
    timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


def send_pushover_notification(cfg: dict[str, Any], message: str, title: str = "Raspberry Pi Backup") -> None:
    pushover_cfg = cfg.get("pushover")
    if not isinstance(pushover_cfg, dict):
        return

    if not pushover_cfg.get("enabled", False):
        return

    user_key = pushover_cfg.get("user_key")
    api_token = pushover_cfg.get("api_token")
    if not user_key or not api_token:
        log("Pushover is enabled but credentials are missing. Skipping notification.")
        return

    payload = {
        "token": str(api_token),
        "user": str(user_key),
        "title": title,
        "message": message,
    }

    try:
        response = requests.post(
            "https://api.pushover.net/1/messages.json",
            data=payload,
            timeout=20,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        log(f"Failed to send Pushover notification: {exc}")


def load_config(config_path: pathlib.Path) -> dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. Create it from raspi_sd_backup_config.yaml.example"
        )

    try:
        with config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in config file {config_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Top-level config must be a YAML mapping/object.")

    if "hosts" not in data or not isinstance(data["hosts"], list) or not data["hosts"]:
        raise ValueError("Config must include a non-empty 'hosts' list.")

    return data


def ensure_dir(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def run_capture(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, capture_output=True)


def build_ssh_base(host_cfg: dict[str, Any], global_cfg: dict[str, Any]) -> list[str]:
    user = host_cfg.get("ssh_user") or global_cfg.get("ssh_user") or "pi"
    port = str(host_cfg.get("ssh_port") or global_cfg.get("ssh_port") or 22)
    strict_host_key = host_cfg.get("strict_host_key_checking")
    if strict_host_key is None:
        strict_host_key = global_cfg.get("strict_host_key_checking", True)

    host = host_cfg.get("host")
    if not host:
        raise ValueError("Each host requires 'host'.")

    ssh_cmd = ["ssh", "-p", port]
    if not strict_host_key:
        ssh_cmd.extend([
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
        ])

    extra_opts = host_cfg.get("ssh_options") or global_cfg.get("ssh_options") or []
    if not isinstance(extra_opts, list):
        raise ValueError("ssh_options must be a YAML list.")

    for opt in extra_opts:
        ssh_cmd.extend(["-o", str(opt)])

    ssh_cmd.append(f"{user}@{host}")
    return ssh_cmd


def validate_remote_access(ssh_base: list[str], host_name: str) -> None:
    log(f"[{host_name}] Validating SSH and non-interactive sudo access...")
    probe = run_capture(ssh_base + ["sudo -n true"])
    if probe.returncode != 0:
        stderr = (probe.stderr or "").strip()
        raise RuntimeError(
            f"[{host_name}] Cannot run 'sudo -n' remotely. Configure passwordless sudo for dd. Details: {stderr}"
        )


def backup_single_host(host_cfg: dict[str, Any], global_cfg: dict[str, Any], backup_root: pathlib.Path) -> pathlib.Path:
    host_name = host_cfg.get("name")
    if not host_name:
        raise ValueError("Each host requires 'name'.")

    source_device = str(host_cfg.get("source_device", "/dev/mmcblk0"))
    gzip_level = int(global_cfg.get("gzip_level", 1))
    gzip_level = min(max(gzip_level, 1), 9)

    ssh_base = build_ssh_base(host_cfg, global_cfg)
    validate_remote_access(ssh_base, host_name)

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    host_dir = backup_root / host_name
    ensure_dir(host_dir)

    final_image_path = host_dir / f"{host_name}_{timestamp}.img.gz"
    partial_image_path = host_dir / f"{host_name}_{timestamp}.img.gz.partial"
    sha_path = host_dir / f"{host_name}_{timestamp}.img.gz.sha256"

    remote_pipeline = (
        f"sudo -n dd if={shlex.quote(source_device)} bs=4M status=none | "
        f"gzip -{gzip_level} -c"
    )

    log(f"[{host_name}] Starting image stream from {source_device}...")
    try:
        with partial_image_path.open("wb") as outfile:
            proc = subprocess.Popen(
                ssh_base + [remote_pipeline],
                stdout=outfile,
                stderr=subprocess.PIPE,
            )
            _, stderr = proc.communicate()
    except KeyboardInterrupt:
        log(f"[{host_name}] Backup interrupted by user. Stopping remote process...")
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
        partial_image_path.unlink(missing_ok=True)
        raise

    if proc.returncode != 0:
        partial_image_path.unlink(missing_ok=True)
        stderr_text = (stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"[{host_name}] Backup failed. SSH/dd output: {stderr_text}")

    partial_image_path.rename(final_image_path)
    log(f"[{host_name}] Backup completed: {final_image_path}")

    sha256 = hashlib.sha256()
    with final_image_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha256.update(chunk)

    checksum = sha256.hexdigest()
    with sha_path.open("w", encoding="utf-8") as f:
        f.write(f"{checksum}  {final_image_path.name}\n")

    log(f"[{host_name}] Checksum written: {sha_path}")
    return final_image_path


def prune_old_backups(backup_root: pathlib.Path, host_cfg: dict[str, Any], retention_count: int) -> None:
    host_name = host_cfg.get("name")
    if not host_name:
        return

    host_dir = backup_root / host_name
    if not host_dir.exists():
        return

    images = sorted(host_dir.glob("*.img.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    if len(images) <= retention_count:
        return

    for old_image in images[retention_count:]:
        sha_path = old_image.with_suffix(old_image.suffix + ".sha256")
        log(f"[{host_name}] Pruning old backup: {old_image}")
        old_image.unlink(missing_ok=True)
        sha_path.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backup Raspberry Pi SD card images over SSH.")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"Path to YAML config file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--host",
        action="append",
        help="Optional host name filter. Can be used multiple times.",
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

    backup_root = pathlib.Path(cfg.get("backup_root", "")).expanduser()
    if not backup_root:
        log("Error: 'backup_root' is required in config.")
        return 1

    ensure_dir(backup_root)

    retention_count = int(cfg.get("retention_count", 6))
    retention_count = max(retention_count, 1)

    selected_hosts = set(args.host or [])
    hosts: list[dict[str, Any]] = cfg.get("hosts", [])

    enabled_hosts: list[dict[str, Any]] = []
    for host_cfg in hosts:
        if not isinstance(host_cfg, dict):
            continue
        if not host_cfg.get("enabled", True):
            continue
        name = host_cfg.get("name")
        if selected_hosts and name not in selected_hosts:
            continue
        enabled_hosts.append(host_cfg)

    if not enabled_hosts:
        log("No enabled hosts matched the selection.")
        return 1

    run_target = ", ".join(h.get("name", "unknown") for h in enabled_hosts)
    send_pushover_notification(
        cfg,
        message=f"Backup started for host(s): {run_target}",
        title="Raspberry Pi Backup Started",
    )

    success_count = 0
    failure_count = 0

    try:
        for host_cfg in enabled_hosts:
            host_name = host_cfg.get("name", "unknown")
            try:
                backup_single_host(host_cfg, cfg, backup_root)
                prune_old_backups(backup_root, host_cfg, retention_count)
                success_count += 1
            except Exception as exc:  # noqa: BLE001
                log(f"[{host_name}] Error: {exc}")
                send_pushover_notification(
                    cfg,
                    message=f"Backup failed for {host_name}: {exc}",
                    title="Raspberry Pi Backup Failed",
                )
                failure_count += 1
    except KeyboardInterrupt:
        log("Backup run cancelled by user.")
        send_pushover_notification(
            cfg,
            message="Backup run cancelled by user.",
            title="Raspberry Pi Backup Cancelled",
        )
        return 130

    summary = f"Finished. Successful hosts: {success_count}. Failed hosts: {failure_count}."
    log(summary)

    if failure_count == 0:
        send_pushover_notification(
            cfg,
            message=summary,
            title="Raspberry Pi Backup Completed",
        )
    else:
        send_pushover_notification(
            cfg,
            message=summary,
            title="Raspberry Pi Backup Completed With Errors",
        )

    return 0 if failure_count == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
