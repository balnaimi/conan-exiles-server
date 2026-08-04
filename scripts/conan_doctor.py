#!/usr/bin/env python3
"""Collect a secret-minimized Conan server diagnostic report."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import selectors
import shutil
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
Run = Callable[[list[str]], str]
MAX_COMMAND_OUTPUT = 1024 * 1024
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}$")
CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{12,64}$")
PROCESS_VALUES = {
    "ConanSandboxServer",
    "Xvfb",
    "bash",
    "python3",
    "sh",
    "sleep",
    "steamcmd",
    "timeout",
    "wine",
    "winedevice.exe",
    "wineserver",
}
STATE_VALUES = {"created", "running", "paused", "restarting", "removing", "exited", "dead"}
HEALTH_VALUES = {"none", "starting", "healthy", "unhealthy"}


def default_run(command: list[str]) -> str:
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    output = bytearray()
    total = 0
    deadline = time.monotonic() + 20
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.wait()
                raise RuntimeError(f"{command[0]} command timed out")
            for key, _ in selector.select(remaining):
                chunk = os.read(key.fd, 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > MAX_COMMAND_OUTPUT:
                    process.kill()
                    process.wait()
                    raise RuntimeError(f"{command[0]} output exceeded the diagnostic safety limit")
                if key.data == "stdout":
                    output.extend(chunk)
        returncode = process.wait()
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        process.stderr.close()
    if returncode != 0:
        raise RuntimeError(f"{command[0]} command failed with exit {returncode}")
    return output.decode("utf-8", errors="replace").strip()


def _safe_version(value: Any) -> str:
    candidate = str(value)
    return candidate if VERSION_RE.fullmatch(candidate) else "unknown"


def _bounded_int(value: Any, minimum: int, maximum: int, default: int = 0) -> int:
    candidate = str(value)
    if len(candidate) > 24 or not re.fullmatch(r"-?[0-9]+", candidate):
        return default
    try:
        parsed = int(candidate)
    except (TypeError, ValueError):
        return default
    return parsed if minimum <= parsed <= maximum else default


def _safe_percent(value: Any) -> float | None:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)%", str(value))
    if not match:
        return None
    parsed = float(match.group(1))
    return parsed if 0 <= parsed <= 1_000_000 else None


def _size_bytes(value: str) -> int | None:
    if len(value) > 40:
        return None
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(B|kB|MB|GB|TB|KiB|MiB|GiB|TiB)", value)
    if not match:
        return None
    decimal = {"B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}
    binary = {"KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    number = match.group(1)
    whole, separator, fraction = number.partition(".")
    scale = 10 ** len(fraction) if separator else 1
    numerator = int(whole + fraction) if separator else int(whole)
    result = numerator * (decimal | binary)[match.group(2)] // scale
    return result if 0 <= result <= 2**63 - 1 else None


def _size_pair(value: Any) -> dict[str, int] | None:
    parts = [part.strip() for part in str(value).split("/")]
    if len(parts) != 2:
        return None
    first, second = (_size_bytes(part) for part in parts)
    if first is None or second is None:
        return None
    return {"first_bytes": first, "second_bytes": second}


def _memory_summary() -> dict[str, int]:
    allowed = {"MemTotal", "MemAvailable"}
    result: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition(":")
            if name in allowed:
                fields = value.split()
                if fields and fields[0].isdigit():
                    result[f"{name.lower()}_kib"] = int(fields[0])
    except OSError:
        pass
    return result


def _cpu_capabilities() -> dict[str, bool]:
    flags: set[str] = set()
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition(":")
            if separator and name.strip() in {"flags", "Features"}:
                flags.update(value.split())
    except OSError:
        pass
    return {name: name in flags for name in ("sse4_2", "avx", "avx2")}


def _safe_container(raw: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    identifier = str(raw.get("id", raw.get("ID", "")))
    runtime_value = str(raw.get("runtime", ""))
    if not runtime_value:
        labels = str(raw.get("Labels", ""))
        match = re.search(r"(?:^|,)com\.balnaimi\.conan\.runtime=([^,]+)", labels)
        runtime_value = match.group(1) if match else ""
    runtime = runtime_value if runtime_value in {"wine", "native-linux"} else None
    safe = {"id": identifier[:12] if CONTAINER_ID_RE.fullmatch(identifier) else ""}
    return safe, runtime


def _version_summary(version_output: str, compose_output: str) -> dict[str, Any]:
    client = "unknown"
    server = "unknown"
    try:
        parsed = json.loads(version_output)
        if isinstance(parsed, dict):
            client_value = parsed.get("Client")
            server_value = parsed.get("Server")
            if isinstance(client_value, dict):
                client = _safe_version(client_value.get("Version", "unknown"))
            elif isinstance(client_value, str):
                client = _safe_version(client_value)
            if isinstance(server_value, dict):
                server = _safe_version(server_value.get("Version", "unknown"))
            elif isinstance(server_value, str):
                server = _safe_version(server_value)
    except json.JSONDecodeError:
        pass
    compose_match = re.search(r"(?:v)?([0-9]+(?:\.[0-9]+){1,2})", compose_output)
    return {
        "available": True,
        "client_version": client,
        "server_version": server,
        "compose_version": compose_match.group(1) if compose_match else "unknown",
    }


def _container_details(run: Run, identifier: str) -> dict[str, Any]:
    details: dict[str, Any] = {"collector_status": "partial"}
    inspect_template = (
        '{"status":"{{.State.Status}}",'
        '"health":"{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",'
        '"oom_killed":{{.State.OOMKilled}},'
        '"restart_count":{{.RestartCount}},'
        '"exit_code":{{.State.ExitCode}}}'
    )
    completed = 0
    try:
        raw = json.loads(run(["docker", "inspect", "--format", inspect_template, identifier]))
        if isinstance(raw, dict):
            details.update(
                {
                    "state": raw.get("status") if raw.get("status") in STATE_VALUES else "unknown",
                    "health": raw.get("health") if raw.get("health") in HEALTH_VALUES else "unknown",
                    "oom_killed": raw.get("oom_killed") is True,
                    "restart_count": _bounded_int(raw.get("restart_count"), 0, 2**31 - 1),
                    "exit_code": _bounded_int(raw.get("exit_code"), -2**31, 2**31 - 1),
                }
            )
            completed += 1
    except (RuntimeError, json.JSONDecodeError, TypeError, ValueError):
        details["inspect_status"] = "unavailable"
    try:
        stats = json.loads(
            run(["docker", "stats", "--no-stream", "--format", "{{json .}}", identifier])
        )
        if isinstance(stats, dict):
            resources: dict[str, Any] = {
                "pids": _bounded_int(stats.get("PIDs"), 0, 10_000_000),
            }
            for key, source in (("cpu_percent", "CPUPerc"), ("memory_percent", "MemPerc")):
                parsed_percent = _safe_percent(stats.get(source))
                if parsed_percent is not None:
                    resources[key] = parsed_percent
            for key, source in (("memory_usage", "MemUsage"), ("network_io", "NetIO"), ("block_io", "BlockIO")):
                parsed_pair = _size_pair(stats.get(source))
                if parsed_pair is not None:
                    resources[key] = parsed_pair
            details["resources"] = resources
            completed += 1
    except (RuntimeError, json.JSONDecodeError):
        details["resources"] = {"collector_status": "unavailable"}
    try:
        output = run(["docker", "top", identifier, "-eo", "pid,ppid,state,comm"])
        processes: list[dict[str, Any]] = []
        for line in output.splitlines()[1:101]:
            fields = line.split(None, 3)
            if len(fields) != 4 or not fields[0].isdigit() or not fields[1].isdigit():
                continue
            state = fields[2] if re.fullmatch(r"[A-Za-z<+]+", fields[2]) else "unknown"
            command = Path(fields[3]).name
            if command not in PROCESS_VALUES:
                continue
            pid = _bounded_int(fields[0], 1, 2**31 - 1, -1)
            ppid = _bounded_int(fields[1], 0, 2**31 - 1, -1)
            if pid < 1 or ppid < 0:
                continue
            processes.append(
                {
                    "pid": pid,
                    "ppid": ppid,
                    "state": state,
                    "comm": command,
                }
            )
        details["processes"] = processes
        completed += 1
    except RuntimeError:
        details["processes"] = []
    try:
        output = run(["docker", "port", identifier])
        ports: list[dict[str, Any]] = []
        for line in output.splitlines()[:128]:
            match = re.match(r"^(\d+)/(tcp|udp)\s+->\s+.*:(\d+)$", line.strip())
            container_port = _bounded_int(match.group(1), 1, 65535, -1) if match else -1
            host_port = _bounded_int(match.group(3), 1, 65535, -1) if match else -1
            if match and container_port > 0 and host_port > 0:
                ports.append(
                    {
                        "container_port": container_port,
                        "protocol": match.group(2),
                        "host_port": host_port,
                    }
                )
        details["ports"] = ports
        completed += 1
    except RuntimeError:
        details["ports"] = []
    details["collector_status"] = "ok" if completed == 4 else "partial"
    return details


def _backup_summary(backup_dir: Path | None) -> dict[str, Any]:
    if backup_dir is None:
        return {"collector_status": "skipped", "count": 0}
    if backup_dir.is_symlink() or not backup_dir.is_dir():
        return {"collector_status": "unavailable", "count": 0}
    count = 0
    total_size = 0
    newest: float | None = None
    truncated = False
    try:
        with os.scandir(backup_dir) as entries:
            entries_scanned = 0
            for entry in entries:
                entries_scanned += 1
                if entries_scanned > 10_000:
                    truncated = True
                    break
                if not entry.name.startswith("conan-") or not entry.name.endswith("-world.tar.gz"):
                    continue
                try:
                    details = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISREG(details.st_mode):
                    continue
                count += 1
                total_size += details.st_size
                newest = details.st_mtime if newest is None else max(newest, details.st_mtime)
    except OSError:
        return {"collector_status": "unavailable", "count": 0}
    if not count or newest is None:
        return {
            "collector_status": "partial" if truncated else "ok",
            "count": 0,
            "total_size_bytes": 0,
        }
    return {
        "collector_status": "partial" if truncated else "ok",
        "count": count,
        "total_size_bytes": total_size,
        "newest_age_seconds": max(0, int(datetime.now(timezone.utc).timestamp() - newest)),
    }


def _a2s_summary(ports: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = sorted(
        {
            int(port["host_port"])
            for port in ports
            if port.get("protocol") == "udp"
            and isinstance(port.get("host_port"), int)
            and 1 <= int(port["host_port"]) <= 65535
        }
    )
    if not candidates:
        return {"available": False, "reason": "no-published-udp-port"}
    helper = Path(__file__).resolve().parent / "native" / "a2s-info.py"
    specification = importlib.util.spec_from_file_location("conan_a2s_info", helper)
    if specification is None or specification.loader is None:
        return {"available": False, "reason": "probe-helper-unavailable"}
    module = importlib.util.module_from_spec(specification)
    try:
        specification.loader.exec_module(module)
    except (ImportError, OSError):
        return {"available": False, "reason": "probe-helper-unavailable"}
    attempted = candidates[:4]
    for port in attempted:
        try:
            info = module.query_counts("127.0.0.1", port, 0.75)
        except (OSError, RuntimeError, ValueError):
            continue
        return {
            "available": True,
            "reachable": True,
            "published_port": port,
            "players": _bounded_int(info.get("players"), 0, 1_000_000),
            "max_players": _bounded_int(info.get("max_players"), 0, 1_000_000),
        }
    return {"available": True, "reachable": False, "attempted_ports": len(attempted)}


def collect_report(
    *,
    run: Run = default_run,
    environ: Mapping[str, str] | None = None,
    backup_dir: Path | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    del environ  # Environment values are intentionally never collected.
    version_path = ROOT / "VERSION"
    project_version = version_path.read_text(encoding="utf-8").strip() if version_path.is_file() else "unknown"
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "project_version": project_version,
        "host": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "memory": _memory_summary(),
            "cpu_capabilities": _cpu_capabilities(),
            "project_disk": {
                "total": shutil.disk_usage(ROOT).total,
                "free": shutil.disk_usage(ROOT).free,
            },
        },
        "docker": {"available": False},
        "runtime": {"name": "unknown", "containers": []},
        "backups": _backup_summary(backup_dir),
        "checks": [],
        "privacy": {
            "environment_collected": False,
            "process_arguments_collected": False,
            "logs_collected": False,
            "world_data_collected": False,
        },
    }
    if offline:
        report["docker"] = {"available": False, "reason": "offline-requested"}
        report["checks"].append("Docker collection was disabled by --offline")
        return report
    try:
        version_output = run(["docker", "version", "--format", "{{json .}}"])
        compose_output = run(["docker", "compose", "version"])
        report["docker"] = _version_summary(version_output, compose_output)
        ps_output = run(
            [
                "docker",
                "ps",
                "--filter",
                "label=com.balnaimi.conan.runtime",
                "--format",
                '{"id":"{{.ID}}","runtime":"{{.Label "com.balnaimi.conan.runtime"}}"}',
            ]
        )
        runtimes: set[str] = set()
        containers: list[dict[str, Any]] = []
        for line in ps_output.splitlines()[:8]:
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                report["checks"].append("Docker returned one malformed container row")
                continue
            if not isinstance(raw, dict):
                continue
            safe, runtime = _safe_container(raw)
            if safe["id"]:
                safe.update(_container_details(run, safe["id"]))
                if run is default_run:
                    ports = safe.get("ports", [])
                    safe["a2s"] = _a2s_summary(ports if isinstance(ports, list) else [])
            containers.append(safe)
            if runtime:
                runtimes.add(runtime)
        if runtimes == {"wine"}:
            runtime_name = "wine"
        elif runtimes == {"native-linux"}:
            runtime_name = "native"
        elif len(runtimes) > 1:
            runtime_name = "ambiguous"
            report["checks"].append("Both Wine and Native containers are running")
        else:
            runtime_name = "unknown"
            report["checks"].append("No labeled running Conan container was found")
        report["runtime"] = {"name": runtime_name, "containers": containers}
    except (FileNotFoundError, RuntimeError, subprocess.SubprocessError) as exc:
        report["docker"] = {"available": False, "reason": type(exc).__name__}
        report["checks"].append("Docker diagnostics are unavailable; host checks were still collected")
    return report


def _open_secure_directory(path: Path) -> int:
    if path.is_absolute():
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        parts = path.parts[1:]
    else:
        descriptor = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
        parts = path.parts
    try:
        for part in parts:
            if part in {"", "."}:
                continue
            if part == "..":
                raise RuntimeError("Diagnostic output path must not contain '..'")
            try:
                child = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                os.mkdir(part, 0o700, dir_fd=descriptor)
                child = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
            except OSError as exc:
                raise RuntimeError(f"Unsafe diagnostic output directory component: {part}") from exc
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def write_report(path: Path, report: Mapping[str, Any]) -> None:
    os.umask(0o077)
    if path.name in {"", ".", ".."}:
        raise RuntimeError("Diagnostic output filename is invalid")
    parent_fd = _open_secure_directory(path.parent)
    temporary_name = f".{path.name}.{os.urandom(8).hex()}.tmp"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            json.dump(report, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        try:
            existing = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            if not __import__("stat").S_ISREG(existing.st_mode):
                raise RuntimeError(f"Refusing unsafe diagnostic output target: {path}")
        except FileNotFoundError:
            pass
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        os.chmod(path.name, 0o600, dir_fd=parent_fd, follow_symlinks=False)
        os.fsync(parent_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.close(parent_fd)


def _text_report(report: Mapping[str, Any]) -> str:
    return "\n".join(
        (
            f"Conan doctor schema: {report['schema_version']}",
            f"Project version: {report['project_version']}",
            f"Docker available: {report['docker']['available']}",
            f"Runtime: {report['runtime']['name']}",
            "Privacy: environment, process arguments, logs, and world data were not collected.",
            *[f"Check: {item}" for item in report["checks"]],
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a secret-minimized Conan diagnostic report")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--backup-dir", type=Path, default=ROOT / ".conan-backups")
    parser.add_argument("--offline", action="store_true", help="skip every Docker command")
    args = parser.parse_args()
    report = collect_report(environ=os.environ, backup_dir=args.backup_dir, offline=args.offline)
    if args.output:
        write_report(args.output, report)
        print(f"Diagnostic report written with owner-only permissions: {args.output}")
    elif args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_text_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
