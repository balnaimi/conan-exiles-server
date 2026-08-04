#!/usr/bin/env python3
"""Compose-aware, reversible Wine-to-Native migration orchestration.

The low-level migration helper performs the SQLite snapshot. This wrapper discovers
Compose's project-scoped named volumes, proves Wine is quiescent, keeps rollback
artifacts outside Native volumes, starts Native, and restores Wine automatically
when Native health does not become healthy.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, NamedTuple, Sequence

ROOT = Path(__file__).resolve().parents[1]
LOW_LEVEL_HELPER = ROOT / "scripts" / "migrate-wine-to-native.sh"
RUNTIME_LABEL = "com.balnaimi.conan.runtime"
WINE_SAVE_TARGET = "/conanexiles/ConanSandbox/Saved"
NATIVE_SAVE_TARGET = "/data/server/ConanSandbox/Saved"
STATE_VERSION = 1


class MigrationError(RuntimeError):
    """A user-correctable migration safety failure."""


class MigrationLayout(NamedTuple):
    project_name: str
    wine_service: str
    native_service: str
    wine_save_volume: str
    native_save_volume: str
    helper_image: str


def _runtime_service(config: dict[str, Any], runtime: str) -> tuple[str, dict[str, Any]]:
    matches = [
        (name, service)
        for name, service in config.get("services", {}).items()
        if service.get("labels", {}).get(RUNTIME_LABEL) == runtime
    ]
    if len(matches) != 1:
        raise MigrationError(f"expected exactly one Compose service labelled {RUNTIME_LABEL}={runtime!r}")
    return matches[0]


def _named_volume_at(config: dict[str, Any], service: dict[str, Any], target: str) -> str:
    matches = [mount for mount in service.get("volumes", []) if mount.get("target") == target]
    if len(matches) != 1:
        raise MigrationError(f"expected exactly one save mount at {target}")
    mount = matches[0]
    if mount.get("type") != "volume":
        raise MigrationError(f"save mount at {target} must be a Compose named volume, not {mount.get('type')!r}")
    source = mount.get("source")
    if not isinstance(source, str) or not source:
        raise MigrationError(f"save mount at {target} has no named-volume source")
    definition = config.get("volumes", {}).get(source)
    if not isinstance(definition, dict) or not isinstance(definition.get("name"), str):
        raise MigrationError(f"Compose did not resolve the engine volume name for {source!r}")
    return definition["name"]


def discover_layout(wine_config: dict[str, Any], native_config: dict[str, Any]) -> MigrationLayout:
    wine_project = wine_config.get("name")
    native_project = native_config.get("name")
    if not isinstance(wine_project, str) or not wine_project or wine_project != native_project:
        raise MigrationError("Wine and Native Compose files must resolve to the same explicit project name")
    wine_name, wine_service = _runtime_service(wine_config, "wine")
    native_name, native_service = _runtime_service(native_config, "native-linux")
    wine_volume = _named_volume_at(wine_config, wine_service, WINE_SAVE_TARGET)
    native_volume = _named_volume_at(native_config, native_service, NATIVE_SAVE_TARGET)
    if wine_volume == native_volume:
        raise MigrationError("Wine source volume and Native destination volume must be different")
    helper_image = native_service.get("image")
    if not isinstance(helper_image, str) or not helper_image:
        raise MigrationError("Native Compose service has no image")
    return MigrationLayout(
        project_name=wine_project,
        wine_service=wine_name,
        native_service=native_name,
        wine_save_volume=wine_volume,
        native_save_volume=native_volume,
        helper_image=helper_image,
    )


class Docker:
    def run(
        self,
        command: Sequence[str],
        *,
        check: bool = True,
        capture: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            list(command),
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            check=False,
        )
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "command failed").strip()
            raise MigrationError(detail)
        return result


def compose_command(args: argparse.Namespace, compose_file: Path, *tail: str) -> list[str]:
    command = ["docker", "compose", "--project-directory", str(args.project_directory)]
    if args.project_name:
        command.extend(["--project-name", args.project_name])
    command.extend(["-f", str(compose_file), *tail])
    return command


def render_compose(docker: Docker, args: argparse.Namespace, compose_file: Path) -> dict[str, Any]:
    result = docker.run(compose_command(args, compose_file, "config", "--format", "json"))
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MigrationError(f"Docker Compose returned invalid JSON for {compose_file}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise MigrationError(f"Docker Compose returned a non-object for {compose_file}")
    return parsed


def volume_exists(docker: Docker, name: str) -> bool:
    result = docker.run(["docker", "volume", "inspect", name], check=False)
    if result.returncode == 0:
        return True
    combined = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    if "no such volume" in combined or "not found" in combined:
        return False
    raise MigrationError((result.stderr or result.stdout or f"cannot inspect volume {name}").strip())


def running_service_ids(docker: Docker, args: argparse.Namespace, compose_file: Path, service: str) -> list[str]:
    result = docker.run(compose_command(args, compose_file, "ps", "--status", "running", "-q", service))
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def active_service_ids(docker: Docker, args: argparse.Namespace, compose_file: Path, service: str) -> list[str]:
    result = docker.run(compose_command(args, compose_file, "ps", "--all", "-q", service))
    active: list[str] = []
    for container_id in (line.strip() for line in result.stdout.splitlines() if line.strip()):
        status, _ = container_health(docker, container_id)
        if status not in {"created", "exited", "dead"}:
            active.append(container_id)
    return active


def source_users(docker: Docker, volume: str) -> list[str]:
    result = docker.run(
        ["docker", "ps", "--filter", f"volume={volume}", "--format", "{{.ID}}"],
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def helper_base(layout: MigrationLayout) -> list[str]:
    helper = str(LOW_LEVEL_HELPER.resolve())
    if "," in helper:
        raise MigrationError("repository path contains a comma and cannot be mounted safely")
    return [
        "docker", "run", "--rm", "--user", "0:0", "--entrypoint", "/bin/bash",
        "--mount", f"type=volume,src={layout.wine_save_volume},dst=/source,readonly",
        "--mount", f"type=bind,src={helper},dst=/tool/migrate.sh,readonly",
    ]


def run_helper_plan(docker: Docker, layout: MigrationLayout) -> str:
    command = helper_base(layout) + [
        layout.helper_image,
        "/tool/migrate.sh",
        "--source", "/source",
        "--destination", "/tmp/native-save",
        "--source-saved-root",
        "--destination-saved-root",
    ]
    result = docker.run(command)
    match = re.search(r"^\s*source_sha256:\s*([0-9a-f]{64})\s*$", result.stdout, re.MULTILINE)
    if not match:
        raise MigrationError("migration helper did not report a source SHA-256")
    return match.group(1)


def run_helper_apply(
    docker: Docker,
    layout: MigrationLayout,
    state_dir: Path,
    host_uid: int,
    host_gid: int,
) -> None:
    if "," in str(state_dir):
        raise MigrationError("migration state path contains a comma and cannot be mounted safely")
    command = helper_base(layout)
    command.extend([
        "--mount", f"type=volume,src={layout.native_save_volume},dst=/destination",
        "--mount", f"type=bind,src={state_dir},dst=/state",
        layout.helper_image,
        "/tool/migrate.sh",
        "--source", "/source",
        "--destination", "/destination",
        "--source-saved-root",
        "--destination-saved-root",
        "--backup-dir", "/state",
        "--source-stopped",
        "--apply",
    ])
    docker.run(command, capture=False)
    docker.run([
        "docker", "run", "--rm", "--user", "0:0", "--entrypoint", "/bin/bash",
        "--mount", f"type=volume,src={layout.native_save_volume},dst=/destination",
        "--mount", f"type=bind,src={state_dir},dst=/state",
        layout.helper_image,
        "-c", f"chown -R 1000:1000 /destination && chown -R {host_uid}:{host_gid} /state",
    ], capture=False)


def prepare_state_directory(path: Path) -> None:
    for component in (*reversed(path.parents), path):
        if component.is_symlink():
            raise MigrationError(f"migration state path must not contain symlinks: {component}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise MigrationError(f"migration state path is not a safe directory: {path}")
    os.chmod(path, 0o700)


def write_state(path: Path, data: dict[str, Any]) -> None:
    if path.is_symlink():
        raise MigrationError(f"migration state must not be a symlink: {path}")
    prepare_state_directory(path.parent)
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        temporary = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise MigrationError(f"cannot safely write migration state: {path}") from exc
    assert temporary is not None
    if path.is_symlink():
        temporary.unlink(missing_ok=True)
        raise MigrationError(f"migration state must not be a symlink: {path}")
    os.replace(temporary, path)


def read_state(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise MigrationError(f"migration state must not be a symlink: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MigrationError(f"migration state is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MigrationError(f"migration state is invalid: {path}") from exc
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        raise MigrationError("unsupported migration state format")
    return data


def state_for(layout: MigrationLayout, status: str, source_hash: str) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "status": status,
        "project_name": layout.project_name,
        "wine_service": layout.wine_service,
        "native_service": layout.native_service,
        "wine_save_volume": layout.wine_save_volume,
        "native_save_volume": layout.native_save_volume,
        "helper_image": layout.helper_image,
        "source_sha256": source_hash,
        "updated_at_unix": int(time.time()),
    }


def update_status(state_path: Path, state: dict[str, Any], status: str) -> None:
    state = dict(state)
    state["status"] = status
    state["updated_at_unix"] = int(time.time())
    write_state(state_path, state)


def assert_state_layout(state: dict[str, Any], layout: MigrationLayout) -> None:
    expected = {
        "project_name": layout.project_name,
        "wine_service": layout.wine_service,
        "native_service": layout.native_service,
        "wine_save_volume": layout.wine_save_volume,
        "native_save_volume": layout.native_save_volume,
    }
    for key, value in expected.items():
        if state.get(key) != value:
            raise MigrationError(f"current Compose layout no longer matches migration state: {key}")


def container_health(docker: Docker, container_id: str) -> tuple[str, str]:
    result = docker.run(["docker", "inspect", container_id, "--format", "{{json .State}}"])
    try:
        state = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MigrationError("Docker returned invalid container state JSON") from exc
    status = str(state.get("Status", "unknown"))
    health = str((state.get("Health") or {}).get("Status", "missing"))
    return status, health


def wait_for_native_health(
    docker: Docker,
    args: argparse.Namespace,
    layout: MigrationLayout,
) -> None:
    deadline = time.monotonic() + args.wait_seconds
    last = "not-created"
    while time.monotonic() < deadline:
        ids = running_service_ids(docker, args, args.native_compose, layout.native_service)
        if ids:
            status, health = container_health(docker, ids[0])
            last = f"status={status} health={health}"
            if status == "running" and health == "healthy":
                print("Native health: healthy")
                return
            if status in {"exited", "dead"} or health == "unhealthy":
                raise MigrationError(f"Native health failed: {last}")
        else:
            last = "Native service is not running"
        time.sleep(min(2.0, max(0.1, args.wait_seconds / 20)))
    raise MigrationError(f"Native health timed out after {args.wait_seconds}s: {last}")


def validate_rollback_source(
    docker: Docker,
    layout: MigrationLayout,
    expected_source_hash: str | None,
) -> None:
    if not volume_exists(docker, layout.wine_save_volume):
        raise MigrationError(
            "Wine source volume is missing; refusing rollback because Compose would create an empty volume"
        )
    current_hash = run_helper_plan(docker, layout)
    if expected_source_hash is not None and current_hash != expected_source_hash:
        raise MigrationError(
            "Wine source database no longer matches the stopped migration source; manual recovery is required"
        )


def stop_native_start_wine(
    docker: Docker,
    args: argparse.Namespace,
    layout: MigrationLayout,
    expected_source_hash: str | None = None,
) -> None:
    validate_rollback_source(docker, layout, expected_source_hash)
    docker.run(compose_command(args, args.native_compose, "stop", layout.native_service), capture=False)
    native_ids = active_service_ids(docker, args, args.native_compose, layout.native_service)
    if native_ids:
        raise MigrationError(
            "rollback could not prove Native stopped; Wine was not started: "
            + ", ".join(native_ids)
        )
    validate_rollback_source(docker, layout, expected_source_hash)
    docker.run(compose_command(args, args.wine_compose, "up", "-d", layout.wine_service), capture=False)
    wine_ids = running_service_ids(docker, args, args.wine_compose, layout.wine_service)
    if not wine_ids:
        raise MigrationError("rollback started the Wine Compose service but could not prove its container is running")


def action_plan(docker: Docker, args: argparse.Namespace, layout: MigrationLayout) -> int:
    if not volume_exists(docker, layout.wine_save_volume):
        raise MigrationError(f"Wine source volume does not exist: {layout.wine_save_volume}")
    source_hash = run_helper_plan(docker, layout)
    destination = "existing" if volume_exists(docker, layout.native_save_volume) else "not created"
    print("Compose Wine-to-Native migration plan (dry-run):")
    print(f"  project: {layout.project_name}")
    print(f"  Wine service/source volume: {layout.wine_service} / {layout.wine_save_volume}")
    print(f"  Native service/destination volume: {layout.native_service} / {layout.native_save_volume} ({destination})")
    print(f"  source_sha256: {source_hash}")
    print("  apply will stop Wine, prove the source volume is unused, snapshot SQLite, start Native, and verify Native health")
    print("  rollback keeps both volumes and restarts Wine; no volume deletion is performed")
    return 0


def action_apply(docker: Docker, args: argparse.Namespace, layout: MigrationLayout, state_path: Path) -> int:
    if state_path.is_symlink():
        raise MigrationError(f"migration state must not be a symlink: {state_path}")
    if state_path.exists():
        existing = read_state(state_path)
        if existing.get("status") not in {"rolled-back", "rolled-back-automatic"}:
            raise MigrationError(f"migration state already exists with status {existing.get('status')!r}; use rollback or inspect it first")
    if not volume_exists(docker, layout.wine_save_volume):
        raise MigrationError(f"Wine source volume does not exist: {layout.wine_save_volume}")
    if not running_service_ids(docker, args, args.wine_compose, layout.wine_service):
        raise MigrationError("Wine service must be running before apply so the wrapper can own the cutover")
    if active_service_ids(docker, args, args.native_compose, layout.native_service):
        raise MigrationError("Native service is already running")

    reviewed_live_hash = run_helper_plan(docker, layout)
    state = state_for(layout, "stopping-wine", reviewed_live_hash)
    write_state(state_path, state)

    stopped_hash: str | None = None
    try:
        docker.run(compose_command(args, args.wine_compose, "stop", layout.wine_service), capture=False)
        users = source_users(docker, layout.wine_save_volume)
        if users:
            raise MigrationError(f"source volume is still attached to running containers: {', '.join(users)}")
        stopped_hash = run_helper_plan(docker, layout)
        if stopped_hash != reviewed_live_hash:
            print("Source database changed during Wine shutdown; using the definitive stopped hash.")
        state = state_for(layout, "copying", stopped_hash)
        write_state(state_path, state)
        if not volume_exists(docker, layout.native_save_volume):
            docker.run(["docker", "volume", "create", layout.native_save_volume])
        destination_users = source_users(docker, layout.native_save_volume)
        if destination_users:
            raise MigrationError(
                "Native destination volume is attached to running containers: "
                + ", ".join(destination_users)
            )
        users = source_users(docker, layout.wine_save_volume)
        if users:
            raise MigrationError(
                "source volume became active again before the snapshot: "
                + ", ".join(users)
            )
        run_helper_apply(docker, layout, args.state_dir, os.getuid(), os.getgid())
        if run_helper_plan(docker, layout) != stopped_hash:
            raise MigrationError("source database changed while the stopped snapshot was being created")
        update_status(state_path, state, "starting-native")
        docker.run(compose_command(args, args.native_compose, "up", "-d", layout.native_service), capture=False)
        wait_for_native_health(docker, args, layout)
    except BaseException as migration_error:
        try:
            stop_native_start_wine(docker, args, layout, stopped_hash)
        except BaseException as rollback_error:
            try:
                update_status(state_path, state, "rollback-failed")
            except Exception:
                pass
            raise MigrationError(
                "migration failed and automatic rollback also failed; manual recovery is required. "
                f"migration error: {migration_error}; rollback error: {rollback_error}"
            ) from rollback_error
        update_status(state_path, state, "rolled-back-automatic")
        raise

    update_status(state_path, state, "native-running-pending-acceptance")
    print("Migration applied. Native health is healthy; Wine data and rollback archive remain unchanged.")
    print(f"State: {state_path}")
    print("Rollback: ./scripts/migrate-compose-wine-to-native.sh rollback")
    return 0


def action_rollback(docker: Docker, args: argparse.Namespace, layout: MigrationLayout, state_path: Path) -> int:
    state = read_state(state_path)
    assert_state_layout(state, layout)
    expected_hash = state.get("source_sha256")
    if not isinstance(expected_hash, str) or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None:
        raise MigrationError("migration state has an invalid source database hash")
    try:
        stop_native_start_wine(docker, args, layout, expected_hash)
    except BaseException:
        try:
            update_status(state_path, state, "rollback-failed")
        except Exception:
            pass
        raise
    update_status(state_path, state, "rolled-back")
    print("Rollback complete: Wine restarted against the unchanged source volume.")
    print("Native volume and rollback archives were preserved.")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan, apply, or rollback a safe Compose Wine-to-Native migration.",
    )
    parser.add_argument("action", choices=("plan", "apply", "rollback"), nargs="?", default="plan")
    parser.add_argument("--project-directory", type=Path, default=Path.cwd())
    parser.add_argument("--project-name")
    parser.add_argument("--wine-compose", type=Path, default=Path("docker-compose.yml"))
    parser.add_argument("--native-compose", type=Path, default=Path("docker-compose.native.yml"))
    parser.add_argument("--state-dir", type=Path, default=Path(".conan-migration"))
    parser.add_argument("--wait-seconds", type=int, default=600)
    args = parser.parse_args(argv)
    args.project_directory = args.project_directory.resolve()
    if not args.wine_compose.is_absolute():
        args.wine_compose = (args.project_directory / args.wine_compose).resolve()
    if not args.native_compose.is_absolute():
        args.native_compose = (args.project_directory / args.native_compose).resolve()
    if not args.state_dir.is_absolute():
        args.state_dir = (args.project_directory / args.state_dir).absolute()
    if args.wait_seconds < 1:
        parser.error("--wait-seconds must be at least 1")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    for compose_file in (args.wine_compose, args.native_compose):
        if not compose_file.is_file():
            raise MigrationError(f"Compose file not found: {compose_file}")
    if not LOW_LEVEL_HELPER.is_file():
        raise MigrationError(f"low-level migration helper not found: {LOW_LEVEL_HELPER}")

    prepare_state_directory(args.state_dir)
    lock_path = args.state_dir / "operation.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(lock_fd)
        raise MigrationError("another migration operation is already running") from exc

    try:
        docker = Docker()
        wine_config = render_compose(docker, args, args.wine_compose)
        native_config = render_compose(docker, args, args.native_compose)
        layout = discover_layout(wine_config, native_config)
        state_path = args.state_dir / "state.json"
        if args.action == "plan":
            return action_plan(docker, args, layout)
        if args.action == "apply":
            return action_apply(docker, args, layout, state_path)
        return action_rollback(docker, args, layout, state_path)
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MigrationError as exc:
        print(f"Migration error: {exc}", file=sys.stderr)
        raise SystemExit(1)
