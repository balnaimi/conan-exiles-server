#!/usr/bin/env python3
"""Behavior contracts for unified backup/restore and safe diagnostics."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKUP_MODULE = ROOT / "scripts" / "conan_backup.py"
DOCTOR_MODULE = ROOT / "scripts" / "conan_doctor.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UnifiedArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "saved" / "game_0.db"
        self.database.parent.mkdir()
        connection = sqlite3.connect(self.database)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE world (value TEXT)")
        connection.execute("INSERT INTO world VALUES ('original')")
        connection.commit()
        connection.close()
        self.backups = self.root / "backups"

    def module(self):
        self.assertTrue(BACKUP_MODULE.is_file(), f"missing {BACKUP_MODULE}")
        return load_module(BACKUP_MODULE, "conan_backup_test")

    def test_create_uses_sqlite_snapshot_and_verified_common_format(self) -> None:
        module = self.module()
        archive = module.create_world_archive(self.database, self.backups, runtime="wine")
        self.assertTrue(archive.is_file())
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
        details = module.verify_archive(archive)
        self.assertEqual(details["runtime"], "wine")
        self.assertEqual(details["world_database"], "game_0.db")
        self.assertEqual(details["integrity"], "ok")
        self.assertNotIn("-wal", "\n".join(details["members"]))
        self.assertNotIn("-shm", "\n".join(details["members"]))

    def test_host_verification_workspace_uses_archive_filesystem(self) -> None:
        module = self.module()
        archive = module.create_world_archive(self.database, self.backups, runtime="wine")
        real_temporary_directory = tempfile.TemporaryDirectory
        requested_dirs: list[Path | None] = []

        def temporary_directory(*args, **kwargs):
            requested_dirs.append(Path(kwargs["dir"]) if kwargs.get("dir") else None)
            return real_temporary_directory(*args, **kwargs)

        with patch.object(module.tempfile, "TemporaryDirectory", side_effect=temporary_directory):
            module.verify_archive(archive)
        self.assertEqual(requested_dirs, [archive.parent])

    def test_verification_rejects_insufficient_workspace_before_extraction(self) -> None:
        module = self.module()
        archive = module.create_world_archive(self.database, self.backups, runtime="wine")
        with (
            patch.object(
                module.shutil,
                "disk_usage",
                return_value=type("DiskUsage", (), {"free": 0})(),
            ),
            patch.object(module.shutil, "copyfileobj") as copy_file,
        ):
            with self.assertRaisesRegex(module.BackupError, "free space"):
                module.verify_archive(archive)
        copy_file.assert_not_called()

    def test_archive_member_limit_is_streamed_without_getmembers(self) -> None:
        module = self.module()
        archive = self.backups / "many-members.tar.gz"
        self.backups.mkdir()
        with tarfile.open(archive, "w:gz") as bundle:
            for number in range(12):
                member = tarfile.TarInfo(f"entry-{number}")
                member.size = 0
                bundle.addfile(member, io.BytesIO())
        observed_member_cache: list[int] = []
        original_next = module.tarfile.TarFile.next

        def tracked_next(bundle):
            member = original_next(bundle)
            observed_member_cache.append(len(bundle.members))
            return member

        with (
            patch.object(module, "MAX_MEMBERS", 10),
            patch.object(module.tarfile.TarFile, "getmembers", side_effect=AssertionError("unbounded")),
            patch.object(module.tarfile.TarFile, "next", tracked_next),
        ):
            with self.assertRaisesRegex(module.BackupError, "too many members"):
                module.verify_archive(archive)
        self.assertLessEqual(max(observed_member_cache), 1)

    def test_normalized_duplicate_is_rejected_before_extraction(self) -> None:
        module = self.module()
        valid = module.create_world_archive(self.database, self.backups, runtime="wine")
        duplicate = self.backups / "normalized-duplicate.tar.gz"
        metadata_payload = b""
        with tarfile.open(valid, "r:gz") as source, tarfile.open(duplicate, "w:gz") as target:
            for member in source:
                if member.isfile():
                    extracted = source.extractfile(member)
                    if extracted is None:
                        self.fail(f"could not read fixture member {member.name}")
                    payload = extracted.read()
                else:
                    payload = b""
                if member.name == "metadata.json":
                    metadata_payload = payload
                target.addfile(member, io.BytesIO(payload) if member.isfile() else None)
            alias = tarfile.TarInfo("./metadata.json")
            alias.size = len(metadata_payload)
            target.addfile(alias, io.BytesIO(metadata_payload))
        with patch.object(module.shutil, "copyfileobj") as copy_file:
            with self.assertRaisesRegex(module.BackupError, "Duplicate archive member"):
                module.verify_archive(duplicate)
        copy_file.assert_not_called()

    def test_verify_rejects_tampered_archive(self) -> None:
        module = self.module()
        archive = module.create_world_archive(self.database, self.backups, runtime="wine")
        payload = bytearray(archive.read_bytes())
        payload[len(payload) // 2] ^= 0x01
        archive.write_bytes(payload)
        with self.assertRaises(module.BackupError):
            module.verify_archive(archive)

    def test_list_backups_ignores_symlinks_and_can_verify(self) -> None:
        module = self.module()
        archive = module.create_world_archive(self.database, self.backups, runtime="wine")
        (self.backups / "linked.tar.gz").symlink_to(archive)
        rows = module.list_backups(self.backups, verify=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], archive.name)
        self.assertEqual(rows[0]["runtime"], "wine")
        self.assertEqual(rows[0]["verification"], "ok")

    def test_restore_dry_run_does_not_mutate_and_apply_is_atomic(self) -> None:
        module = self.module()
        archive = module.create_world_archive(self.database, self.backups, runtime="wine")
        target = self.root / "target" / "game_0.db"
        target.parent.mkdir()
        connection = sqlite3.connect(target)
        connection.execute("CREATE TABLE old (value TEXT)")
        connection.commit()
        connection.close()
        old_hash = module.sha256_file(target)
        old_stat = target.stat()
        details = module.restore_world_archive(archive, target, apply=False)
        self.assertEqual(details["action"], "dry-run")
        self.assertEqual(module.sha256_file(target), old_hash)
        details = module.restore_world_archive(archive, target, apply=True)
        self.assertEqual(details["action"], "restored")
        restored_stat = target.stat()
        self.assertEqual((restored_stat.st_uid, restored_stat.st_gid), (old_stat.st_uid, old_stat.st_gid))
        self.assertEqual(stat.S_IMODE(restored_stat.st_mode), stat.S_IMODE(old_stat.st_mode) | 0o600)
        connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            self.assertEqual(connection.execute("SELECT value FROM world").fetchone()[0], "original")
        finally:
            connection.close()

    def test_restore_sidecar_cleanup_failure_keeps_old_database(self) -> None:
        module = self.module()
        archive = module.create_world_archive(self.database, self.backups, runtime="wine")
        target = self.root / "target-crash" / "game_0.db"
        target.parent.mkdir()
        connection = sqlite3.connect(target)
        connection.execute("CREATE TABLE old (value TEXT)")
        connection.execute("INSERT INTO old VALUES ('preserved')")
        connection.commit()
        connection.close()
        (target.parent / "game_0.db-wal").write_bytes(b"")
        real_unlink = module.os.unlink

        def fail_sidecar(path, *args, **kwargs):
            if str(path).endswith("game_0.db-wal"):
                raise OSError("injected sidecar cleanup failure")
            return real_unlink(path, *args, **kwargs)

        with patch.object(module.os, "unlink", side_effect=fail_sidecar):
            with self.assertRaises(OSError):
                module.restore_world_archive(archive, target, apply=True)
        connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            self.assertEqual(connection.execute("SELECT value FROM old").fetchone()[0], "preserved")
        finally:
            connection.close()

    def test_compare_archive_detects_committed_wal_changes(self) -> None:
        module = self.module()
        source = self.root / "wal-source.db"
        connection = sqlite3.connect(source)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE world (value TEXT)")
        connection.execute("INSERT INTO world VALUES ('original')")
        connection.commit()
        connection.close()
        archive = module.create_world_archive(source, self.backups, runtime="wine")
        target = self.root / "wal-target" / "game_0.db"
        target.parent.mkdir()
        module.restore_world_archive(archive, target, apply=True)
        reader = sqlite3.connect(target)
        writer = sqlite3.connect(target)
        try:
            reader.execute("BEGIN")
            self.assertEqual(reader.execute("SELECT value FROM world").fetchone()[0], "original")
            writer.execute("UPDATE world SET value = 'live-newer'")
            writer.commit()
            self.assertTrue((target.parent / "game_0.db-wal").exists())
            result = module.compare_archive_target(archive, target)
            self.assertFalse(result["matches"])
        finally:
            writer.close()
            reader.close()

    def test_restore_rejects_archive_replaced_after_verification(self) -> None:
        module = self.module()
        archive_a = module.create_world_archive(self.database, self.backups, runtime="wine")
        other_db = self.root / "other.db"
        connection = sqlite3.connect(other_db)
        connection.execute("CREATE TABLE world (value TEXT)")
        connection.execute("INSERT INTO world VALUES ('other')")
        connection.commit()
        connection.close()
        other_backups = self.root / "other-backups"
        archive_b = module.create_world_archive(other_db, other_backups, runtime="wine")
        details = module.verify_archive(archive_a)
        replacement = archive_a.with_suffix(".replacement")
        replacement.write_bytes(archive_b.read_bytes())
        os.replace(replacement, archive_a)
        target = self.root / "swap-target" / "game_0.db"
        target.parent.mkdir()
        target.write_bytes(b"preserve")
        with self.assertRaisesRegex(module.BackupError, "changed after verification"):
            module.restore_world_archive(
                archive_a,
                target,
                apply=True,
                expected_archive_sha256=details["archive_sha256"],
            )
        self.assertEqual(target.read_bytes(), b"preserve")

    def test_restore_rejects_symlink_target_without_touching_victim(self) -> None:
        module = self.module()
        archive = module.create_world_archive(self.database, self.backups, runtime="wine")
        victim = self.root / "victim.db"
        victim.write_text("keep", encoding="utf-8")
        target = self.root / "target.db"
        target.symlink_to(victim)
        with self.assertRaises(module.BackupError):
            module.restore_world_archive(archive, target, apply=True)
        self.assertTrue(target.is_symlink())
        self.assertEqual(victim.read_text(encoding="utf-8"), "keep")

    def test_compose_discovery_selects_exact_runtime_save_volume(self) -> None:
        module = self.module()
        wine = {
            "name": "fixture",
            "services": {
                "conan": {
                    "labels": {"com.balnaimi.conan.runtime": "wine"},
                    "image": "wine:test",
                    "environment": {"QUERY_PORT": "27115"},
                    "ports": [{"target": 27115, "published": "30115", "protocol": "udp"}],
                    "volumes": [
                        {"type": "volume", "source": "game", "target": "/conanexiles"},
                        {
                            "type": "volume",
                            "source": "saved",
                            "target": "/conanexiles/ConanSandbox/Saved",
                        },
                    ],
                }
            },
            "volumes": {"game": {"name": "fixture_game"}, "saved": {"name": "fixture_saved"}},
        }
        native = {
            "name": "fixture",
            "services": {
                "conan-native": {
                    "labels": {"com.balnaimi.conan.runtime": "native-linux"},
                    "image": "native:test",
                    "volumes": [
                        {
                            "type": "volume",
                            "source": "native-saved",
                            "target": "/data/server/ConanSandbox/Saved",
                        }
                    ],
                }
            },
            "volumes": {"native-saved": {"name": "fixture_native_saved"}},
        }
        wine_layout = module.discover_runtime_layout(wine, native, "wine")
        native_layout = module.discover_runtime_layout(wine, native, "native")
        self.assertEqual(wine_layout.save_volume, "fixture_saved")
        self.assertEqual(wine_layout.service, "conan")
        self.assertEqual(wine_layout.helper_image, module.HELPER_IMAGE)
        self.assertEqual(wine_layout.query_port, 30115)
        self.assertEqual(native_layout.save_volume, "fixture_native_saved")
        self.assertEqual(native_layout.service, "conan-native")

        native["volumes"]["native-saved"]["name"] = "fixture_saved"
        with self.assertRaises(module.BackupError):
            module.discover_runtime_layout(wine, native, "native")

    def test_compose_discovery_rejects_bind_mounted_world(self) -> None:
        module = self.module()
        wine = {
            "name": "fixture",
            "services": {
                "conan": {
                    "labels": {"com.balnaimi.conan.runtime": "wine"},
                    "volumes": [
                        {
                            "type": "bind",
                            "source": "/tmp/saved",
                            "target": "/conanexiles/ConanSandbox/Saved",
                        }
                    ],
                }
            },
            "volumes": {},
        }
        native = {
            "name": "fixture",
            "services": {
                "conan-native": {
                    "labels": {"com.balnaimi.conan.runtime": "native-linux"},
                    "image": "native:test",
                    "volumes": [],
                }
            },
            "volumes": {},
        }
        with self.assertRaises(module.BackupError):
            module.discover_runtime_layout(wine, native, "wine")

    def test_foreign_or_unlabelled_volume_is_rejected(self) -> None:
        module = self.module()

        class FakeDocker:
            def __init__(self, labels):
                self.labels = labels

            def run(self, command, *, check=True, capture=True):
                del check, capture
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps([{"Name": "fixture_saved", "Labels": self.labels}]),
                    "",
                )

        for labels in (
            None,
            {"com.docker.compose.project": "other", "com.docker.compose.volume": "saved"},
            {"com.docker.compose.project": "fixture", "com.docker.compose.volume": "other"},
        ):
            with self.subTest(labels=labels):
                with self.assertRaises(module.BackupError):
                    module.volume_exists(FakeDocker(labels), "fixture_saved", "fixture", "saved")
        self.assertTrue(
            module.volume_exists(
                FakeDocker(
                    {
                        "com.docker.compose.project": "fixture",
                        "com.docker.compose.volume": "saved",
                    }
                ),
                "fixture_saved",
                "fixture",
                "saved",
            )
        )

    def test_compose_create_helper_mounts_world_readonly(self) -> None:
        module = self.module()
        layout = module.RuntimeLayout("fixture", "wine", "conan", "fixture_saved", "native:test")
        backup_dir = self.root / "host-backups"
        commands: list[list[str]] = []

        class FakeDocker:
            def run(self, command, *, check=True, capture=True):
                del check, capture
                commands.append(list(command))
                backup_dir.mkdir(parents=True, exist_ok=True)
                (backup_dir / "created.tar.gz").write_bytes(b"fixture")
                return subprocess.CompletedProcess(command, 0, "/backups/created.tar.gz\n", "")

        with patch.object(module, "verify_archive", return_value={"runtime": "wine", "archive_sha256": "a" * 64}):
            archive = module.create_compose_backup(FakeDocker(), layout, backup_dir)
        self.assertEqual(archive, backup_dir / "created.tar.gz")
        command = commands[0]
        self.assertIn("type=volume,src=fixture_saved,dst=/source,readonly", command)
        self.assertIn("archive-create", command)
        self.assertIn("--network", command)
        self.assertIn("no-new-privileges", command)
        self.assertIn("--read-only", command)
        self.assertIn("type=volume,dst=/tmp,volume-nocopy", command)
        self.assertNotIn("--tmpfs", command)
        self.assertNotIn("--apply", command)

    def test_compose_restore_defaults_to_readonly_dry_run(self) -> None:
        module = self.module()
        layout = module.RuntimeLayout("fixture", "wine", "conan", "fixture_saved", "native:test")
        archive = self.root / "archive.tar.gz"
        archive.write_bytes(b"fixture")
        commands: list[list[str]] = []

        class FakeDocker:
            def run(self, command, *, check=True, capture=True):
                del check, capture
                commands.append(list(command))
                return subprocess.CompletedProcess(command, 0, '{"action":"dry-run"}\n', "")

        module.run_compose_restore(FakeDocker(), layout, archive, apply=False)
        command = commands[0]
        self.assertIn("type=volume,src=fixture_saved,dst=/target,readonly", command)
        self.assertIn("archive-restore", command)
        self.assertNotIn("--apply", command)

    def test_docker_bind_source_rejects_symlink_in_any_path_component(self) -> None:
        module = self.module()
        real = self.root / "real"
        real.mkdir()
        linked = self.root / "linked"
        linked.symlink_to(real, target_is_directory=True)
        candidate = linked / "archive-dir"
        candidate.mkdir()
        with self.assertRaises(module.BackupError):
            module._safe_mount_source(candidate, "backup directory")

    def test_operation_lock_rejects_concurrent_mutation(self) -> None:
        module = self.module()
        with module.operation_lock(self.backups):
            with self.assertRaises(module.BackupError):
                with module.operation_lock(self.backups):
                    self.fail("second operation lock unexpectedly succeeded")

    def test_cli_create_rejects_pending_state_before_runtime_detection(self) -> None:
        module = self.module()
        backup_dir = self.root / "cli-pending"
        backup_dir.mkdir(mode=0o700)
        state_path = backup_dir / "restore-state.json"
        state_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "operation": "restore",
                    "status": "recovery-required",
                    "runtime": "wine",
                    "volume": "fixture_saved",
                    "pre_restore_archive": "pre-restore.tar.gz",
                    "was_running": True,
                }
            ),
            encoding="utf-8",
        )
        state_path.chmod(0o600)
        argv = [
            str(BACKUP_MODULE),
            "--project-directory",
            str(self.root),
            "--backup-dir",
            str(backup_dir),
            "create",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(module, "Docker") as docker,
        ):
            self.assertEqual(module.main(), 1)
        docker.assert_not_called()
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["status"], "recovery-required")

    def test_empty_operation_state_is_rejected_before_service_activity(self) -> None:
        module = self.module()
        layout = module.RuntimeLayout("fixture", "wine", "conan", "fixture_saved", "native:test")
        backup_dir = self.root / "empty-state"
        backup_dir.mkdir(mode=0o700)
        state_path = backup_dir / "restore-state.json"
        state_path.write_text("{}", encoding="utf-8")
        state_path.chmod(0o600)
        with (
            patch.object(module, "volume_exists") as volume_exists,
            patch.object(module, "service_activity") as activity,
        ):
            with self.assertRaisesRegex(module.BackupError, "recovery state"):
                module.create_offline_backup(
                    module.Docker(),
                    layout,
                    backup_dir,
                    compose_file=self.root / "docker-compose.yml",
                    project_directory=self.root,
                    project_name="fixture",
                )
        volume_exists.assert_not_called()
        activity.assert_not_called()
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), {})

    def test_create_refuses_to_overwrite_pending_restore_recovery_state(self) -> None:
        module = self.module()
        layout = module.RuntimeLayout("fixture", "wine", "conan", "fixture_saved", "native:test")
        backup_dir = self.root / "pending-state"
        state_path = backup_dir / "restore-state.json"
        backup_dir.mkdir(mode=0o700)
        state_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "operation": "restore",
                    "status": "prepared",
                    "runtime": "wine",
                    "volume": "fixture_saved",
                    "archive": "requested.tar.gz",
                    "pre_restore_archive": "pre-restore.tar.gz",
                    "was_running": True,
                }
            ),
            encoding="utf-8",
        )
        state_path.chmod(0o600)
        with (
            patch.object(module, "volume_exists", return_value=True),
            patch.object(module, "service_activity") as activity,
            patch.object(module, "create_compose_backup"),
        ):
            with self.assertRaisesRegex(module.BackupError, "recovery state"):
                module.create_offline_backup(
                    object(),
                    layout,
                    backup_dir,
                    compose_file=self.root / "compose.yml",
                    project_directory=self.root,
                    project_name="fixture",
                )
        activity.assert_not_called()
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["status"], "prepared")

    def test_offline_backup_stops_proves_and_restores_prior_running_state(self) -> None:
        module = self.module()
        layout = module.RuntimeLayout("fixture", "wine", "conan", "fixture_saved", "native:test")
        events: list[str] = []
        archive = self.root / "backups" / "created.tar.gz"
        archive.parent.mkdir()
        archive.write_bytes(b"fixture")
        with (
            patch.object(module, "volume_exists", return_value=True),
            patch.object(module, "service_activity", return_value=(True, [])),
            patch.object(
                module,
                "stop_and_prove",
                side_effect=lambda *args, **kwargs: events.append("stop"),
            ),
            patch.object(
                module,
                "create_compose_backup",
                side_effect=lambda *args, **kwargs: events.append("snapshot") or archive,
            ),
            patch.object(
                module,
                "start_and_verify",
                side_effect=lambda *args, **kwargs: events.append("start"),
            ),
        ):
            result = module.create_offline_backup(
                object(),
                layout,
                archive.parent,
                compose_file=self.root / "docker-compose.yml",
                project_directory=self.root,
                project_name=None,
            )
        self.assertEqual(result, archive)
        self.assertEqual(events, ["stop", "snapshot", "start"])

    def test_comparison_failure_after_quiescence_restarts_service(self) -> None:
        module = self.module()
        layout = module.RuntimeLayout("fixture", "wine", "conan", "fixture_saved", "native:test")
        requested = self.root / "requested.tar.gz"
        requested.write_bytes(b"requested")
        with (
            patch.object(module, "verify_archive", return_value={"runtime": "wine", "archive_sha256": "a" * 64}),
            patch.object(module, "volume_exists", return_value=True),
            patch.object(module, "service_activity", return_value=(True, [])),
            patch.object(module, "stop_and_prove"),
            patch.object(
                module,
                "target_matches_archive",
                side_effect=module.BackupError("comparison failed"),
            ),
            patch.object(module, "start_and_verify") as restart,
        ):
            with self.assertRaisesRegex(module.BackupError, "comparison failed"):
                module.apply_compose_restore(
                    object(),
                    layout,
                    requested,
                    self.backups,
                    compose_file=self.root / "docker-compose.yml",
                    project_directory=self.root,
                    project_name=None,
                )
        restart.assert_called_once()
        state = module._read_restore_state(self.backups / "restore-state.json")
        self.assertIsNotNone(state)
        self.assertEqual(state["status"], "rolled-back")

    def test_restore_apply_rolls_back_pre_restore_backup_when_start_fails(self) -> None:
        module = self.module()
        layout = module.RuntimeLayout("fixture", "wine", "conan", "fixture_saved", "native:test")
        backup_dir = self.root / "backups"
        backup_dir.mkdir()
        requested = backup_dir / "requested.tar.gz"
        requested.write_bytes(b"requested")
        previous = backup_dir / "pre-restore.tar.gz"
        previous.write_bytes(b"previous")
        restored: list[Path] = []
        starts = iter((module.BackupError("readiness failed"), None))

        def start(*args, **kwargs):
            del args, kwargs
            outcome = next(starts)
            if outcome:
                raise outcome

        def restore(*args, **kwargs):
            del kwargs
            restored.append(args[2])
            return {"action": "restored"}

        with (
            patch.object(module, "verify_archive", return_value={"runtime": "wine", "archive_sha256": "a" * 64}),
            patch.object(module, "volume_exists", return_value=True),
            patch.object(module, "target_matches_archive", return_value=False),
            patch.object(module, "service_activity", return_value=(True, [])),
            patch.object(module, "stop_and_prove"),
            patch.object(module, "create_compose_backup", return_value=previous),
            patch.object(module, "run_compose_restore", side_effect=restore),
            patch.object(module, "start_and_verify", side_effect=start),
        ):
            with self.assertRaisesRegex(module.BackupError, "rolled back"):
                module.apply_compose_restore(
                    object(),
                    layout,
                    requested,
                    backup_dir,
                    compose_file=self.root / "docker-compose.yml",
                    project_directory=self.root,
                    project_name=None,
                )
        self.assertEqual(restored, [requested, previous])
        state = json.loads((backup_dir / "restore-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "rolled-back")

    def test_recover_stopping_state_restarts_prior_running_service(self) -> None:
        module = self.module()
        layout = module.RuntimeLayout("test", "wine", "conan", "wine-save", "helper:latest")
        state_path = self.backups / "restore-state.json"
        module.write_restore_state(
            state_path,
            {
                "schema_version": 1,
                "operation": "backup",
                "status": "stopping",
                "runtime": "wine",
                "volume": "wine-save",
                "was_running": True,
            },
        )
        with (
            patch.object(module, "volume_exists", return_value=True),
            patch.object(module, "start_and_verify") as restart,
        ):
            result = module.recover_compose_operation(
                object(),
                layout,
                self.backups,
                compose_file=Path("compose.yml"),
                project_directory=self.root,
                project_name="test",
            )
        restart.assert_called_once()
        self.assertEqual(result["action"], "service-state-recovered")
        self.assertEqual(module._read_restore_state(state_path)["status"], "rolled-back")

    def test_quiescence_failure_records_safe_rolled_back_state(self) -> None:
        module = self.module()
        archive = self.root / "requested.tar.gz"
        archive.write_bytes(b"fixture")
        restart = patch.object(module, "start_and_verify")
        with (
            patch.object(module, "verify_archive", return_value={"runtime": "wine", "archive_sha256": "a" * 64}),
            patch.object(module, "volume_exists", return_value=True),
            patch.object(module, "target_matches_archive", return_value=False),
            patch.object(module, "service_activity", return_value=(True, [])),
            patch.object(module, "stop_and_prove", side_effect=module.BackupError("still in use")),
            restart as restart_mock,
        ):
            with self.assertRaises(module.BackupError):
                module.apply_compose_restore(
                    object(),
                    module.RuntimeLayout("test", "wine", "conan", "wine-save", "helper:latest"),
                    archive,
                    self.backups,
                    compose_file=self.root / "wine.yml",
                    project_directory=self.root,
                    project_name="test",
                )
        restart_mock.assert_called_once()
        state = module._read_restore_state(self.backups / "restore-state.json")
        self.assertIsNotNone(state)
        self.assertEqual(state["status"], "rolled-back")

    def test_restore_state_rejects_dangling_symlink(self) -> None:
        module = self.module()
        state = self.root / "restore-state.json"
        state.symlink_to(self.root / "missing")
        with self.assertRaises(module.BackupError):
            module.write_restore_state(state, {"status": "planned"})
        self.assertTrue(state.is_symlink())


class DoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def module(self):
        self.assertTrue(DOCTOR_MODULE.is_file(), f"missing {DOCTOR_MODULE}")
        return load_module(DOCTOR_MODULE, "conan_doctor_test")

    def test_command_runner_rejects_excessive_stderr(self) -> None:
        module = self.module()
        with patch.object(module, "MAX_COMMAND_OUTPUT", 1024):
            with self.assertRaisesRegex(RuntimeError, "safety limit"):
                module.default_run(
                    ["python3", "-c", "import os; os.write(2, b'x' * 2048)"]
                )

    def test_backup_scan_caps_total_directory_entries(self) -> None:
        module = self.module()
        visited = 0

        class Entry:
            name = "unrelated-file"

        class Scan:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def __iter__(self):
                nonlocal visited
                for _ in range(20_000):
                    visited += 1
                    yield Entry()

        with (
            patch.object(module.os, "scandir", return_value=Scan()),
            patch.object(module.Path, "is_symlink", return_value=False),
            patch.object(module.Path, "is_dir", return_value=True),
        ):
            summary = module._backup_summary(self.root)
        self.assertLessEqual(visited, 10_001)
        self.assertEqual(summary["collector_status"], "partial")

    def test_hostile_numeric_process_fields_do_not_crash(self) -> None:
        module = self.module()
        huge = "9" * 5000

        def fake_run(command: list[str]):
            if command[:2] == ["docker", "inspect"]:
                return '{"status":"running","health":"healthy","oom_killed":false,"restart_count":0,"exit_code":0}'
            if command[:3] == ["docker", "stats", "--no-stream"]:
                return json.dumps({"PIDs": huge, "CPUPerc": "0%", "MemPerc": "0%"})
            if command[:2] == ["docker", "top"]:
                return f"PID PPID S COMMAND\n{huge} 1 S python3"
            if command[:2] == ["docker", "port"]:
                return ""
            raise RuntimeError("unexpected")

        details = module._container_details(fake_run, "abcdef123456")
        self.assertEqual(details["resources"]["pids"], 0)
        self.assertEqual(details["processes"], [])
        self.assertIsNone(module._size_bytes(huge + "B"))

    def test_a2s_probe_count_is_globally_bounded(self) -> None:
        module = self.module()
        calls: list[int] = []

        class Loader:
            def exec_module(self, target):
                del target

        class Spec:
            loader = Loader()

        class A2S:
            @staticmethod
            def query_counts(host, port, timeout):
                del host, timeout
                calls.append(port)
                raise OSError("timeout")

        ports = [
            {"protocol": "udp", "host_port": port, "container_port": 10000 + port}
            for port in range(1, 129)
        ]
        with (
            patch.object(module.importlib.util, "spec_from_file_location", return_value=Spec()),
            patch.object(module.importlib.util, "module_from_spec", return_value=A2S()),
        ):
            result = module._a2s_summary(ports)
        self.assertLessEqual(len(calls), 4)
        self.assertEqual(result["attempted_ports"], len(calls))

    def test_offline_report_is_useful_without_docker(self) -> None:
        module = self.module()

        def unavailable(command: list[str]):
            raise FileNotFoundError(command[0])

        report = module.collect_report(run=unavailable, environ={})
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["docker"]["available"], False)
        self.assertIn("host", report)
        self.assertIn("checks", report)

    def test_report_never_includes_environment_or_malicious_secret_values(self) -> None:
        module = self.module()
        secret = "DO_NOT_LEAK_super_secret_password"

        def fake_run(command: list[str]):
            if command[:2] == ["docker", "version"]:
                return '{"Client":"29","Server":"29"}'
            if command[:2] == ["docker", "compose"]:
                return "Docker Compose version v5"
            if command[:2] == ["docker", "ps"]:
                return json.dumps(
                    {
                        "ID": "abc123",
                        "Names": "conan",
                        "Image": "example:latest",
                        "State": "running",
                        "Status": "Up",
                        "Labels": f"password={secret},com.balnaimi.conan.runtime=wine",
                    }
                )
            raise RuntimeError("unexpected command")

        report = module.collect_report(run=fake_run, environ={"ADMIN_PASSWORD": secret, "TOKEN": secret})
        encoded = json.dumps(report, sort_keys=True)
        self.assertNotIn(secret, encoded)
        self.assertNotIn("ADMIN_PASSWORD", encoded)
        self.assertNotIn("TOKEN", encoded)
        self.assertEqual(report["runtime"]["name"], "wine")

    def test_hostile_docker_fields_cannot_exfiltrate_free_form_text(self) -> None:
        module = self.module()
        secret = "LEAK_ME_SECRET_123"

        def fake_run(command: list[str]):
            if command[:2] == ["docker", "version"]:
                return json.dumps({"Client": {"Version": secret}, "Server": {"Version": secret}})
            if command[:3] == ["docker", "compose", "version"]:
                return secret
            if command[:2] == ["docker", "ps"]:
                template = command[command.index("--format") + 1]
                self.assertNotIn("{{json .}}", template)
                self.assertNotIn("Names", template)
                self.assertNotIn("Image", template)
                self.assertNotIn("Status", template)
                return json.dumps(
                    {
                        "id": "abcdef1234567890",
                        "runtime": "wine",
                        "Names": secret,
                        "Image": secret,
                        "Status": secret,
                    }
                )
            if command[:2] == ["docker", "inspect"]:
                return json.dumps(
                    {
                        "status": secret,
                        "health": secret,
                        "oom_killed": secret,
                        "restart_count": secret,
                        "exit_code": secret,
                    }
                )
            if command[:3] == ["docker", "stats", "--no-stream"]:
                return json.dumps(
                    {
                        "CPUPerc": secret,
                        "MemUsage": secret,
                        "MemPerc": secret,
                        "PIDs": secret,
                        "NetIO": secret,
                        "BlockIO": secret,
                    }
                )
            if command[:2] == ["docker", "top"]:
                return f"PID PPID S COMMAND\n123 1 S {secret}"
            if command[:2] == ["docker", "port"]:
                return secret
            raise RuntimeError(f"unexpected command: {command[0]}")

        report = module.collect_report(run=fake_run, environ={"TOKEN": secret})
        self.assertNotIn(secret, json.dumps(report, sort_keys=True))
        container = report["runtime"]["containers"][0]
        self.assertEqual(container["state"], "unknown")
        self.assertEqual(container["health"], "unknown")
        self.assertEqual(container["processes"], [])

    def test_output_file_is_owner_only(self) -> None:
        module = self.module()
        output = self.root / "doctor.json"
        module.write_report(output, {"schema_version": 1})
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_output_file_rejects_symlink(self) -> None:
        module = self.module()
        victim = self.root / "victim"
        victim.write_text("keep", encoding="utf-8")
        output = self.root / "doctor.json"
        output.symlink_to(victim)
        with self.assertRaises(RuntimeError):
            module.write_report(output, {"secret": "must-not-land"})
        self.assertEqual(victim.read_text(encoding="utf-8"), "keep")

    def test_output_file_rejects_symlinked_parent(self) -> None:
        module = self.module()
        safe = self.root / "safe-parent"
        safe.mkdir()
        alias = self.root / "alias-parent"
        alias.symlink_to(safe, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            module.write_report(alias / "doctor.json", {"secret": "must-not-land"})
        self.assertFalse((safe / "doctor.json").exists())

    def test_a2s_summary_omits_server_and_map_identity(self) -> None:
        module = self.module()
        self.assertEqual(
            module._a2s_summary([]),
            {"available": False, "reason": "no-published-udp-port"},
        )

    def test_a2s_counts_parser_does_not_return_identity_fields(self) -> None:
        helper = ROOT / "scripts" / "native" / "a2s-info.py"
        specification = importlib.util.spec_from_file_location("a2s_counts_test", helper)
        assert specification is not None and specification.loader is not None
        a2s = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(a2s)
        secret = b"SERVER_SECRET"
        payload = (
            b"\xff\xff\xff\xffI\x11"
            + secret + b"\x00"
            + b"MAP_SECRET\x00folder\x00game\x00"
            + b"\x01\x00"
            + bytes((7, 40, 1, ord("d"), ord("l"), 0, 1))
            + b"1.0\x00"
        )
        with patch.object(a2s, "_request", return_value=(payload, ("127.0.0.1", 7777))):
            result = a2s.query_counts("127.0.0.1", 7777, 0.1)
        self.assertEqual(result, {"players": 7, "max_players": 40, "bots": 1})
        self.assertNotIn(secret.decode(), json.dumps(result))

    def test_container_details_are_whitelisted_and_backup_names_are_omitted(self) -> None:
        module = self.module()
        secret = "NEVER_INCLUDE_THIS_SECRET"
        backup_dir = self.root / "backups"
        backup_dir.mkdir()
        (backup_dir / f"conan-wine-{secret}-world.tar.gz").write_bytes(b"x")
        commands: list[list[str]] = []

        def fake_run(command: list[str]):
            commands.append(command)
            if command[:2] == ["docker", "version"]:
                return '{"Client":{"Version":"29.6.2"},"Server":{"Version":"29.6.2"}}'
            if command[:3] == ["docker", "compose", "version"]:
                return "Docker Compose version v5.3.1"
            if command[:2] == ["docker", "ps"]:
                return json.dumps(
                    {
                        "ID": "abcdef1234567890",
                        "Names": "conan",
                        "Image": "ghcr.io/balnaimi/conan-exiles-server:latest",
                        "State": "running",
                        "Status": "Up",
                        "Labels": "com.balnaimi.conan.runtime=wine",
                    }
                )
            if command[:2] == ["docker", "inspect"]:
                template = command[command.index("--format") + 1]
                for forbidden in (".Config", "Env", ".Path", ".Args"):
                    self.assertNotIn(forbidden, template)
                return json.dumps(
                    {
                        "status": "running",
                        "health": "none",
                        "oom_killed": False,
                        "restart_count": 0,
                        "exit_code": 0,
                    }
                )
            if command[:3] == ["docker", "stats", "--no-stream"]:
                return json.dumps({"CPUPerc": "1.2%", "MemUsage": "1GiB / 16GiB", "PIDs": "42"})
            if command[:2] == ["docker", "top"]:
                return "PID PPID S COMMAND\n123 1 S wineserver"
            if command[:2] == ["docker", "port"]:
                return "27015/udp -> 0.0.0.0:27015"
            raise RuntimeError(f"unexpected command: {command}")

        report = module.collect_report(run=fake_run, environ={}, backup_dir=backup_dir)
        encoded = json.dumps(report, sort_keys=True)
        self.assertNotIn(secret, encoded)
        container = report["runtime"]["containers"][0]
        self.assertEqual(container["health"], "none")
        self.assertEqual(container["resources"]["cpu_percent"], 1.2)
        self.assertEqual(container["processes"][0]["comm"], "wineserver")
        self.assertEqual(report["backups"]["count"], 1)
        self.assertNotIn("name", report["backups"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
