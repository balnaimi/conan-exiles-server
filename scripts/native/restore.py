#!/usr/bin/env python3
"""Verify and safely restore Conan Enhanced native backup archives."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, cast


DEFAULT_MAX_MEMBERS = 10_000
DEFAULT_MAX_BYTES = 20 * 1024**3
MAX_RESTORE_BYTES = 100 * 1024**3
MIN_FREE_RESERVE_BYTES = 1024**3


def safe_member(member: tarfile.TarInfo) -> None:
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts or not member.name:
        raise RuntimeError(f"Unsafe archive path: {member.name}")
    if member.issym() or member.islnk() or member.isdev():
        raise RuntimeError(f"Unsafe archive member type: {member.name}")
    if not (member.isfile() or member.isdir()):
        raise RuntimeError(f"Unsupported archive member type: {member.name}")


def restore_limit(name: str, default: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < 1 or value > maximum:
        raise RuntimeError(f"{name} must be between 1 and {maximum}")
    return value


def extract_safely(archive: Path, destination: Path) -> None:
    max_members = restore_limit("NATIVE_RESTORE_MAX_MEMBERS", DEFAULT_MAX_MEMBERS, 100_000)
    max_bytes = restore_limit("NATIVE_RESTORE_MAX_BYTES", DEFAULT_MAX_BYTES, MAX_RESTORE_BYTES)
    if archive.stat().st_size > max_bytes:
        raise RuntimeError("Backup archive exceeds the configured size limit")
    seen: set[str] = set()
    total_size = 0
    member_count = 0
    free_bytes = shutil.disk_usage(destination).free
    reserve_bytes = min(MIN_FREE_RESERVE_BYTES, free_bytes // 10)
    extraction_budget = max(0, free_bytes - reserve_bytes)
    with tarfile.open(archive, "r|gz") as bundle:
        for member in bundle:
            member_count += 1
            if member_count > max_members:
                raise RuntimeError("Backup archive exceeds the configured member limit")
            safe_member(member)
            if member.name in seen:
                raise RuntimeError(f"Duplicate archive member: {member.name}")
            seen.add(member.name)
            total_size += member.size
            if total_size > max_bytes:
                raise RuntimeError("Backup archive exceeds the configured expansion size limit")
            if total_size > extraction_budget:
                raise RuntimeError("Insufficient free space for safe backup extraction")
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise RuntimeError(f"Could not read archive member: {member.name}")
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            target.chmod(0o600)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_checksums(stage: Path) -> None:
    manifest = stage / "checksums.sha256"
    if not manifest.is_file():
        raise RuntimeError("Backup checksum manifest is missing")
    expected_paths: set[str] = set()
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            continue
        try:
            digest, relative = line.split("  ", 1)
        except ValueError as exc:
            raise RuntimeError(f"Malformed checksum line {number}") from exc
        path = PurePosixPath(relative)
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeError(f"Invalid SHA-256 digest on checksum line {number}")
        if relative in expected_paths:
            raise RuntimeError(f"Duplicate checksum path: {relative}")
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"Unsafe checksum path: {relative}")
        source = stage.joinpath(*path.parts)
        if not source.is_file() or source.is_symlink():
            raise RuntimeError(f"Checksummed file is missing or unsafe: {relative}")
        actual = sha256_file(source)
        if actual != digest:
            raise RuntimeError(f"Checksum mismatch: {relative}")
        expected_paths.add(relative)
    actual_paths = {
        path.relative_to(stage).as_posix()
        for path in stage.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
    }
    if actual_paths != expected_paths:
        raise RuntimeError("Backup contains unchecksummed or missing files")


def verify_database(stage: Path) -> None:
    database = stage / "world" / "game_0.db"
    if not database.is_file():
        raise RuntimeError("Backup world/game_0.db is missing")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    if not result or result[0] != "ok":
        raise RuntimeError(f"Backup database integrity failed: {result}")


def open_safe_directory(root: Path, parts: tuple[str, ...]) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        current = os.open(root, flags)
    except OSError as exc:
        raise RuntimeError(f"Restore target root is a symlink or not a directory: {root}") from exc
    try:
        for part in parts:
            if not part or part in {".", ".."} or "/" in part:
                raise RuntimeError(f"Unsafe restore destination component: {part}")
            try:
                os.mkdir(part, mode=0o700, dir_fd=current)
            except FileExistsError:
                pass
            try:
                child = os.open(part, flags, dir_fd=current)
            except OSError as exc:
                raise RuntimeError(
                    f"Restore target contains a symlink or non-directory component: {part}"
                ) from exc
            os.close(current)
            current = child
        return current
    except Exception:
        os.close(current)
        raise


def safe_target_relative(relative: Path) -> tuple[tuple[str, ...], str]:
    path = PurePosixPath(relative.as_posix())
    if path.is_absolute() or ".." in path.parts or len(path.parts) < 1:
        raise RuntimeError(f"Unsafe restore destination path: {relative}")
    return tuple(path.parts[:-1]), path.parts[-1]


def atomic_write_target(target: Path, relative: Path, *, source: Path | None = None, payload: bytes | None = None) -> None:
    if (source is None) == (payload is None):
        raise RuntimeError("Restore writer requires exactly one source")
    parent_parts, name = safe_target_relative(relative)
    parent_fd = open_safe_directory(target, parent_parts)
    temporary = f".{name}.restore-{secrets.token_hex(8)}"
    output_fd: int | None = None
    try:
        output_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        with os.fdopen(output_fd, "wb", closefd=True) as output:
            output_fd = None
            if source is not None:
                with source.open("rb") as input_file:
                    shutil.copyfileobj(input_file, output, length=1024 * 1024)
            else:
                assert payload is not None
                output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        if output_fd is not None:
            os.close(output_fd)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.close(parent_fd)


def ensure_restore_target_layout(target: Path) -> None:
    for parts in (
        (".runtime",),
        ("ConanSandbox", "Saved"),
        ("ConanSandbox", "Saved", "Config", "LinuxServer"),
        ("ConanSandbox", "Mods"),
    ):
        descriptor = open_safe_directory(target, parts)
        os.close(descriptor)


def process_is_active(pid_file: Path) -> bool:
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(pid_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise RuntimeError(f"cannot verify server PID file safely: {pid_file}") from exc
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            raise RuntimeError(f"cannot verify unsafe server PID file: {pid_file}")
        payload = os.read(descriptor, 64).decode("ascii", errors="strict").strip()
        pid = int(payload)
        if pid < 1:
            raise ValueError("PID must be positive")
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError(f"cannot verify server PID file: {pid_file}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError) as exc:
        raise RuntimeError(f"cannot verify whether server PID {pid} is active") from exc
    return True


def validate_metadata(stage: Path) -> dict[str, object]:
    metadata_file = stage / "metadata.json"
    if not metadata_file.is_file():
        raise RuntimeError("Backup metadata is missing")
    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise RuntimeError("Backup metadata must be an object")
    if type(metadata.get("format_version")) is not int or metadata["format_version"] != 1:
        raise RuntimeError("Unsupported backup metadata format version")
    for key in ("created_utc", "reason"):
        if not isinstance(metadata.get(key), str) or not metadata[key]:
            raise RuntimeError(f"Backup metadata field {key} must be a non-empty string")
    mode = metadata.get("mode")
    if mode not in {"light", "full"}:
        raise RuntimeError("Backup metadata mode must be light or full")
    if metadata.get("world_database") != "game_0.db":
        raise RuntimeError("Backup metadata world_database is invalid")
    if metadata.get("config_secrets_redacted") is not True:
        raise RuntimeError("Backup metadata must confirm config secret redaction")
    workshop_ids = metadata.get("workshop_ids")
    active_packages = metadata.get("active_packages")
    if not isinstance(workshop_ids, list) or any(
        not isinstance(item, str) or not item.isdigit() for item in workshop_ids
    ):
        raise RuntimeError("Backup metadata workshop_ids must be a list of numeric strings")
    if not isinstance(active_packages, list) or any(
        not isinstance(item, str)
        or Path(item).name != item
        or not item.endswith(".pak")
        for item in active_packages
    ):
        raise RuntimeError("Backup metadata active_packages must contain safe PAK names")
    if len(workshop_ids) != len(active_packages):
        raise RuntimeError("Backup metadata mod lists have different lengths")
    if len(set(active_packages)) != len(active_packages):
        raise RuntimeError("Backup metadata contains duplicate active package names")
    mods_dir = stage / "mods"
    pak_names = sorted(path.name for path in mods_dir.glob("*.pak")) if mods_dir.is_dir() else []
    expected_paks = sorted(active_packages) if mode == "full" else []
    if pak_names != expected_paks:
        raise RuntimeError("Backup metadata mode does not match archived PAK files")
    return metadata


def load_runtime_state_module() -> Any:
    state_tool = Path(__file__).with_name("runtime_state.py")
    spec = importlib.util.spec_from_file_location("conan_native_runtime_state", state_tool)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load secure runtime state helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_pre_restore_backup(target: Path) -> Path:
    backup_tool = Path(__file__).with_name("backup.py")
    spec = importlib.util.spec_from_file_location("conan_native_backup", backup_tool)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load pre-restore backup tool")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous = os.environ.get("GAME_DIR")
    os.environ["GAME_DIR"] = str(target)
    try:
        return cast(Path, module.create_backup("pre-restore", acquire_operation_lock=False))
    finally:
        if previous is None:
            os.environ.pop("GAME_DIR", None)
        else:
            os.environ["GAME_DIR"] = previous


def apply_restore(stage: Path, target: Path, metadata: dict[str, object]) -> None:
    ensure_restore_target_layout(target)
    pid_file = target / ".runtime" / "server.pid"
    if process_is_active(pid_file):
        raise RuntimeError("Refusing restore while the native server process is active")

    existing_db = target / "ConanSandbox" / "Saved" / "game_0.db"
    if existing_db.exists():
        create_pre_restore_backup(target)

    atomic_write_target(
        target,
        Path("ConanSandbox/Saved/game_0.db"),
        source=stage / "world" / "game_0.db",
    )
    config_source = stage / "config"
    if config_source.exists():
        for source in sorted(config_source.rglob("*")):
            if source.is_file():
                atomic_write_target(
                    target,
                    Path("ConanSandbox/Saved/Config/LinuxServer")
                    / source.relative_to(config_source),
                    source=source,
                )
    mods_source = stage / "mods"
    if mods_source.exists():
        for source in sorted(mods_source.rglob("*")):
            if source.is_file():
                atomic_write_target(
                    target,
                    Path("ConanSandbox/Mods") / source.relative_to(mods_source),
                    source=source,
                )

    workshop_ids = cast(list[str], metadata["workshop_ids"])
    marker = Path(".runtime/restore-required-workshop-ids")
    atomic_write_target(
        target,
        marker,
        payload=(",".join(workshop_ids) + "\n").encode("ascii"),
    )


def verify_and_maybe_apply(
    archive: Path,
    target: Path,
    *,
    apply: bool,
    stage_parent: Path,
) -> None:
    with tempfile.TemporaryDirectory(prefix="conan-restore-", dir=stage_parent) as temporary:
        stage = Path(temporary)
        extract_safely(archive, stage)
        verify_checksums(stage)
        verify_database(stage)
        metadata = validate_metadata(stage)
        if apply:
            apply_restore(stage, target, metadata)


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Verify or restore a native Conan backup")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--target", type=Path, default=Path(os.environ.get("GAME_DIR", "/data/server")))
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--verify-only", action="store_true")
    action.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    try:
        if not args.archive.is_file():
            raise RuntimeError("Backup archive does not exist")
        target = Path(os.path.abspath(args.target))
        if target.is_symlink():
            raise RuntimeError("Restore target root must not be a symlink")
        if args.apply:
            target.mkdir(parents=True, exist_ok=True)
            state = load_runtime_state_module()
            game_fd, runtime_fd = state.open_runtime_directory(target)
            lock_fd: int | None = None
            try:
                lock_fd = state.open_regular(
                    runtime_fd, "operation.lock", os.O_RDWR | os.O_CREAT
                )
                assert lock_fd is not None
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError("Another target operation is already running") from exc
                verify_and_maybe_apply(
                    args.archive.resolve(),
                    target,
                    apply=True,
                    stage_parent=Path(f"/proc/self/fd/{runtime_fd}"),
                )
            finally:
                if lock_fd is not None:
                    os.close(lock_fd)
                os.close(runtime_fd)
                os.close(game_fd)
        else:
            stage_parent = Path(
                os.environ.get("NATIVE_RESTORE_STAGE_DIR", str(args.archive.parent))
            ).resolve()
            if not stage_parent.is_dir():
                raise RuntimeError("Restore staging directory does not exist")
            verify_and_maybe_apply(
                args.archive.resolve(), target, apply=False, stage_parent=stage_parent
            )
    except (OSError, RuntimeError, sqlite3.Error, tarfile.TarError, json.JSONDecodeError) as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 1

    print("restore_verification=ok")
    if args.apply:
        print(f"restore_applied={target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
