#!/usr/bin/env python3
"""Verify and safely restore Conan Enhanced native backup archives."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


def safe_member(member: tarfile.TarInfo) -> None:
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts or not member.name:
        raise RuntimeError(f"Unsafe archive path: {member.name}")
    if member.issym() or member.islnk() or member.isdev():
        raise RuntimeError(f"Unsafe archive member type: {member.name}")
    if not (member.isfile() or member.isdir()):
        raise RuntimeError(f"Unsupported archive member type: {member.name}")


def extract_safely(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        for member in members:
            safe_member(member)
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise RuntimeError(f"Could not read archive member: {member.name}")
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o600)


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
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"Unsafe checksum path: {relative}")
        source = stage.joinpath(*path.parts)
        if not source.is_file() or source.is_symlink():
            raise RuntimeError(f"Checksummed file is missing or unsafe: {relative}")
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
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


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".restore-tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def process_is_active(pid_file: Path) -> bool:
    if not pid_file.is_file():
        return False
    try:
        pid = int(pid_file.read_text(encoding="ascii").strip())
        os.kill(pid, 0)
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        return False
    return True


def apply_restore(stage: Path, target: Path) -> None:
    pid_file = target / ".runtime" / "server.pid"
    if process_is_active(pid_file):
        raise RuntimeError("Refusing restore while the native server process is active")

    existing_db = target / "ConanSandbox" / "Saved" / "game_0.db"
    if existing_db.exists():
        environment = os.environ.copy()
        environment["GAME_DIR"] = str(target)
        backup_tool = Path(__file__).with_name("backup.py")
        completed = subprocess.run(
            [sys.executable, str(backup_tool), "--reason", "pre-restore"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Pre-restore backup failed: {completed.stderr.strip()}")

    atomic_copy(stage / "world" / "game_0.db", target / "ConanSandbox" / "Saved" / "game_0.db")
    config_source = stage / "config"
    if config_source.exists():
        for source in sorted(config_source.rglob("*")):
            if source.is_file():
                atomic_copy(
                    source,
                    target / "ConanSandbox" / "Saved" / "Config" / "LinuxServer" / source.relative_to(config_source),
                )
    mods_source = stage / "mods"
    if mods_source.exists():
        for source in sorted(mods_source.rglob("*")):
            if source.is_file():
                atomic_copy(source, target / "ConanSandbox" / "Mods" / source.relative_to(mods_source))

    metadata = json.loads((stage / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("mode") == "light" and metadata.get("workshop_ids"):
        runtime = target / ".runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "restore-required-workshop-ids").write_text(
            ",".join(metadata["workshop_ids"]) + "\n", encoding="ascii"
        )


def main() -> int:
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
        lock_path = args.archive.parent / ".restore.lock"
        with lock_path.open("a+b") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another restore operation is already running") from exc
            with tempfile.TemporaryDirectory(prefix="conan-restore-") as temporary:
                stage = Path(temporary)
                extract_safely(args.archive, stage)
                verify_checksums(stage)
                verify_database(stage)
                metadata_file = stage / "metadata.json"
                if not metadata_file.is_file():
                    raise RuntimeError("Backup metadata is missing")
                metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
                if metadata.get("format_version") != 1:
                    raise RuntimeError("Unsupported backup format version")
                if args.apply:
                    apply_restore(stage, args.target.resolve())
    except (OSError, RuntimeError, sqlite3.Error, tarfile.TarError, json.JSONDecodeError) as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 1

    print("restore_verification=ok")
    if args.apply:
        print(f"restore_applied={args.target.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
