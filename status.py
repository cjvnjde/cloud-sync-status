#!/usr/bin/env python3
"""Observe rclone only. Never launch transfers, change services, or contact clouds."""
import argparse
import configparser
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import urllib.request
from urllib.error import HTTPError
from urllib.parse import urlsplit

COMMANDS = {"bisync", "sync", "copy", "mount", "cmount", "nfsmount"}
MOUNTS = {"mount", "cmount", "nfsmount"}
PROVIDERS = {"protondrive": "Proton Drive", "drive": "Google Drive", "onedrive": "OneDrive", "dropbox": "Dropbox", "s3": "Amazon S3", "mega": "MEGA", "pcloud": "pCloud", "box": "Box"}
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
BYTE = r"([\d.]+)\s*([KMGTPE]?i?B)"
PROGRESS = re.compile(BYTE + r"\s*/\s*" + BYTE + r",\s*([\d.]+)%,\s*" + BYTE + r"/s,\s*ETA\s+([^\n]+)")
VALUE_FLAGS = {"--config", "--cache-dir", "--workdir", "--log-file", "--log-level", "--stats", "--rc-addr", "--rc-user", "--rc-pass", "--transfers", "--checkers", "--filter", "--exclude", "--include", "--backup-dir", "--backup-dir1", "--backup-dir2", "--compare", "--max-lock", "--resync-mode", "--conflict-resolve", "--conflict-loser", "--conflict-suffix", "--vfs-cache-mode", "--vfs-cache-max-size", "--dir-cache-time", "--max-delete", "--bwlimit", "--tpslimit", "--stats-log-level"}


def run(args, timeout=4):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return p.stdout if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def records(text):
    for line in text.splitlines():
        try:
            yield json.loads(line)
        except (ValueError, TypeError):
            continue


def message(record):
    value = record.get("MESSAGE", "")
    if isinstance(value, list):
        value = bytes(value).decode("utf-8", "replace")
    return ANSI.sub("", str(value)).strip()


def bytes_value(value, unit):
    power = "BKMGTPE".index(unit[0]) if unit[0] != "B" else 0
    return round(float(value) * (1024 if "i" in unit else 1000) ** power)


def duration(seconds):
    if seconds is None:
        return ""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def option(args, key):
    for i, arg in enumerate(args):
        if arg.startswith(key + "="):
            return arg.split("=", 1)[1]
        if arg == key and i + 1 < len(args):
            return args[i + 1]
    return None


def parse_job(args):
    """Retain only paths and monitoring metadata, never credentials/other flags."""
    if not args or Path(args[0]).name != "rclone":
        return None
    index = next((i for i, arg in enumerate(args[1:], 1) if arg in COMMANDS), None)
    if index is None:
        return None
    paths, skip = [], False
    for arg in args[index + 1:]:
        if skip:
            skip = False
            continue
        if arg.startswith("-"):
            skip = "=" not in arg and arg in VALUE_FLAGS
        else:
            paths.append(arg)
        if len(paths) == 2:
            break
    if len(paths) != 2:
        return None
    remotes = [p.split(":", 1)[0] for p in paths if re.match(r"^[^/:]+:", p)]
    if not remotes:
        return None
    return {"mode": args[index], "paths": paths, "remotes": remotes,
            "localPath": next((p for p in paths if p.startswith("/") or p.startswith("~")), ""),
            "configPath": option(args, "--config"),
            "workdir": option(args, "--workdir"),
            "initializing": "--resync" in args or option(args, "--resync-mode") not in (None, "none"),
            "rcAddress": (option(args, "--rc-addr") or "localhost:5572") if any(arg in {"--rc", "--rc=true"} for arg in args) else None}


def unit_from_cgroup(group):
    leaf = group.strip().rsplit("/", 1)[-1]
    return leaf if leaf.endswith(".service") else ""


def active_jobs():
    jobs = {}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid() or (proc / "comm").read_text().strip() != "rclone":
                continue
            args = (proc / "cmdline").read_bytes().decode("utf-8", "replace").strip("\0").split("\0")
            job = parse_job(args)
            if not job:
                continue
            group = (proc / "cgroup").read_text()
            unit = unit_from_cgroup(group)
            job.update(pid=int(proc.name), unit=unit)
            jobs[unit or "pid:" + proc.name] = job
        except (OSError, ValueError):
            continue
    return jobs


def remote_types(config_path=None):
    parser = configparser.RawConfigParser(strict=False)
    try:
        if not config_path:
            config_path = os.environ.get("RCLONE_CONFIG")
        if not config_path:
            config_path = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "rclone/rclone.conf"
            if not config_path.is_file() and (Path.home() / ".rclone.conf").is_file():
                config_path = Path.home() / ".rclone.conf"
        parser.read(Path(config_path).expanduser(), encoding="utf-8")
        return {name: parser.get(name, "type", fallback="unknown") for name in parser.sections()}
    except (configparser.Error, OSError, UnicodeError):
        return {}


def properties(unit, names):
    if not unit:
        return {}
    text = run(["systemctl", "--user", "show", unit, "--no-pager", "--property=" + ",".join(names)])
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


class Progress:
    def __init__(self):
        self.invocation = ""
        self.cursor = ""
        self.completed = 0
        self.total_files = None
        self.transferred = None
        self.total_bytes = None
        self.speed = None
        self.eta = ""
        self.error_count = 0
        self.success = False
        self.success_time = None
        self.last_file = ""
        self.updated = None
        self.reported = None
        self.truncated = False

    def consume(self, entry):
        self.cursor = entry.get("__CURSOR", self.cursor)
        text = message(entry)
        self.updated = float(entry.get("__REALTIME_TIMESTAMP", 0)) / 1e6 or self.updated
        if re.search(r": Copied(?: \(|$)", text):
            self.completed += 1
            self.last_file = re.sub(r"^.*?(?:INFO|NOTICE)\s*:\s*", "", text).rsplit(": Copied", 1)[0]
        if "Bisync successful" in text:
            self.success = True
            self.success_time = self.updated
        if re.search(r"\b(?:ERROR|CRITICAL)\b", text):
            self.error_count += 1
        for match in PROGRESS.finditer(text):
            v = match.groups()
            self.transferred = bytes_value(v[0], v[1])
            self.total_bytes = bytes_value(v[2], v[3])
            self.speed = bytes_value(v[5], v[6])
            self.eta = v[7].strip()
            self.reported = self.updated
        match = re.search(r"Transferred:\s*(\d+)\s*/\s*(\d+),", text)
        if match:
            self.completed, self.total_files = map(int, match.groups())


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep local status requests local, including after an HTTP redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def rc_stats(address):
    """Only GET status equivalents via loopback RC; never call a mutation endpoint."""
    if not address:
        return None
    try:
        if address.startswith(":"):
            address = "127.0.0.1" + address
        parsed = urlsplit(address if "://" in address else "http://" + address)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password:
            return None
        req = urllib.request.Request(parsed.geturl().rstrip("/") + "/core/stats", data=b"{}", headers={"Content-Type": "application/json"})
        # Ignore environment proxies; this request must stay on this machine.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirectHandler())
        with opener.open(req, timeout=0.5) as response:
            result = json.load(response)
            return result if isinstance(result, dict) else None
    except HTTPError as exc:
        exc.close()
        return None
    except (OSError, ValueError):
        return None


class Monitor:
    def __init__(self):
        self.jobs = {}
        self.progress = {}
        self.discovered = 0
        self.providers = {}
        self.last_success = {}
        self.inventory_cache = {}
        self.config_types = {}

    def inventory(self, job, last_success):
        if not last_success or job.get("mode") != "bisync" or not job.get("workdir"):
            return None
        local_path = job.get("localPath")
        paths = job.get("paths", [])
        if not local_path or local_path not in paths:
            return None
        # Dedicated workdirs can be attributed safely. Do not guess among jobs.
        try:
            directory = Path(job["workdir"]).expanduser()
            pairs = [(p, p.with_name(p.name.replace('.path1.lst', '.path2.lst')))
                     for p in directory.glob('*.path1.lst')]
            pairs = [(a, b) for a, b in pairs if b.is_file()]
            if len(pairs) != 1:
                return None
            a, b = pairs[0]
            signature = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in (a, b))
            if any(abs(p.stat().st_mtime - last_success) > 10 for p in (a, b)):
                return None
            if signature not in self.inventory_cache:
                counts = []
                for path in (a, b):
                    with path.open() as source:
                        counts.append(sum(1 for line in source if line.strip() and not line.startswith('#')))
                local_index = paths.index(local_path)
                if len(self.inventory_cache) >= 64:
                    self.inventory_cache.pop(next(iter(self.inventory_cache)))
                self.inventory_cache[signature] = {"localItems": counts[local_index], "remoteItems": counts[1 - local_index]}
            return self.inventory_cache[signature]
        except (OSError, ValueError):
            return None

    def discover(self):
        self.providers = remote_types()
        self.config_types.clear()
        # Recent journal commands allow completed/failed jobs to remain visible.
        for entry in records(run(["journalctl", "--user", "_COMM=rclone", "-n", "250", "-o", "json", "--no-pager"])):
            try:
                job = parse_job(shlex.split(entry.get("_CMDLINE", "")))
            except ValueError:
                continue
            unit = entry.get("_SYSTEMD_USER_UNIT", "")
            if unit and "Bisync successful" in message(entry):
                timestamp = float(entry.get("__REALTIME_TIMESTAMP", 0)) / 1e6
                self.last_success[unit] = max(timestamp, self.last_success.get(unit, 0))
            if job and unit:
                job.update(unit=unit, pid=None, invocation=entry.get("_SYSTEMD_INVOCATION_ID", ""))
                self.jobs[unit] = job
        self.discovered = time.monotonic()

    def snapshot(self):
        if time.monotonic() - self.discovered > 30 or not self.discovered:
            self.discover()
        active = active_jobs()
        for key, job in active.items():
            self.jobs[key] = {**self.jobs.get(key, {}), **job}
        clouds = []
        for key, job in self.jobs.items():
            unit = job.get("unit", "")
            state = properties(unit, ["ActiveState", "SubState", "Result", "InvocationID", "ExecMainExitTimestamp", "ExecMainStatus"])
            invocation = state.get("InvocationID") or job.get("invocation", "")
            progress = self.progress.setdefault(key, Progress())
            if invocation != progress.invocation:
                progress = self.progress[key] = Progress()
                progress.invocation = invocation
            if unit and invocation:
                cmd = ["journalctl", "--user", "-u", unit, "_SYSTEMD_INVOCATION_ID=" + invocation, "-o", "json", "--no-pager"]
                if progress.cursor:
                    cmd += ["--after-cursor", progress.cursor]
                else:
                    cmd += ["-n", "50000"]
                entries = list(records(run(cmd, timeout=8)))
                progress.truncated |= len(entries) >= 50000
                for entry in entries:
                    progress.consume(entry)
            if progress.success_time:
                self.last_success[key] = max(progress.success_time, self.last_success.get(key, 0))
            last_success = self.last_success.get(key)
            inventory = self.inventory(job, last_success)
            running = key in active
            mounted = job["mode"] in MOUNTS
            failed = state.get("ActiveState") == "failed" or (not running and state.get("Result", "success") != "success")
            timer = properties(unit.removesuffix(".service") + ".timer", ["ActiveState", "NextElapseUSecRealtime"]) if unit else {}
            scheduled = timer.get("ActiveState") == "active"
            if running:
                status = "mounted" if mounted else "syncing"
            elif unit and not state:
                status = "unavailable"
            elif failed:
                status = "error"
            elif progress.success:
                status = "synced" if scheduled else "stopped"
            else:
                status = "waiting" if scheduled else "stopped"
            stats = rc_stats(job.get("rcAddress")) if running else None
            files_done = stats.get("transfers") if stats is not None else progress.completed
            files_total = stats.get("totalTransfers") if stats is not None else progress.total_files
            # RC totalTransfers = completed + bounded queue + active transfers.
            # It does not include work the producer has not enqueued yet.
            queued_files = max(0, files_total - files_done) if files_total is not None and files_done is not None else None
            done_bytes = stats.get("bytes") if stats is not None else progress.transferred
            reported_bytes = stats.get("totalBytes") if stats is not None else progress.total_bytes
            queued_bytes = max(0, reported_bytes - done_bytes) if reported_bytes is not None and done_bytes is not None else None
            active_files = []
            if stats is not None:
                for item in stats.get("transferring", []):
                    percent = item.get("percentage")
                    active_files.append({"name": item.get("name", ""), "bytes": item.get("bytes"),
                                         "size": item.get("size"), "percentage": max(0, min(100, percent)) if percent is not None and percent >= 0 else None})
            config_path = job.get("configPath")
            if config_path and config_path not in self.config_types:
                self.config_types[config_path] = remote_types(config_path)
            providers = self.config_types.get(config_path, self.providers)
            for remote in job["remotes"]:
                provider = providers.get(remote, "unknown")
                clouds.append({
                    "remote": remote, "provider": PROVIDERS.get(provider, provider), "mode": job["mode"],
                    "state": status, "localPath": job.get("localPath", ""),
                    "remotePath": next((p for p in job["paths"] if p.startswith(remote + ":")), remote + ":"),
                    "unit": unit, "jobId": key, "scheduled": scheduled, "completedFiles": files_done,
                    "phase": "scanning" if running and stats is not None and not stats.get("transferring") else "",
                    "elapsed": stats.get("elapsedTime") if stats is not None else None,
                    "elapsedText": duration(stats.get("elapsedTime")) if stats is not None else "",
                    "lastSuccess": last_success, "verifiedInventory": inventory,
                    "listed": stats.get("listed") if stats is not None else None,
                    "checks": stats.get("checks") if stats is not None else None,
                    "queuedFiles": queued_files, "activeFiles": active_files,
                    "bytes": done_bytes, "queuedBytes": queued_bytes,
                    "speed": stats.get("speed") if stats is not None else (progress.speed if running else 0),
                    "eta": (duration(stats.get("eta")) if stats is not None else progress.eta) if running else "", "errors": stats.get("errors", 0) if stats is not None else progress.error_count,
                    "lastFile": progress.last_file, "lastUpdate": progress.updated, "lastReport": time.time() if stats is not None else progress.reported, "limitedHistory": progress.truncated,
                    "source": "live" if stats is not None else "journal", "initializing": running and job.get("initializing", False),
                })
        for remote, provider in self.providers.items():
            if not any(c["remote"] == remote for c in clouds):
                clouds.append({"remote": remote, "provider": PROVIDERS.get(provider, provider), "state": "unmonitored", "mode": "", "localPath": "", "remotePath": remote + ":"})
        clouds.sort(key=lambda c: (c["remote"].lower(), c.get("unit", "")))
        return {"clouds": clouds, "updated": time.time(), "error": ""}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=float, default=0, metavar="SECONDS")
    args = parser.parse_args()
    monitor = Monitor()
    while True:
        try:
            result = monitor.snapshot()
        except Exception as exc:
            result = {"clouds": [], "updated": time.time(), "error": "Status unavailable (" + type(exc).__name__ + ")"}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not args.watch:
            break
        time.sleep(max(2, args.watch))


if __name__ == "__main__":
    main()
