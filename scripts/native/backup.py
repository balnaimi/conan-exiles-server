#!/usr/bin/env python3
"""Create verified Conan Enhanced native backups.

The world database is copied through SQLite's backup API. Live WAL/SHM files are
never combined with that snapshot.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

import runtime_state


def positive_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


SECRET_INI_PATTERN = re.compile(
    r"^(?P<prefix>\s*(?:AdminPassword|ServerPassword|RconPassword)\s*=).*$",
    flags=re.IGNORECASE,
)


def safe_copy(source: Path, destination: Path, *, redact_ini_secrets: bool = False) -> None:
    if source.is_symlink() or not source.is_file():
        raise RuntimeError(f"Refusing non-regular backup source: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if redact_ini_secrets:
        lines = source.read_text(encoding="utf-8", errors="strict").splitlines(keepends=True)
        redacted: list[str] = []
        for line in lines:
            ending = "\n" if line.endswith("\n") else ""
            body = line[:-1] if ending else line
            if body.endswith("\r"):
                body = body[:-1]
            match = SECRET_INI_PATTERN.match(body)
            redacted.append(f"{match.group('prefix')}[REDACTED]{ending}" if match else line)
        destination.write_text("".join(redacted), encoding="utf-8")
        destination.chmod(0o600)
        return
    shutil.copy2(source, destination)


def sqlite_snapshot(source_path: Path, destination_path: Path) -> None:
    if source_path.is_symlink() or not source_path.is_file():
        raise RuntimeError(f"World database is missing or unsafe: {source_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=30)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"SQLite snapshot integrity failed: {result}")
    finally:
        destination.close()
        source.close()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checksums(stage: Path) -> None:
    rows: list[str] = []
    for path in sorted(stage.rglob("*")):
        if not path.is_file() or path.name == "checksums.sha256":
            continue
        digest = sha256_file(path)
        rows.append(f"{digest}  {path.relative_to(stage).as_posix()}")
    (stage / "checksums.sha256").write_text("\n".join(rows) + "\n", encoding="utf-8")


def active_mods(mods_dir: Path) -> list[tuple[str, str]]:
    manifest = mods_dir / ".managed-mods.tsv"
    if not manifest.exists():
        return []
    rows: list[tuple[str, str]] = []
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        try:
            mod_id, pak_name = line.split("\t", 1)
        except ValueError as exc:
            raise RuntimeError(f"Malformed managed mod manifest line {number}") from exc
        if not mod_id.isdigit() or Path(pak_name).name != pak_name or not pak_name.endswith(".pak"):
            raise RuntimeError(f"Unsafe managed mod manifest line {number}")
        rows.append((mod_id, pak_name))
    return rows


def apply_retention(backup_dir: Path, keep_count: int, keep_days: int) -> None:
    archives = sorted(backup_dir.glob("conan-native-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - keep_days * 86400
    for index, archive in enumerate(archives):
        if index >= keep_count or archive.stat().st_mtime < cutoff:
            archive.unlink()


def create_backup(reason: str, *, acquire_operation_lock: bool = True) -> Path:
    os.umask(0o077)
    game_dir = Path(os.environ.get("GAME_DIR", "/data/server")).resolve()
    backup_dir = Path(os.environ.get("BACKUP_DIR", "/data/backups")).resolve()
    mode = os.environ.get("NATIVE_BACKUP_MODE", "light").lower()
    if mode not in {"light", "full"}:
        raise RuntimeError("NATIVE_BACKUP_MODE must be light or full")
    keep_count = positive_int("NATIVE_BACKUP_RETENTION_COUNT", 14, 1, 1000)
    keep_days = positive_int("NATIVE_BACKUP_RETENTION_DAYS", 30, 1, 3650)

    saved_dir = game_dir / "ConanSandbox" / "Saved"
    config_dir = saved_dir / "Config" / "LinuxServer"
    mods_dir = game_dir / "ConanSandbox" / "Mods"
    world_db = saved_dir / "game_0.db"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.chmod(0o700)
    lock_path = backup_dir / ".backup.lock"

    with contextlib.ExitStack() as locks:
        if acquire_operation_lock:
            game_fd, runtime_fd = runtime_state.open_runtime_directory(game_dir)
            locks.callback(os.close, game_fd)
            locks.callback(os.close, runtime_fd)
            operation_lock = runtime_state.open_regular(
                runtime_fd, "operation.lock", os.O_RDWR | os.O_CREAT
            )
            locks.callback(os.close, operation_lock)
            try:
                fcntl.flock(operation_lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another target operation is already running") from exc
        backup_fd = os.open(
            backup_dir,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        locks.callback(os.close, backup_fd)
        lock = runtime_state.open_regular(
            backup_fd, lock_path.name, os.O_RDWR | os.O_CREAT
        )
        locks.callback(os.close, lock)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another backup operation is already running") from exc

        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        final = backup_dir / f"conan-native-{timestamp}-{mode}.tar.gz"
        temporary_archive = final.with_suffix(final.suffix + ".tmp")

        with tempfile.TemporaryDirectory(prefix=".backup-stage-", dir=backup_dir) as temporary:
            stage = Path(temporary)
            sqlite_snapshot(world_db, stage / "world" / "game_0.db")

            if config_dir.exists():
                for source in sorted(config_dir.rglob("*")):
                    if source.is_file():
                        safe_copy(
                            source,
                            stage / "config" / source.relative_to(config_dir),
                            redact_ini_secrets=source.suffix.lower() == ".ini",
                        )

            for name in ("modlist.txt", ".managed-mods.tsv"):
                source = mods_dir / name
                if source.exists():
                    safe_copy(source, stage / "mods" / name)

            mods = active_mods(mods_dir)
            if mode == "full":
                for _, pak_name in mods:
                    safe_copy(mods_dir / pak_name, stage / "mods" / pak_name)

            metadata = {
                "format_version": 1,
                "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "reason": reason,
                "mode": mode,
                "world_database": "game_0.db",
                "workshop_ids": [mod_id for mod_id, _ in mods],
                "active_packages": [pak for _, pak in mods],
                "config_secrets_redacted": True,
            }
            (stage / "metadata.json").write_text(
                json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            checksums(stage)

            with tarfile.open(temporary_archive, "w:gz", format=tarfile.PAX_FORMAT) as bundle:
                for source in sorted(stage.rglob("*")):
                    if source.is_file():
                        bundle.add(source, arcname=source.relative_to(stage).as_posix(), recursive=False)
            os.replace(temporary_archive, final)
            final.chmod(0o600)

        apply_retention(backup_dir, keep_count, keep_days)
        return final


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a verified native Conan backup")
    parser.add_argument("--reason", default="manual")
    args = parser.parse_args()
    try:
        archive = create_backup(args.reason)
    except (OSError, RuntimeError, sqlite3.Error, tarfile.TarError) as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"backup_created={archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
