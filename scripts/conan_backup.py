#!/usr/bin/env python3
"""Verified world archives and unified Compose backup/restore orchestration."""

from __future__ import annotations

import argparse
import fcntl
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager
import time
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

MAX_ARCHIVE_BYTES = 20 * 1024**3
MAX_EXPANDED_BYTES = 20 * 1024**3
MAX_MEMBERS = 10_000
MAX_METADATA_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 4 * 1024**2
WORKSPACE_SAFETY_BYTES = 64 * 1024 * 1024
TOOL_PATH = Path(__file__).resolve()
HELPER_IMAGE = "python@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba"


class BackupError(RuntimeError):
    pass


class RuntimeLayout(NamedTuple):
    project_name: str
    runtime: str
    service: str
    save_volume: str
    helper_image: str
    query_port: int | None = None
    save_volume_key: str | None = None


def _published_query_port(service: dict[str, Any]) -> int | None:
    environment = service.get("environment", {})
    query_target = 27015
    if isinstance(environment, dict):
        try:
            query_target = int(environment.get("QUERY_PORT", query_target))
        except (TypeError, ValueError):
            return None
    for port in service.get("ports", []):
        if not isinstance(port, dict):
            continue
        target_value = port.get("target")
        published_value = port.get("published")
        if target_value is None or published_value is None:
            continue
        try:
            target = int(target_value)
            published = int(published_value)
        except (TypeError, ValueError):
            continue
        if target == query_target and port.get("protocol", "tcp") == "udp" and 1 <= published <= 65535:
            return published
    return None


def _runtime_service(config: dict[str, Any], label_value: str) -> tuple[str, dict[str, Any]]:
    matches = [
        (name, service)
        for name, service in config.get("services", {}).items()
        if service.get("labels", {}).get("com.balnaimi.conan.runtime") == label_value
    ]
    if len(matches) != 1:
        raise BackupError(
            "Expected exactly one Compose service labelled "
            f"com.balnaimi.conan.runtime={label_value!r}"
        )
    return matches[0]


def _named_volume_at(config: dict[str, Any], service: dict[str, Any], target: str) -> tuple[str, str]:
    matches = [mount for mount in service.get("volumes", []) if mount.get("target") == target]
    if len(matches) != 1:
        raise BackupError(f"Expected exactly one save mount at {target}")
    mount = matches[0]
    if mount.get("type") != "volume":
        raise BackupError(f"Save mount at {target} must be a Compose named volume")
    source = mount.get("source")
    definition = config.get("volumes", {}).get(source)
    if not isinstance(source, str) or not isinstance(definition, dict):
        raise BackupError(f"Compose did not resolve the save volume at {target}")
    engine_name = definition.get("name")
    if (
        not isinstance(engine_name, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", engine_name)
    ):
        raise BackupError(f"Compose did not resolve the engine volume name for {source!r}")
    return engine_name, source


def discover_runtime_layout(
    wine_config: dict[str, Any], native_config: dict[str, Any], runtime: str
) -> RuntimeLayout:
    if runtime not in {"wine", "native"}:
        raise BackupError("runtime must be wine or native")
    wine_project = wine_config.get("name")
    native_project = native_config.get("name")
    if not isinstance(wine_project, str) or not wine_project or wine_project != native_project:
        raise BackupError("Wine and Native Compose files must resolve to the same project name")
    wine_name, wine_service = _runtime_service(wine_config, "wine")
    native_name, native_service = _runtime_service(native_config, "native-linux")
    wine_volume, wine_volume_key = _named_volume_at(
        wine_config, wine_service, "/conanexiles/ConanSandbox/Saved"
    )
    native_volume, native_volume_key = _named_volume_at(
        native_config, native_service, "/data/server/ConanSandbox/Saved"
    )
    if wine_volume == native_volume:
        raise BackupError("Wine and Native save volumes must resolve to distinct Docker volumes")
    if runtime == "wine":
        service_name, service, save_volume, save_volume_key = (
            wine_name,
            wine_service,
            wine_volume,
            wine_volume_key,
        )
    else:
        service_name, service, save_volume, save_volume_key = (
            native_name,
            native_service,
            native_volume,
            native_volume_key,
        )
    return RuntimeLayout(
        project_name=wine_project,
        runtime=runtime,
        service=service_name,
        save_volume=save_volume,
        helper_image=HELPER_IMAGE,
        query_port=_published_query_port(service),
        save_volume_key=save_volume_key,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_snapshot(source_path: Path, destination_path: Path) -> None:
    if source_path.is_symlink() or not source_path.is_file():
        raise BackupError(f"World database is missing or unsafe: {source_path}")
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=30)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise BackupError(f"SQLite snapshot integrity failed: {result}")
    except sqlite3.Error as exc:
        raise BackupError(f"SQLite snapshot failed: {exc}") from exc
    finally:
        destination.close()
        source.close()
    destination_path.chmod(0o600)


def _safe_archive_member(member: tarfile.TarInfo) -> PurePosixPath:
    path = PurePosixPath(member.name)
    if not member.name or path.is_absolute() or ".." in path.parts:
        raise BackupError(f"Unsafe archive path: {member.name}")
    if not (member.isfile() or member.isdir()) or member.issym() or member.islnk() or member.isdev():
        raise BackupError(f"Unsafe archive member type: {member.name}")
    return path


def _write_checksums(stage: Path) -> None:
    rows: list[str] = []
    for path in sorted(stage.rglob("*")):
        if path.is_file() and path.name != "checksums.sha256":
            rows.append(f"{sha256_file(path)}  {path.relative_to(stage).as_posix()}")
    manifest = stage / "checksums.sha256"
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
    manifest.chmod(0o600)


def create_world_archive(
    source_db: Path,
    backup_dir: Path,
    *,
    runtime: str,
    owner_uid: int | None = None,
    owner_gid: int | None = None,
) -> Path:
    if runtime not in {"wine", "native"}:
        raise BackupError("runtime must be wine or native")
    if (owner_uid is None) != (owner_gid is None):
        raise BackupError("Backup owner UID and GID must be supplied together")
    if owner_uid is not None and (owner_uid < 0 or owner_gid is None or owner_gid < 0):
        raise BackupError("Backup owner UID and GID must be non-negative")
    os.umask(0o077)
    if backup_dir.is_symlink():
        raise BackupError(f"Backup directory must not be a symlink: {backup_dir}")
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup_dir.chmod(0o700)
    if not backup_dir.is_dir():
        raise BackupError(f"Backup destination is not a directory: {backup_dir}")
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    final = backup_dir / f"conan-{runtime}-{timestamp}-{secrets.token_hex(4)}-world.tar.gz"
    fd, temporary_name = tempfile.mkstemp(prefix=".conan-backup-", suffix=".tmp", dir=backup_dir)
    os.close(fd)
    temporary = Path(temporary_name)
    temporary.chmod(0o600)
    try:
        with tempfile.TemporaryDirectory(prefix=".conan-stage-", dir=backup_dir) as stage_name:
            stage = Path(stage_name)
            world = stage / "world" / "game_0.db"
            world.parent.mkdir(parents=True)
            _sqlite_snapshot(source_db, world)
            metadata = {
                "format_version": 2,
                "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "runtime": runtime,
                "world_database": "game_0.db",
            }
            metadata_path = stage / "metadata.json"
            metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            metadata_path.chmod(0o600)
            _write_checksums(stage)
            with tarfile.open(temporary, "w:gz", format=tarfile.PAX_FORMAT) as bundle:
                for source in sorted(stage.rglob("*")):
                    if source.is_file():
                        bundle.add(source, arcname=source.relative_to(stage).as_posix(), recursive=False)
        archive_fd = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(archive_fd)
        finally:
            os.close(archive_fd)
        if owner_uid is not None and owner_gid is not None:
            os.chown(temporary, owner_uid, owner_gid)
        if final.exists() or final.is_symlink():
            raise BackupError(f"Refusing to replace existing backup: {final}")
        os.replace(temporary, final)
        final.chmod(0o600)
        directory_fd = os.open(backup_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return final
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, BackupError):
            raise
        raise BackupError(f"Could not create backup: {exc}") from exc


class Docker:
    def run(
        self,
        command: list[str],
        *,
        check: bool = True,
        capture: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            check=False,
        )
        if check and result.returncode != 0:
            raise BackupError((result.stderr or result.stdout or "Docker command failed").strip())
        return result


def _safe_mount_source(path: Path, description: str) -> str:
    candidate = Path(os.path.abspath(path))
    for component in (*reversed(candidate.parents), candidate):
        if component.is_symlink():
            raise BackupError(f"{description} path must not contain symlinks: {component}")
    try:
        resolved = str(candidate.resolve(strict=True))
    except OSError as exc:
        raise BackupError(f"{description} does not exist or cannot be resolved safely: {candidate}") from exc
    if "," in resolved:
        raise BackupError(f"{description} contains a comma and cannot be mounted safely")
    return resolved


def _helper_security_args() -> list[str]:
    return [
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "DAC_OVERRIDE",
        "--cap-add",
        "CHOWN",
        "--cap-add",
        "FOWNER",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--pids-limit",
        "64",
        "--memory",
        "5g",
        "--mount",
        "type=volume,dst=/tmp,volume-nocopy",
    ]


def create_compose_backup(docker: Any, layout: RuntimeLayout, backup_dir: Path) -> Path:
    os.umask(0o077)
    if backup_dir.is_symlink():
        raise BackupError(f"Backup directory must not be a symlink: {backup_dir}")
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup_dir.chmod(0o700)
    host_backup = _safe_mount_source(backup_dir, "Backup directory")
    host_tool = _safe_mount_source(TOOL_PATH, "Backup tool")
    command = [
        "docker",
        "run",
        "--rm",
        *_helper_security_args(),
        "--user",
        "0:0",
        "--entrypoint",
        "python3",
        "--mount",
        f"type=volume,src={layout.save_volume},dst=/source,readonly",
        "--mount",
        f"type=bind,src={host_backup},dst=/backups",
        "--mount",
        f"type=bind,src={host_tool},dst=/conan_backup.py,readonly",
        layout.helper_image,
        "/conan_backup.py",
        "archive-create",
        "--database",
        "/source/game_0.db",
        "--backup-dir",
        "/backups",
        "--runtime",
        layout.runtime,
        "--owner-uid",
        str(os.getuid()),
        "--owner-gid",
        str(os.getgid()),
    ]
    result = docker.run(command)
    output = (result.stdout or "").strip().splitlines()
    if not output:
        raise BackupError("Backup helper did not report an archive path")
    container_path = PurePosixPath(output[-1])
    if container_path.parent != PurePosixPath("/backups") or container_path.name != Path(container_path.name).name:
        raise BackupError("Backup helper returned an unsafe archive path")
    archive = backup_dir / container_path.name
    if archive.is_symlink() or not archive.is_file():
        raise BackupError("Backup helper did not create the reported archive")
    verified = verify_archive(archive)
    if verified.get("runtime") != layout.runtime:
        raise BackupError("Backup helper created an archive for the wrong runtime")
    return archive


def run_compose_restore(
    docker: Any,
    layout: RuntimeLayout,
    archive: Path,
    *,
    apply: bool,
    expected_archive_sha256: str | None = None,
) -> dict[str, Any]:
    if archive.is_symlink() or not archive.is_file():
        raise BackupError(f"Backup archive is missing or unsafe: {archive}")
    host_archive = _safe_mount_source(archive, "Backup archive")
    host_tool = _safe_mount_source(TOOL_PATH, "Backup tool")
    target_mount = f"type=volume,src={layout.save_volume},dst=/target"
    if not apply:
        target_mount += ",readonly"
    command = [
        "docker",
        "run",
        "--rm",
        *_helper_security_args(),
        "--user",
        "0:0",
        "--entrypoint",
        "python3",
        "--mount",
        target_mount,
        "--mount",
        f"type=bind,src={host_archive},dst=/archive.tar.gz,readonly",
        "--mount",
        f"type=bind,src={host_tool},dst=/conan_backup.py,readonly",
        layout.helper_image,
        "/conan_backup.py",
        "archive-restore",
        "/archive.tar.gz",
        "--target",
        "/target/game_0.db",
        "--target-uid",
        "1000" if layout.runtime == "native" else "0",
        "--target-gid",
        "1000" if layout.runtime == "native" else "0",
    ]
    if expected_archive_sha256 is not None:
        command.extend(["--expected-archive-sha256", expected_archive_sha256])
    if apply:
        command.append("--apply")
    result = docker.run(command)
    try:
        parsed = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise BackupError(f"Restore helper returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise BackupError("Restore helper returned a non-object result")
    return parsed


def compose_command(
    project_directory: Path,
    project_name: str | None,
    compose_file: Path,
    *tail: str,
) -> list[str]:
    command = ["docker", "compose", "--project-directory", str(project_directory)]
    if project_name:
        command.extend(["--project-name", project_name])
    command.extend(["-f", str(compose_file), *tail])
    return command


def volume_exists(
    docker: Any,
    volume: str,
    expected_project: str | None = None,
    expected_volume_key: str | None = None,
) -> bool:
    result = docker.run(["docker", "volume", "inspect", volume], check=False)
    if result.returncode == 0:
        if expected_project is None and expected_volume_key is None:
            return True
        try:
            rows = json.loads(result.stdout)
            labels = rows[0].get("Labels") if isinstance(rows, list) and rows else None
        except (json.JSONDecodeError, AttributeError, TypeError):
            labels = None
        if not isinstance(labels, dict):
            raise BackupError(f"Save volume {volume} has no trusted Compose identity labels")
        if (
            labels.get("com.docker.compose.project") != expected_project
            or labels.get("com.docker.compose.volume") != expected_volume_key
        ):
            raise BackupError(f"Save volume {volume} does not belong to the selected Compose project")
        return True
    combined = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    if "no such volume" in combined or "not found" in combined:
        return False
    raise BackupError((result.stderr or result.stdout or f"Cannot inspect volume {volume}").strip())


def service_activity(
    docker: Any,
    layout: RuntimeLayout,
    *,
    compose_file: Path,
    project_directory: Path,
    project_name: str | None,
) -> tuple[bool, list[str]]:
    result = docker.run(
        compose_command(
            project_directory,
            project_name,
            compose_file,
            "ps",
            "--all",
            "-q",
            layout.service,
        )
    )
    running = False
    unexpected: list[str] = []
    for container_id in (line.strip() for line in (result.stdout or "").splitlines() if line.strip()):
        inspected = docker.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", container_id]
        )
        status = (inspected.stdout or "").strip()
        if status == "running":
            running = True
        elif status not in {"created", "exited", "dead"}:
            unexpected.append(f"{container_id[:12]}:{status or 'unknown'}")
    return running, unexpected


def _source_users(docker: Any, volume: str) -> list[str]:
    result = docker.run(
        ["docker", "ps", "--filter", f"volume={volume}", "--format", "{{.ID}}"]
    )
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def stop_and_prove(
    docker: Any,
    layout: RuntimeLayout,
    *,
    compose_file: Path,
    project_directory: Path,
    project_name: str | None,
) -> None:
    docker.run(
        compose_command(
            project_directory,
            project_name,
            compose_file,
            "stop",
            layout.service,
        ),
        capture=False,
    )
    users = _source_users(docker, layout.save_volume)
    if users:
        raise BackupError(
            f"Save volume {layout.save_volume} is still used by container(s): "
            + ", ".join(identifier[:12] for identifier in users)
        )
    running, unexpected = service_activity(
        docker,
        layout,
        compose_file=compose_file,
        project_directory=project_directory,
        project_name=project_name,
    )
    if running or unexpected:
        raise BackupError("Could not prove the Conan service is quiescent")


def start_and_verify(
    docker: Any,
    layout: RuntimeLayout,
    *,
    compose_file: Path,
    project_directory: Path,
    project_name: str | None,
    timeout: int = 300,
) -> None:
    docker.run(
        compose_command(
            project_directory,
            project_name,
            compose_file,
            "up",
            "-d",
            layout.service,
        ),
        capture=False,
    )
    deadline = time.monotonic() + timeout
    last_detail = "container not found"
    while time.monotonic() < deadline:
        result = docker.run(
            compose_command(
                project_directory,
                project_name,
                compose_file,
                "ps",
                "--status",
                "running",
                "-q",
                layout.service,
            )
        )
        identifiers = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        if identifiers:
            if layout.runtime == "wine":
                if layout.query_port is not None:
                    probe = subprocess.run(
                        [
                            sys.executable,
                            str(Path(__file__).resolve().parent / "native" / "a2s-info.py"),
                            "127.0.0.1",
                            str(layout.query_port),
                            "--timeout",
                            "2",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                    if probe.returncode == 0:
                        return
                    last_detail = f"Wine A2S is not ready on UDP {layout.query_port}"
                else:
                    # Custom Compose files may not publish A2S. In that case only an
                    # immediate-crash observation is possible.
                    time.sleep(10)
                    confirm = docker.run(
                        ["docker", "inspect", "--format", "{{.State.Status}}", identifiers[0]]
                    )
                    if (confirm.stdout or "").strip() == "running":
                        return
                    last_detail = "Wine container did not remain running"
            else:
                health = docker.run(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
                        identifiers[0],
                    ]
                )
                status = (health.stdout or "").strip()
                if status == "healthy":
                    return
                if status == "unhealthy":
                    raise BackupError("Native container became unhealthy")
                last_detail = f"Native health is {status or 'unknown'}"
        time.sleep(2)
    raise BackupError(f"Service readiness timed out: {last_detail}")


def stop_with_restart_on_failure(
    docker: Any,
    layout: RuntimeLayout,
    *,
    compose_file: Path,
    project_directory: Path,
    project_name: str | None,
) -> None:
    try:
        stop_and_prove(
            docker,
            layout,
            compose_file=compose_file,
            project_directory=project_directory,
            project_name=project_name,
        )
    except Exception as original:
        try:
            start_and_verify(
                docker,
                layout,
                compose_file=compose_file,
                project_directory=project_directory,
                project_name=project_name,
            )
        except Exception as restart_error:
            raise BackupError(
                f"Could not prove the service stopped and could not restore its running state: {restart_error}"
            ) from original
        raise


def _open_private_directory(path: Path, *, create: bool) -> int | None:
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
                raise BackupError("Private state path must not contain '..'")
            try:
                child = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                if not create:
                    os.close(descriptor)
                    return None
                os.mkdir(part, 0o700, dir_fd=descriptor)
                child = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
            except OSError as exc:
                raise BackupError(f"Private state path has an unsafe component: {part}") from exc
            os.close(descriptor)
            descriptor = child
        if create:
            os.fchmod(descriptor, 0o700)
        return descriptor
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _prepare_private_directory(path: Path) -> None:
    descriptor = _open_private_directory(path, create=True)
    assert descriptor is not None
    os.close(descriptor)


@contextmanager
def operation_lock(backup_dir: Path):
    parent_fd = _open_private_directory(backup_dir, create=True)
    assert parent_fd is not None
    lock_fd = -1
    try:
        lock_fd = os.open(
            ".operation.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("Another Conan backup or restore operation is already running") from exc
        yield
    finally:
        if lock_fd >= 0:
            os.close(lock_fd)
        os.close(parent_fd)


def write_restore_state(path: Path, state: dict[str, Any]) -> None:
    os.umask(0o077)
    if path.is_symlink():
        raise BackupError(f"Restore state must not be a symlink: {path}")
    parent = path.parent
    parent_fd = _open_private_directory(parent, create=True)
    assert parent_fd is not None
    temporary_name = f".{path.name}.{secrets.token_hex(8)}.tmp"
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
            json.dump(state, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        try:
            existing = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            if stat.S_ISLNK(existing.st_mode):
                raise BackupError(f"Restore state became a symlink: {path}")
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
    except OSError as exc:
        raise BackupError(f"Cannot safely write restore state: {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.close(parent_fd)


def _read_restore_state(path: Path) -> dict[str, Any] | None:
    parent_fd = _open_private_directory(path.parent, create=False)
    if parent_fd is None:
        return None
    try:
        try:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        except FileNotFoundError:
            return None
        try:
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                state = json.load(stream)
        except (OSError, json.JSONDecodeError) as exc:
            raise BackupError(f"Restore state is unreadable: {exc}") from exc
    finally:
        os.close(parent_fd)
    if not isinstance(state, dict):
        raise BackupError("Restore state must be a JSON object")
    return state


def target_matches_archive(
    docker: Any,
    layout: RuntimeLayout,
    archive: Path,
    *,
    expected_archive_sha256: str | None = None,
) -> bool:
    if archive.is_symlink() or not archive.is_file():
        raise BackupError(f"Backup archive is missing or unsafe: {archive}")
    host_archive = _safe_mount_source(archive, "Backup archive")
    host_tool = _safe_mount_source(TOOL_PATH, "Backup tool")
    result = docker.run(
        [
            "docker",
            "run",
            "--rm",
            *_helper_security_args(),
            "--user",
            "0:0",
            "--entrypoint",
            "python3",
            "--mount",
            f"type=volume,src={layout.save_volume},dst=/target,readonly",
            "--mount",
            f"type=bind,src={host_archive},dst=/archive.tar.gz,readonly",
            "--mount",
            f"type=bind,src={host_tool},dst=/conan_backup.py,readonly",
            layout.helper_image,
            "/conan_backup.py",
            "archive-compare",
            "/archive.tar.gz",
            "--target",
            "/target/game_0.db",
            *(
                ["--expected-archive-sha256", expected_archive_sha256]
                if expected_archive_sha256 is not None
                else []
            ),
        ]
    )
    try:
        parsed = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise BackupError(f"Archive comparison returned invalid JSON: {exc}") from exc
    if expected_archive_sha256 is not None and parsed.get("archive_sha256") != expected_archive_sha256:
        raise BackupError("Backup archive changed after verification")
    return parsed.get("matches") is True


def create_offline_backup(
    docker: Any,
    layout: RuntimeLayout,
    backup_dir: Path,
    *,
    compose_file: Path,
    project_directory: Path,
    project_name: str | None,
) -> Path:
    state_path = backup_dir / "restore-state.json"
    previous_state = _read_restore_state(state_path)
    if previous_state is not None and previous_state.get("status") not in {"complete", "rolled-back"}:
        raise BackupError("An unfinished operation recovery state must be resolved before creating a backup")
    if not volume_exists(
        docker,
        layout.save_volume,
        layout.project_name,
        layout.save_volume_key,
    ):
        raise BackupError(f"Save volume does not exist: {layout.save_volume}")
    was_running, unexpected = service_activity(
        docker,
        layout,
        compose_file=compose_file,
        project_directory=project_directory,
        project_name=project_name,
    )
    if unexpected:
        raise BackupError("Refusing backup while service has unexpected state(s): " + ", ".join(unexpected))
    operation_state = {
        "schema_version": 1,
        "operation": "backup",
        "status": "stopping",
        "runtime": layout.runtime,
        "volume": layout.save_volume,
        "was_running": was_running,
    }
    if was_running:
        write_restore_state(state_path, operation_state)
        try:
            stop_with_restart_on_failure(
                docker,
                layout,
                compose_file=compose_file,
                project_directory=project_directory,
                project_name=project_name,
            )
        except Exception:
            write_restore_state(state_path, {**operation_state, "status": "rolled-back"})
            raise
    elif _source_users(docker, layout.save_volume):
        raise BackupError("Save volume is used by an unexpected running container")
    try:
        archive = create_compose_backup(docker, layout, backup_dir)
    except Exception as original:
        if was_running:
            try:
                start_and_verify(
                    docker,
                    layout,
                    compose_file=compose_file,
                    project_directory=project_directory,
                    project_name=project_name,
                )
                write_restore_state(state_path, {**operation_state, "status": "rolled-back"})
            except Exception as restart_error:
                raise BackupError(
                    f"Backup failed and the original service could not restart: {restart_error}"
                ) from original
        raise
    if was_running:
        start_and_verify(
            docker,
            layout,
            compose_file=compose_file,
            project_directory=project_directory,
            project_name=project_name,
        )
        write_restore_state(state_path, {**operation_state, "status": "complete"})
    return archive


def apply_compose_restore(
    docker: Any,
    layout: RuntimeLayout,
    archive: Path,
    backup_dir: Path,
    *,
    compose_file: Path,
    project_directory: Path,
    project_name: str | None,
) -> dict[str, Any]:
    details = verify_archive(archive)
    if details.get("runtime") != layout.runtime:
        raise BackupError(
            f"Archive runtime {details.get('runtime')!r} does not match selected runtime {layout.runtime!r}"
        )
    if not volume_exists(
        docker,
        layout.save_volume,
        layout.project_name,
        layout.save_volume_key,
    ):
        raise BackupError(f"Save volume does not exist: {layout.save_volume}")
    state_path = backup_dir / "restore-state.json"
    previous_state = _read_restore_state(state_path)
    if previous_state is not None and previous_state.get("status") not in {"complete", "rolled-back"}:
        raise BackupError("A previous restore requires recovery before another apply")
    expected_archive_sha256 = details.get("archive_sha256")
    if not isinstance(expected_archive_sha256, str):
        raise BackupError("Verified archive is missing its outer SHA-256 identity")
    was_running, unexpected = service_activity(
        docker,
        layout,
        compose_file=compose_file,
        project_directory=project_directory,
        project_name=project_name,
    )
    if unexpected:
        raise BackupError("Refusing restore while service has unexpected state(s): " + ", ".join(unexpected))
    stopping_state = {
        "schema_version": 1,
        "operation": "restore",
        "status": "stopping",
        "runtime": layout.runtime,
        "volume": layout.save_volume,
        "archive": archive.name,
        "was_running": was_running,
    }
    if was_running:
        write_restore_state(state_path, stopping_state)
        try:
            stop_with_restart_on_failure(
                docker,
                layout,
                compose_file=compose_file,
                project_directory=project_directory,
                project_name=project_name,
            )
        except Exception:
            write_restore_state(state_path, {**stopping_state, "status": "rolled-back"})
            raise
    elif _source_users(docker, layout.save_volume):
        raise BackupError("Save volume is used by an unexpected running container")
    try:
        already_applied = target_matches_archive(
            docker,
            layout,
            archive,
            expected_archive_sha256=expected_archive_sha256,
        )
    except Exception as original:
        if was_running:
            try:
                start_and_verify(
                    docker,
                    layout,
                    compose_file=compose_file,
                    project_directory=project_directory,
                    project_name=project_name,
                )
                write_restore_state(state_path, {**stopping_state, "status": "rolled-back"})
            except Exception as restart_error:
                write_restore_state(
                    state_path,
                    {
                        **stopping_state,
                        "status": "recovery-required",
                        "recovery_error": str(restart_error)[:512],
                    },
                )
                raise BackupError(
                    f"Restore comparison failed and the original service could not restart: {restart_error}"
                ) from original
        raise
    if already_applied:
        if was_running:
            start_and_verify(
                docker,
                layout,
                compose_file=compose_file,
                project_directory=project_directory,
                project_name=project_name,
            )
            write_restore_state(state_path, {**stopping_state, "status": "complete"})
        return {**details, "action": "already-applied"}
    try:
        previous = create_compose_backup(docker, layout, backup_dir)
        previous_details = verify_archive(previous)
        previous_sha256 = previous_details.get("archive_sha256")
        if not isinstance(previous_sha256, str):
            raise BackupError("Pre-restore archive is missing its outer SHA-256 identity")
    except Exception:
        if was_running:
            start_and_verify(
                docker,
                layout,
                compose_file=compose_file,
                project_directory=project_directory,
                project_name=project_name,
            )
            write_restore_state(state_path, {**stopping_state, "status": "rolled-back"})
        raise
    state = {
        "schema_version": 1,
        "status": "prepared",
        "runtime": layout.runtime,
        "volume": layout.save_volume,
        "archive": archive.name,
        "pre_restore_archive": previous.name,
        "was_running": was_running,
    }
    write_restore_state(state_path, state)
    try:
        result = run_compose_restore(
            docker,
            layout,
            archive,
            apply=True,
            expected_archive_sha256=expected_archive_sha256,
        )
        write_restore_state(state_path, {**state, "status": "applied"})
        if was_running:
            start_and_verify(
                docker,
                layout,
                compose_file=compose_file,
                project_directory=project_directory,
                project_name=project_name,
            )
        write_restore_state(state_path, {**state, "status": "complete"})
        return result
    except Exception as original:
        try:
            stop_and_prove(
                docker,
                layout,
                compose_file=compose_file,
                project_directory=project_directory,
                project_name=project_name,
            )
            run_compose_restore(
                docker,
                layout,
                previous,
                apply=True,
                expected_archive_sha256=previous_sha256,
            )
            if was_running:
                start_and_verify(
                    docker,
                    layout,
                    compose_file=compose_file,
                    project_directory=project_directory,
                    project_name=project_name,
                )
            write_restore_state(state_path, {**state, "status": "rolled-back"})
        except Exception as rollback_error:
            write_restore_state(
                state_path,
                {**state, "status": "recovery-required", "recovery_error": str(rollback_error)[:512]},
            )
            raise BackupError(
                "Restore failed and automatic rollback failed; recovery is required using "
                f"{previous.name}: {rollback_error}"
            ) from original
        raise BackupError(f"Restore failed and was rolled back safely: {original}") from original


def recover_compose_operation(
    docker: Any,
    layout: RuntimeLayout,
    backup_dir: Path,
    *,
    compose_file: Path,
    project_directory: Path,
    project_name: str | None,
) -> dict[str, Any]:
    state_path = backup_dir / "restore-state.json"
    if not volume_exists(
        docker,
        layout.save_volume,
        layout.project_name,
        layout.save_volume_key,
    ):
        raise BackupError(f"Save volume does not exist: {layout.save_volume}")
    state = _read_restore_state(state_path)
    if state is None or state.get("status") in {"complete", "rolled-back"}:
        return {"action": "no-recovery-required"}
    if state.get("runtime") != layout.runtime or state.get("volume") != layout.save_volume:
        raise BackupError("Recovery state does not match the selected runtime and save volume")
    was_running = state.get("was_running") is True
    status = state.get("status")
    if status == "stopping":
        if was_running:
            start_and_verify(
                docker,
                layout,
                compose_file=compose_file,
                project_directory=project_directory,
                project_name=project_name,
            )
        write_restore_state(state_path, {**state, "status": "rolled-back"})
        return {"action": "service-state-recovered"}
    if status not in {"prepared", "applied", "recovery-required"}:
        raise BackupError(f"Unsupported recovery state: {status!r}")
    archive_name = state.get("pre_restore_archive")
    if not isinstance(archive_name, str) or Path(archive_name).name != archive_name:
        raise BackupError("Recovery state has an unsafe pre-restore archive name")
    previous = backup_dir / archive_name
    details = verify_archive(previous)
    if details.get("runtime") != layout.runtime:
        raise BackupError("Recovery archive runtime does not match the selected runtime")
    running, unexpected = service_activity(
        docker,
        layout,
        compose_file=compose_file,
        project_directory=project_directory,
        project_name=project_name,
    )
    if unexpected:
        raise BackupError("Refusing recovery while service has unexpected state(s): " + ", ".join(unexpected))
    if running:
        stop_with_restart_on_failure(
            docker,
            layout,
            compose_file=compose_file,
            project_directory=project_directory,
            project_name=project_name,
        )
    elif _source_users(docker, layout.save_volume):
        raise BackupError("Save volume is used by an unexpected running container")
    previous_sha256 = details.get("archive_sha256")
    if not isinstance(previous_sha256, str):
        raise BackupError("Recovery archive is missing its outer SHA-256 identity")
    run_compose_restore(
        docker,
        layout,
        previous,
        apply=True,
        expected_archive_sha256=previous_sha256,
    )
    if was_running:
        start_and_verify(
            docker,
            layout,
            compose_file=compose_file,
            project_directory=project_directory,
            project_name=project_name,
        )
    write_restore_state(state_path, {**state, "status": "rolled-back"})
    return {"action": "restore-rolled-back", "archive": archive_name}


def _extract_verified(
    archive: Path,
    destination: Path,
    *,
    expected_archive_sha256: str | None = None,
) -> dict[str, Any]:
    if archive.is_symlink() or not archive.is_file():
        raise BackupError(f"Backup archive is missing or unsafe: {archive}")
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise BackupError("Backup archive exceeds the configured size limit")
    archive_sha256 = sha256_file(archive)
    if expected_archive_sha256 is not None and archive_sha256 != expected_archive_sha256:
        raise BackupError("Backup archive changed after verification")
    def validate_member(
        member: tarfile.TarInfo,
        normalized_seen: set[str],
        count: int,
        total_size: int,
    ) -> tuple[PurePosixPath, int, int]:
        count += 1
        if count > MAX_MEMBERS:
            raise BackupError("Backup archive has too many members")
        path = _safe_archive_member(member)
        normalized = path.as_posix()
        if normalized in normalized_seen:
            raise BackupError(f"Duplicate archive member: {member.name}")
        normalized_seen.add(normalized)
        if normalized == "metadata.json" and member.size > MAX_METADATA_BYTES:
            raise BackupError("Backup metadata exceeds the configured size limit")
        if normalized == "checksums.sha256" and member.size > MAX_MANIFEST_BYTES:
            raise BackupError("Backup checksum manifest exceeds the configured size limit")
        total_size += member.size
        if total_size > MAX_EXPANDED_BYTES:
            raise BackupError("Backup archive expansion exceeds the configured limit")
        return path, count, total_size

    expanded = 0
    try:
        # First streaming pass validates the complete header set and resource
        # budget without materializing every TarInfo object in memory.
        seen: set[str] = set()
        count = 0
        with tarfile.open(archive, "r|gz") as bundle:
            for member in bundle:
                _, count, expanded = validate_member(member, seen, count, expanded)
                getattr(bundle, "members").clear()
        free_bytes = shutil.disk_usage(destination).free
        if free_bytes < expanded + WORKSPACE_SAFETY_BYTES:
            raise BackupError(
                "Not enough free space in the verification workspace "
                f"(need at least {expanded + WORKSPACE_SAFETY_BYTES} bytes, have {free_bytes})"
            )
        if sha256_file(archive) != archive_sha256:
            raise BackupError("Backup archive changed during header verification")

        # Reopen for extraction and revalidate as data is streamed. The outer
        # digest is checked again afterward, so a path replacement or in-place
        # modification can never become a verified result.
        seen = set()
        count = 0
        extracted_size = 0
        with tarfile.open(archive, "r|gz") as bundle:
            for member in bundle:
                path, count, extracted_size = validate_member(
                    member,
                    seen,
                    count,
                    extracted_size,
                )
                target = destination.joinpath(*path.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    getattr(bundle, "members").clear()
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise BackupError(f"Could not read archive member: {member.name}")
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                target.chmod(0o600)
                getattr(bundle, "members").clear()
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise BackupError(f"Could not read backup archive: {exc}") from exc

    metadata_path = destination / "metadata.json"
    manifest = destination / "checksums.sha256"
    database = destination / "world" / "game_0.db"
    if not metadata_path.is_file() or not manifest.is_file() or not database.is_file():
        raise BackupError("Backup is missing metadata, checksums, or world/game_0.db")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise BackupError(f"Backup metadata is invalid: {exc}") from exc
    if metadata.get("format_version") not in {1, 2} or metadata.get("world_database") != "game_0.db":
        raise BackupError("Backup metadata contract is unsupported")
    runtime = metadata.get("runtime", "native" if metadata.get("format_version") == 1 else None)
    if runtime not in {"wine", "native"}:
        raise BackupError("Backup runtime metadata is invalid")

    try:
        manifest_lines = manifest.read_text(encoding="utf-8").splitlines()
    except UnicodeError as exc:
        raise BackupError(f"Backup checksum manifest is invalid: {exc}") from exc
    expected: set[str] = set()
    for number, line in enumerate(manifest_lines, 1):
        try:
            digest, relative = line.split("  ", 1)
        except ValueError as exc:
            raise BackupError(f"Malformed checksum line {number}") from exc
        path = PurePosixPath(relative)
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise BackupError(f"Invalid digest on checksum line {number}")
        if path.is_absolute() or ".." in path.parts or relative in expected:
            raise BackupError(f"Unsafe or duplicate checksum path: {relative}")
        source = destination.joinpath(*path.parts)
        if source.is_symlink() or not source.is_file() or sha256_file(source) != digest:
            raise BackupError(f"Checksum mismatch: {relative}")
        expected.add(relative)
    actual = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
    }
    if actual != expected:
        raise BackupError("Backup contains unchecksummed or missing files")
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise BackupError(f"Backup database could not be checked: {exc}") from exc
    if not integrity or integrity[0] != "ok":
        raise BackupError(f"Backup database integrity failed: {integrity}")
    if sha256_file(archive) != archive_sha256:
        raise BackupError("Backup archive changed while it was being verified")
    metadata["runtime"] = runtime
    metadata["archive_sha256"] = archive_sha256
    metadata["integrity"] = "ok"
    metadata["members"] = sorted(seen)
    return metadata


def verify_archive(archive: Path) -> dict[str, Any]:
    try:
        with tempfile.TemporaryDirectory(
            prefix=".conan-verify-",
            dir=archive.parent,
        ) as temporary:
            return _extract_verified(archive, Path(temporary))
    except OSError as exc:
        raise BackupError(f"Could not create archive verification workspace: {exc}") from exc


def list_backups(backup_dir: Path, *, verify: bool) -> list[dict[str, Any]]:
    if backup_dir.is_symlink() or not backup_dir.is_dir():
        raise BackupError(f"Backup directory is missing or unsafe: {backup_dir}")
    rows: list[dict[str, Any]] = []
    for archive in sorted(backup_dir.glob("conan-*-world.tar.gz"), reverse=True):
        if archive.is_symlink() or not archive.is_file():
            continue
        row: dict[str, Any] = {
            "name": archive.name,
            "size_bytes": archive.stat().st_size,
            "modified_utc": dt.datetime.fromtimestamp(
                archive.stat().st_mtime, tz=dt.timezone.utc
            ).isoformat(),
        }
        if verify:
            try:
                details = verify_archive(archive)
                row.update(
                    {
                        "verification": "ok",
                        "runtime": details["runtime"],
                        "created_utc": details.get("created_utc"),
                    }
                )
            except BackupError as exc:
                row.update({"verification": "failed", "error": str(exc)[:256]})
        rows.append(row)
    return rows


def compare_archive_target(
    archive: Path,
    target_db: Path,
    *,
    expected_archive_sha256: str | None = None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="conan-compare-") as temporary:
        stage = Path(temporary)
        details = _extract_verified(
            archive,
            stage,
            expected_archive_sha256=expected_archive_sha256,
        )
        if target_db.is_symlink():
            raise BackupError(f"Target database must not be a symlink: {target_db}")
        if not target_db.is_file():
            return {**details, "matches": False, "target": "missing"}
        target_snapshot = stage / "target-snapshot.db"
        _sqlite_snapshot(target_db, target_snapshot)
        source = stage / "world" / "game_0.db"
        return {
            **details,
            "matches": sha256_file(source) == sha256_file(target_snapshot),
            "target": "present",
        }


def _open_parent_no_symlinks(parent: Path) -> int:
    absolute = parent.absolute()
    parts = absolute.parts
    current = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
        return current
    except Exception:
        os.close(current)
        raise


def restore_world_archive(
    archive: Path,
    target_db: Path,
    *,
    apply: bool,
    target_uid: int | None = None,
    target_gid: int | None = None,
    expected_archive_sha256: str | None = None,
) -> dict[str, Any]:
    if (target_uid is None) != (target_gid is None):
        raise BackupError("Restore target UID and GID must be supplied together")
    if target_uid is not None and (target_uid < 0 or target_gid is None or target_gid < 0):
        raise BackupError("Restore target UID and GID must be non-negative")
    if target_db.name != "game_0.db":
        raise BackupError("Restore target must be named game_0.db")
    if target_db.is_symlink():
        raise BackupError(f"Restore target must not be a symlink: {target_db}")
    if not target_db.parent.is_dir():
        raise BackupError(f"Restore target directory does not exist: {target_db.parent}")
    with tempfile.TemporaryDirectory(prefix="conan-restore-") as temporary:
        stage = Path(temporary)
        details = _extract_verified(
            archive,
            stage,
            expected_archive_sha256=expected_archive_sha256,
        )
        if not apply:
            return {**details, "action": "dry-run"}
        source = stage / "world" / "game_0.db"
        try:
            parent_fd = _open_parent_no_symlinks(target_db.parent)
        except OSError as exc:
            raise BackupError(f"Restore target path is unsafe: {exc}") from exc
        temporary_name = f".{target_db.name}.restore-{secrets.token_hex(8)}"
        output_fd: int | None = None
        try:
            existing: os.stat_result | None = None
            target_exists = True
            try:
                existing = os.stat(target_db.name, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISREG(existing.st_mode):
                    raise BackupError("Existing restore target is not a regular file")
            except FileNotFoundError:
                target_exists = False
            if target_exists:
                assert existing is not None
                desired_uid = existing.st_uid
                desired_gid = existing.st_gid
                desired_mode = stat.S_IMODE(existing.st_mode) | 0o600
            else:
                desired_uid = os.getuid() if target_uid is None else target_uid
                desired_gid = os.getgid() if target_gid is None else target_gid
                desired_mode = 0o600
            for sidecar in (f"{target_db.name}-wal", f"{target_db.name}-shm"):
                try:
                    sidecar_stat = os.stat(sidecar, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(sidecar_stat.st_mode):
                    raise BackupError(f"Unsafe SQLite sidecar: {sidecar}")
            output_fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            os.fchown(output_fd, desired_uid, desired_gid)
            os.fchmod(output_fd, desired_mode)
            with os.fdopen(output_fd, "wb", closefd=True) as output, source.open("rb") as input_file:
                output_fd = None
                shutil.copyfileobj(input_file, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            if target_exists:
                target_via_fd = f"file:/proc/self/fd/{parent_fd}/{target_db.name}?mode=rw"
                try:
                    connection = sqlite3.connect(target_via_fd, uri=True, timeout=5)
                    try:
                        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    finally:
                        connection.close()
                except sqlite3.Error as exc:
                    raise BackupError(f"Existing world database could not be checkpointed safely: {exc}") from exc
                if checkpoint is not None and checkpoint[0] != 0:
                    raise BackupError("Existing world database WAL is busy; refusing restore")
                target_fd = os.open(target_db.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
                try:
                    os.fsync(target_fd)
                finally:
                    os.close(target_fd)
            for sidecar in (f"{target_db.name}-wal", f"{target_db.name}-shm"):
                try:
                    os.unlink(sidecar, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            os.fsync(parent_fd)
            os.replace(temporary_name, target_db.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            if output_fd is not None:
                os.close(output_fd)
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            os.close(parent_fd)
        return {**details, "action": "restored"}


def render_compose(
    docker: Any,
    *,
    project_directory: Path,
    project_name: str | None,
    compose_file: Path,
) -> dict[str, Any]:
    result = docker.run(
        compose_command(
            project_directory,
            project_name,
            compose_file,
            "config",
            "--format",
            "json",
        )
    )
    try:
        parsed = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise BackupError(f"Docker Compose returned invalid JSON for {compose_file}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise BackupError(f"Docker Compose returned a non-object for {compose_file}")
    return parsed


def _resolved_public_context(args: argparse.Namespace, docker: Any):
    project_directory = args.project_directory.resolve()
    wine_file = args.wine_compose_file
    native_file = args.native_compose_file
    if not wine_file.is_absolute():
        wine_file = project_directory / wine_file
    if not native_file.is_absolute():
        native_file = project_directory / native_file
    if not wine_file.is_file() or not native_file.is_file():
        raise BackupError("Run from a complete project checkout containing both Compose files")
    wine_config = render_compose(
        docker,
        project_directory=project_directory,
        project_name=args.project_name,
        compose_file=wine_file,
    )
    native_config = render_compose(
        docker,
        project_directory=project_directory,
        project_name=args.project_name,
        compose_file=native_file,
    )
    return project_directory, wine_file, native_file, wine_config, native_config


def _select_public_runtime(
    args: argparse.Namespace,
    docker: Any,
    wine_config: dict[str, Any],
    native_config: dict[str, Any],
    wine_file: Path,
    native_file: Path,
    project_directory: Path,
    *,
    archive_runtime: str | None = None,
) -> tuple[RuntimeLayout, Path]:
    requested = args.runtime
    if requested == "auto" and archive_runtime in {"wine", "native"}:
        requested = archive_runtime
    if requested in {"wine", "native"}:
        return (
            discover_runtime_layout(wine_config, native_config, requested),
            wine_file if requested == "wine" else native_file,
        )
    candidates: list[tuple[RuntimeLayout, Path]] = []
    for runtime, compose_file in (("wine", wine_file), ("native", native_file)):
        layout = discover_runtime_layout(wine_config, native_config, runtime)
        running, unexpected = service_activity(
            docker,
            layout,
            compose_file=compose_file,
            project_directory=project_directory,
            project_name=args.project_name,
        )
        if unexpected:
            raise BackupError(
                f"Cannot auto-detect runtime while {runtime} has unexpected state(s): "
                + ", ".join(unexpected)
            )
        if running:
            candidates.append((layout, compose_file))
    if len(candidates) != 1:
        raise BackupError("Runtime auto-detection requires exactly one running Wine or Native service; use --runtime")
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create, list, verify, and safely restore portable Conan world backups"
    )
    parser.add_argument("--runtime", choices=("auto", "wine", "native"), default="auto")
    parser.add_argument("--project-directory", type=Path, default=Path.cwd())
    parser.add_argument("--project-name")
    parser.add_argument("--wine-compose-file", type=Path, default=Path("docker-compose.yml"))
    parser.add_argument(
        "--native-compose-file", type=Path, default=Path("docker-compose.native.yml")
    )
    parser.add_argument("--backup-dir", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("create", help="stop safely, snapshot the world, and restore prior state")
    listing = subparsers.add_parser("list", help="list local portable world backups")
    listing.add_argument("--verify", action="store_true")
    verify = subparsers.add_parser("verify", help="verify checksums and SQLite integrity")
    verify.add_argument("archive", type=Path)
    restore = subparsers.add_parser("restore", help="plan by default; mutate only with --apply")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--apply", action="store_true")
    subparsers.add_parser("recover", help="recover an interrupted backup or restore safely")

    # Internal helper commands run inside the Native image with explicit mounts.
    internal_create = subparsers.add_parser("archive-create", help=argparse.SUPPRESS)
    internal_create.add_argument("--database", type=Path, required=True)
    internal_create.add_argument("--backup-dir", type=Path, required=True)
    internal_create.add_argument("--runtime", choices=("wine", "native"), required=True)
    internal_create.add_argument("--owner-uid", type=int)
    internal_create.add_argument("--owner-gid", type=int)
    compare = subparsers.add_parser("archive-compare", help=argparse.SUPPRESS)
    compare.add_argument("archive", type=Path)
    compare.add_argument("--target", type=Path, required=True)
    compare.add_argument("--expected-archive-sha256")
    internal_restore = subparsers.add_parser("archive-restore", help=argparse.SUPPRESS)
    internal_restore.add_argument("archive", type=Path)
    internal_restore.add_argument("--target", type=Path, required=True)
    internal_restore.add_argument("--apply", action="store_true")
    internal_restore.add_argument("--target-uid", type=int)
    internal_restore.add_argument("--target-gid", type=int)
    internal_restore.add_argument("--expected-archive-sha256")
    args = parser.parse_args()
    try:
        if args.command == "archive-create":
            print(
                create_world_archive(
                    args.database,
                    args.backup_dir,
                    runtime=args.runtime,
                    owner_uid=args.owner_uid,
                    owner_gid=args.owner_gid,
                )
            )
            return 0
        if args.command == "archive-compare":
            print(
                json.dumps(
                    compare_archive_target(
                        args.archive,
                        args.target,
                        expected_archive_sha256=args.expected_archive_sha256,
                    ),
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "archive-restore":
            print(
                json.dumps(
                    restore_world_archive(
                        args.archive,
                        args.target,
                        apply=args.apply,
                        target_uid=args.target_uid,
                        target_gid=args.target_gid,
                        expected_archive_sha256=args.expected_archive_sha256,
                    ),
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        project_directory = args.project_directory.resolve()
        backup_dir = args.backup_dir or (project_directory / ".conan-backups")
        if not backup_dir.is_absolute():
            backup_dir = project_directory / backup_dir
        recovery_state = None
        if args.command == "create" or (args.command == "restore" and args.apply):
            pending_state = _read_restore_state(backup_dir / "restore-state.json")
            if pending_state is not None and pending_state.get("status") not in {"complete", "rolled-back"}:
                raise BackupError(
                    "An unfinished operation recovery state must be resolved before another mutation"
                )
        if args.command == "recover":
            recovery_state = _read_restore_state(backup_dir / "restore-state.json")
            if recovery_state is None or recovery_state.get("status") in {"complete", "rolled-back"}:
                print(json.dumps({"action": "no-recovery-required"}, indent=2))
                return 0
        if args.command == "list":
            rows = [] if not backup_dir.exists() else list_backups(backup_dir, verify=args.verify)
            print(json.dumps(rows, indent=2, sort_keys=True))
            return 0
        if args.command == "verify":
            print(json.dumps(verify_archive(args.archive), indent=2, sort_keys=True))
            return 0

        docker = Docker()
        (
            project_directory,
            wine_file,
            native_file,
            wine_config,
            native_config,
        ) = _resolved_public_context(args, docker)
        archive_details = verify_archive(args.archive) if args.command == "restore" else None

        layout, compose_file = _select_public_runtime(
            args,
            docker,
            wine_config,
            native_config,
            wine_file,
            native_file,
            project_directory,
            archive_runtime=(
                archive_details.get("runtime")
                if archive_details
                else recovery_state.get("runtime") if recovery_state else None
            ),
        )
        if args.command == "create":
            with operation_lock(backup_dir):
                archive = create_offline_backup(
                    docker,
                    layout,
                    backup_dir,
                    compose_file=compose_file,
                    project_directory=project_directory,
                    project_name=args.project_name,
                )
            print(json.dumps({"action": "created", "archive": str(archive)}, indent=2))
        elif args.command == "recover":
            with operation_lock(backup_dir):
                result = recover_compose_operation(
                    docker,
                    layout,
                    backup_dir,
                    compose_file=compose_file,
                    project_directory=project_directory,
                    project_name=args.project_name,
                )
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.apply:
            with operation_lock(backup_dir):
                result = apply_compose_restore(
                    docker,
                    layout,
                    args.archive,
                    backup_dir,
                    compose_file=compose_file,
                    project_directory=project_directory,
                    project_name=args.project_name,
                )
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            if archive_details and archive_details.get("runtime") != layout.runtime:
                raise BackupError("Archive runtime does not match the selected target runtime")
            if not volume_exists(
                docker,
                layout.save_volume,
                layout.project_name,
                layout.save_volume_key,
            ):
                raise BackupError(f"Save volume does not exist: {layout.save_volume}")
            print(
                json.dumps(
                    run_compose_restore(
                        docker,
                        layout,
                        args.archive,
                        apply=False,
                        expected_archive_sha256=(
                            archive_details.get("archive_sha256") if archive_details else None
                        ),
                    ),
                    indent=2,
                    sort_keys=True,
                )
            )
        return 0
    except BackupError as exc:
        print(f"ERROR: {exc}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
