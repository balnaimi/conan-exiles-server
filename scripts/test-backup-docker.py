#!/usr/bin/env python3
"""Isolated Docker integration for portable Wine/Native world backup and restore."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "conan_backup.py"
IMAGE = os.environ.get("CONAN_BACKUP_TEST_IMAGE", "python:3.12-slim")


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def compose(project: str, directory: Path, file: Path, *tail: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-directory",
        str(directory),
        "--project-name",
        project,
        "-f",
        str(file),
        *tail,
    ]


def write_compose(path: Path, project: str, runtime: str) -> tuple[str, str]:
    if runtime == "wine":
        service = "conan"
        label = "wine"
        target = "/conanexiles/ConanSandbox/Saved"
        volume_key = "saved"
    else:
        service = "conan-native"
        label = "native-linux"
        target = "/data/server/ConanSandbox/Saved"
        volume_key = "native-saved"
    volume_name = f"{project}_{volume_key.replace('-', '_')}"
    health = ""
    if runtime == "native":
        health = """
    healthcheck:
      test: [\"CMD\", \"python3\", \"-c\", \"raise SystemExit(0)\"]
      interval: 1s
      timeout: 1s
      retries: 3
"""
    path.write_text(
        f"""name: {project}
services:
  {service}:
    image: {IMAGE}
    command: [\"python3\", \"-c\", \"import time; time.sleep(3600)\"]
    labels:
      com.balnaimi.conan.runtime: {label}
    volumes:
      - {volume_key}:{target}
{health}volumes:
  {volume_key}:
    name: {volume_name}
""",
        encoding="utf-8",
    )
    return service, volume_name


def database_value(volume: str) -> str:
    code = (
        "import sqlite3; "
        "c=sqlite3.connect('/saved/game_0.db'); "
        "print(c.execute('select value from world').fetchone()[0]); c.close()"
    )
    return run(
        [
            "docker",
            "run",
            "--rm",
            "--mount",
            f"type=volume,src={volume},dst=/saved",
            IMAGE,
            "python3",
            "-c",
            code,
        ]
    ).stdout.strip()


def initialize_database(volume: str, value: str, *, uid: int = 0, gid: int = 0) -> None:
    code = (
        "import sqlite3; "
        "c=sqlite3.connect('/saved/game_0.db'); "
        "c.execute('create table if not exists world(value text)'); "
        "c.execute('delete from world'); "
        "c.execute('insert into world values (?)', (" + repr(value) + ",)); "
        "c.commit(); c.close(); "
        f"__import__('os').chown('/saved/game_0.db', {uid}, {gid})"
    )
    run(
        [
            "docker",
            "run",
            "--rm",
            "--mount",
            f"type=volume,src={volume},dst=/saved",
            IMAGE,
            "python3",
            "-c",
            code,
        ]
    )


def tool_command(
    project: str,
    directory: Path,
    wine_file: Path,
    native_file: Path,
    backup_dir: Path,
    runtime: str,
    *tail: str,
) -> list[str]:
    return [
        sys.executable,
        str(TOOL),
        "--project-directory",
        str(directory),
        "--project-name",
        project,
        "--wine-compose-file",
        str(wine_file),
        "--native-compose-file",
        str(native_file),
        "--backup-dir",
        str(backup_dir),
        "--runtime",
        runtime,
        *tail,
    ]


def scenario(runtime: str) -> None:
    project = f"conan_backup_it_{runtime}_{secrets.token_hex(5)}"
    if not project.startswith("conan_backup_it_"):
        raise RuntimeError("unsafe integration project name")
    temporary = Path(tempfile.mkdtemp(prefix=f"{project}-"))
    wine_file = temporary / "wine.yml"
    native_file = temporary / "native.yml"
    wine_service, wine_volume = write_compose(wine_file, project, "wine")
    native_service, native_volume = write_compose(native_file, project, "native")
    service = wine_service if runtime == "wine" else native_service
    volume = wine_volume if runtime == "wine" else native_volume
    compose_file = wine_file if runtime == "wine" else native_file
    backup_dir = temporary / "backups"
    try:
        run(["docker", "pull", IMAGE])
        run(compose(project, temporary, compose_file, "up", "-d", service))
        expected_owner = (1000, 1000) if runtime == "native" else (0, 0)
        initialize_database(volume, "original", uid=expected_owner[0], gid=expected_owner[1])

        created = json.loads(
            run(
                tool_command(
                    project,
                    temporary,
                    wine_file,
                    native_file,
                    backup_dir,
                    runtime,
                    "create",
                )
            ).stdout
        )
        archive = Path(created["archive"])
        if not archive.is_file():
            raise RuntimeError("create did not produce the reported archive")

        initialize_database(volume, "mutated", uid=expected_owner[0], gid=expected_owner[1])
        dry_run = json.loads(
            run(
                tool_command(
                    project,
                    temporary,
                    wine_file,
                    native_file,
                    backup_dir,
                    runtime,
                    "restore",
                    str(archive),
                )
            ).stdout
        )
        if dry_run["action"] != "dry-run" or database_value(volume) != "mutated":
            raise RuntimeError("restore dry-run mutated the world")

        applied = json.loads(
            run(
                tool_command(
                    project,
                    temporary,
                    wine_file,
                    native_file,
                    backup_dir,
                    runtime,
                    "restore",
                    str(archive),
                    "--apply",
                )
            ).stdout
        )
        if applied["action"] != "restored" or database_value(volume) != "original":
            raise RuntimeError("restore apply did not restore the archived world")
        owner = run(
            [
                "docker", "run", "--rm", "--mount", f"type=volume,src={volume},dst=/saved,readonly",
                IMAGE, "python3", "-c",
                "import os; s=os.stat('/saved/game_0.db'); print(f'{s.st_uid}:{s.st_gid}')",
            ]
        ).stdout.strip()
        if owner != f"{expected_owner[0]}:{expected_owner[1]}":
            raise RuntimeError(f"restore changed database ownership: {owner}")

        before = run(compose(project, temporary, compose_file, "ps", "-q", service)).stdout.strip()
        repeated = json.loads(
            run(
                tool_command(
                    project,
                    temporary,
                    wine_file,
                    native_file,
                    backup_dir,
                    runtime,
                    "restore",
                    str(archive),
                    "--apply",
                )
            ).stdout
        )
        after = run(compose(project, temporary, compose_file, "ps", "-q", service)).stdout.strip()
        if repeated["action"] != "already-applied" or before != after:
            raise RuntimeError("idempotent restore contract failed")
        state = json.loads((backup_dir / "restore-state.json").read_text(encoding="utf-8"))
        if state["status"] != "complete":
            raise RuntimeError("restore state did not record completion")
        print(f"{runtime}: create + dry-run + apply + idempotency PASS")
    finally:
        for file in (wine_file, native_file):
            run(compose(project, temporary, file, "down", "--remove-orphans", "-v"), check=False)
        for volume_name in (wine_volume, native_volume):
            if volume_name.startswith(project):
                run(["docker", "volume", "rm", "-f", volume_name], check=False)
        shutil.rmtree(temporary, ignore_errors=True)


def main() -> int:
    run(["docker", "version"])
    scenario("wine")
    scenario("native")
    print("Portable backup Docker integration: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
