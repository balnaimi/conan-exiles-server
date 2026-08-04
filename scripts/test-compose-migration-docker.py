#!/usr/bin/env python3
"""Manual Docker integration test for Compose Wine-to-Native migration.

Uses isolated random Compose projects and synthetic SQLite worlds. It never mounts
or inspects an operator's existing Conan volumes.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import textwrap
import uuid
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "migrate-compose-wine-to-native.sh"


def run(command: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stderr}")
    return result


def compose(project_dir: Path, project: str, file: str, *tail: str, check: bool = True):
    return run(
        [
            "docker", "compose", "--project-directory", str(project_dir),
            "--project-name", project, "-f", str(project_dir / file), *tail,
        ],
        check=check,
    )


def write_compose(project_dir: Path, image: str, healthy: bool) -> None:
    stop_hook = project_dir / "stop-hook.py"
    stop_hook.write_text(
        "import sqlite3\n"
        "p='/conanexiles/ConanSandbox/Saved/game_0.db'\n"
        "c=sqlite3.connect(p)\n"
        "c.execute(\"UPDATE migration_test SET value='synthetic-world-after-clean-stop'\")\n"
        "c.commit()\n"
        "c.close()\n",
        encoding="utf-8",
    )
    wine = f"""
services:
  conan:
    image: {image}
    entrypoint: ["/bin/bash", "-c"]
    command: ["trap 'python3 /stop-hook.py; exit 0' TERM INT; while true; do sleep 1; done"]
    labels:
      com.balnaimi.conan.runtime: "wine"
    volumes:
      - game-data:/conanexiles
      - config-data:/conanexiles/ConanSandbox/Saved
      - type: bind
        source: {json.dumps(str(stop_hook))}
        target: /stop-hook.py
        read_only: true
volumes:
  game-data:
  config-data:
"""
    health_command = "test -s /data/server/ConanSandbox/Saved/game_0.db" if healthy else "exit 1"
    native = f"""
services:
  conan-native:
    image: {image}
    entrypoint: ["/bin/bash", "-c"]
    command: ["trap 'exit 0' TERM INT; while true; do sleep 1; done"]
    labels:
      com.balnaimi.conan.runtime: "native-linux"
    healthcheck:
      test: ["CMD-SHELL", "{health_command}"]
      interval: 1s
      timeout: 1s
      retries: 3
      start_period: 1s
    volumes:
      - native-game-data:/data/server
      - native-save-data:/data/server/ConanSandbox/Saved
      - native-steam-data:/data/steam
      - native-backups:/data/backups
volumes:
  native-game-data:
  native-save-data:
  native-steam-data:
  native-backups:
"""
    (project_dir / "docker-compose.yml").write_text(textwrap.dedent(wine).lstrip(), encoding="utf-8")
    (project_dir / "docker-compose.native.yml").write_text(textwrap.dedent(native).lstrip(), encoding="utf-8")


def volume_names(project: str) -> list[str]:
    return [
        f"{project}_game-data",
        f"{project}_config-data",
        f"{project}_native-game-data",
        f"{project}_native-save-data",
        f"{project}_native-steam-data",
        f"{project}_native-backups",
    ]


def populate_world(image: str, volume: str) -> None:
    program = """
import sqlite3
import os
from pathlib import Path
path = Path('/saved/game_0.db')
connection = sqlite3.connect(path)
connection.execute('CREATE TABLE migration_test (value TEXT NOT NULL)')
connection.execute("INSERT INTO migration_test VALUES ('synthetic-world-ok')")
connection.commit()
connection.close()
Path('/saved/Config/WindowsServer').mkdir(parents=True)
Path('/saved/Config/WindowsServer/ServerSettings.ini').write_text('[ServerSettings]\\nPVPEnabled=False\\n')
for root, directories, files in os.walk('/saved'):
    os.chown(root, 1000, 1000)
    for name in directories + files:
        os.chown(os.path.join(root, name), 1000, 1000)
"""
    run([
        "docker", "run", "--rm", "--user", "0:0",
        "--mount", f"type=volume,src={volume},dst=/saved",
        "--entrypoint", "python3", image, "-c", textwrap.dedent(program),
    ])


def database_hash(image: str, volume: str) -> str:
    result = run([
        "docker", "run", "--rm", "--user", "0:0",
        "--mount", f"type=volume,src={volume},dst=/saved,readonly",
        "--entrypoint", "python3", image, "-c",
        "import hashlib; print(hashlib.sha256(open('/saved/game_0.db','rb').read()).hexdigest())",
    ])
    value = result.stdout.strip()
    if len(value) != 64:
        raise AssertionError(f"invalid database hash: {value!r}")
    return value


def destination_value(image: str, volume: str) -> str:
    result = run([
        "docker", "run", "--rm", "--user", "0:0",
        "--mount", f"type=volume,src={volume},dst=/saved,readonly",
        "--entrypoint", "python3", image, "-c",
        "import sqlite3; c=sqlite3.connect('file:/saved/game_0.db?mode=ro',uri=True); print(c.execute('SELECT value FROM migration_test').fetchone()[0])",
    ])
    return result.stdout.strip()


def running(project_dir: Path, project: str, file: str, service: str) -> bool:
    result = compose(project_dir, project, file, "ps", "--status", "running", "-q", service)
    return bool(result.stdout.strip())


def wrapper(project_dir: Path, project: str, action: str, *, check: bool = True):
    return run(
        [
            str(WRAPPER), action,
            "--project-directory", str(project_dir),
            "--project-name", project,
            "--wait-seconds", "12",
        ],
        check=check,
    )


def cleanup(project_dir: Path, project: str) -> None:
    compose(project_dir, project, "docker-compose.native.yml", "down", "--remove-orphans", check=False)
    compose(project_dir, project, "docker-compose.yml", "down", "--remove-orphans", check=False)
    for volume in volume_names(project):
        run(["docker", "volume", "rm", "--force", volume], check=False)


def scenario(image: str, *, healthy: bool, remove_source_before_rollback: bool = False) -> None:
    project = f"serenmigration{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory(prefix="conan-compose-migration-") as temporary:
        project_dir = Path(temporary)
        write_compose(project_dir, image, healthy)
        try:
            compose(project_dir, project, "docker-compose.yml", "up", "-d", "conan")
            source_volume = f"{project}_config-data"
            destination_volume = f"{project}_native-save-data"
            populate_world(image, source_volume)
            before_hash = database_hash(image, source_volume)

            plan = wrapper(project_dir, project, "plan")
            assert "dry-run" in plan.stdout
            assert running(project_dir, project, "docker-compose.yml", "conan")
            assert not run(["docker", "volume", "inspect", destination_volume], check=False).returncode == 0

            applied = wrapper(project_dir, project, "apply", check=healthy)
            state_path = project_dir / ".conan-migration" / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            stopped_hash = database_hash(image, source_volume)
            assert stopped_hash != before_hash
            assert destination_value(image, destination_volume) == "synthetic-world-after-clean-stop"
            assert list((project_dir / ".conan-migration").glob("wine-pre-native-*.tar.gz"))
            assert run(["docker", "volume", "inspect", source_volume], check=False).returncode == 0

            if healthy:
                assert applied.returncode == 0
                assert state["status"] == "native-running-pending-acceptance"
                assert not running(project_dir, project, "docker-compose.yml", "conan")
                assert running(project_dir, project, "docker-compose.native.yml", "conan-native")
                if remove_source_before_rollback:
                    compose(project_dir, project, "docker-compose.yml", "rm", "-f", "conan")
                    run(["docker", "volume", "rm", source_volume])
                    refused = wrapper(project_dir, project, "rollback", check=False)
                    assert refused.returncode != 0
                    assert "source volume is missing" in refused.stderr
                    assert running(project_dir, project, "docker-compose.native.yml", "conan-native")
                    assert run(["docker", "volume", "inspect", source_volume], check=False).returncode != 0
                    failed_state = json.loads(state_path.read_text(encoding="utf-8"))
                    assert failed_state["status"] == "rollback-failed"
                    print("PASS missing Wine source refuses rollback before stopping Native or recreating a volume")
                    return
                wrapper(project_dir, project, "rollback")
                rolled_back = json.loads(state_path.read_text(encoding="utf-8"))
                assert rolled_back["status"] == "rolled-back"
                assert database_hash(image, source_volume) == stopped_hash
                assert running(project_dir, project, "docker-compose.yml", "conan")
                assert not running(project_dir, project, "docker-compose.native.yml", "conan-native")
                print("PASS isolated named-volume apply + healthy Native + explicit rollback")
            else:
                assert applied.returncode != 0
                assert "Native health failed" in applied.stderr
                assert state["status"] == "rolled-back-automatic"
                assert database_hash(image, source_volume) == stopped_hash
                assert running(project_dir, project, "docker-compose.yml", "conan")
                assert not running(project_dir, project, "docker-compose.native.yml", "conan-native")
                print("PASS isolated Native health failure + automatic Wine rollback")
        finally:
            cleanup(project_dir, project)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--image",
        default=os.environ.get("MIGRATION_TEST_IMAGE", "ghcr.io/balnaimi/conan-exiles-server:native"),
    )
    args = parser.parse_args()
    run(["docker", "image", "inspect", args.image])
    scenario(args.image, healthy=True)
    scenario(args.image, healthy=False)
    scenario(args.image, healthy=True, remove_source_before_rollback=True)
    print("Compose migration Docker integration checks OK")


if __name__ == "__main__":
    main()
