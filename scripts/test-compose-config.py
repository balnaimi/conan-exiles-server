#!/usr/bin/env python3
"""Contracts for Native Compose isolation and Wine-to-Native migration."""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE_COMPOSE = ROOT / "docker-compose.native.yml"
NATIVE_BUILD_COMPOSE = ROOT / "docker-compose.native.build.yml"
MIGRATE = ROOT / "scripts" / "migrate-wine-to-native.sh"
COMPOSE_MIGRATE = ROOT / "scripts" / "migrate_compose_wine_to_native.py"
COMPOSE_MIGRATE_LAUNCHER = ROOT / "scripts" / "migrate-compose-wine-to-native.sh"


class NativeComposeTests(unittest.TestCase):
    def test_current_setup_and_contribution_surfaces_use_recommended_native_wording(self) -> None:
        paths = [
            ROOT / ".env.minimal",
            ROOT / ".env.example",
            ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md",
            ROOT / ".github" / "ISSUE_TEMPLATE" / "bug-report.yml",
            ROOT / ".github" / "ISSUE_TEMPLATE" / "feature-request.yml",
        ]
        text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        self.assertNotIn("Native Linux Experimental", text)
        self.assertNotIn("NATIVE LINUX EXPERIMENTAL", text)
        self.assertNotIn("# Experimental (docker-compose.native.yml / :native)", text)
        self.assertIn("Recommended for new servers: Native Linux", text)
        self.assertIn("Native Linux / recommended for new servers", text)

    def test_native_compose_is_obvious_isolated_and_hardened(self) -> None:
        self.assertTrue(NATIVE_COMPOSE.is_file())
        text = NATIVE_COMPOSE.read_text(encoding="utf-8")
        self.assertIn("ghcr.io/balnaimi/conan-exiles-server:native", text)
        self.assertIn("Native Linux — Recommended for New Servers", text)
        self.assertIn("native-game-data", text)
        self.assertIn("native-save-data", text)
        self.assertIn("native-steam-data", text)
        self.assertIn("native-backups", text)
        self.assertIn("platform: linux/amd64", text)
        self.assertIn('com.balnaimi.conan.support-tier: "recommended"', text)
        self.assertIn('com.balnaimi.conan.installation-track: "new-servers"', text)
        self.assertNotIn("experimental", text.lower())
        stable = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("ghcr.io/balnaimi/conan-exiles-server:latest", stable)
        self.assertIn('com.balnaimi.conan.support-tier: "existing-deployments"', stable)
        self.assertNotIn("native-game-data", stable)
        self.assertNotIn(":latest", text)
        self.assertIn("stop_grace_period: 2m", text)
        self.assertIn("no-new-privileges:true", text)
        self.assertIn("cap_drop:", text)
        self.assertIn("init: true", text)
        self.assertNotRegex(text, r"(?m)^\s*-\s*\"?\$\{RCON_PORT")
        self.assertNotIn(":25575", text)

    def test_native_build_compose_uses_dockerfile_native(self) -> None:
        self.assertTrue(NATIVE_BUILD_COMPOSE.is_file())
        text = NATIVE_BUILD_COMPOSE.read_text(encoding="utf-8")
        self.assertIn("dockerfile: Dockerfile.native", text)
        self.assertIn("Native Linux — Recommended for New Servers", text)
        self.assertNotIn("experimental", text.lower())


class CiWorkflowTests(unittest.TestCase):
    VALIDATE = ROOT / ".github" / "workflows" / "validate.yml"
    PUBLISH = ROOT / ".github" / "workflows" / "docker-publish.yml"

    def test_validate_runs_native_runtime_compose_and_image_checks(self) -> None:
        text = self.VALIDATE.read_text(encoding="utf-8")
        for marker in (
            "scripts/test-runtime-config.py",
            "scripts/test-native-runtime.py",
            "scripts/test-native-image.py",
            "scripts/test-compose-config.py",
            "docker-compose.native.yml",
            "Dockerfile.native",
            "shellcheck",
        ):
            self.assertIn(marker, text)

    def test_publish_matrix_keeps_latest_wine_and_native_separate(self) -> None:
        text = self.PUBLISH.read_text(encoding="utf-8")
        self.assertIn("variant: wine-compatible", text)
        self.assertIn("variant: native-recommended", text)
        self.assertIn("flavor: |\n            latest=false", text)
        self.assertIn("type=semver,pattern={{version}}${{ matrix.semver_suffix }}", text)
        self.assertIn("platforms: linux/amd64", text)
        blocks = {
            match.group("variant"): match.group("body")
            for match in re.finditer(
                r"^\s{10}- variant: (?P<variant>[^\n]+)\n(?P<body>(?:^\s{12}[^\n]+\n)+)",
                text,
                re.MULTILINE,
            )
        }
        self.assertEqual(set(blocks), {"wine-compatible", "native-recommended"})
        self.assertIn("channel: latest", blocks["wine-compatible"])
        self.assertIn("semver_suffix: ''", blocks["wine-compatible"])
        self.assertNotIn("channel: latest", blocks["native-recommended"])
        self.assertIn("channel: native", blocks["native-recommended"])
        self.assertIn("semver_suffix: -native", blocks["native-recommended"])
        self.assertIn("title: Conan Exiles Enhanced Dedicated Server — Wine Compatibility", blocks["wine-compatible"])
        self.assertIn("title: Conan Exiles Enhanced Dedicated Server — Native Linux Recommended", blocks["native-recommended"])
        self.assertIn("support_tier: existing-deployments", blocks["wine-compatible"])
        self.assertIn("support_tier: recommended", blocks["native-recommended"])
        self.assertIn("installation_track: new-servers", blocks["native-recommended"])
        self.assertNotIn("experimental", text.lower())
        self.assertIn("org.opencontainers.image.title=${{ matrix.title }}", text)
        self.assertIn("com.balnaimi.conan.support-tier=${{ matrix.support_tier }}", text)
        self.assertIn("com.balnaimi.conan.installation-track=${{ matrix.installation_track }}", text)
        self.assertIn("file: ${{ matrix.dockerfile }}", text)

    def test_publish_filters_runtime_changes_but_tags_build_both(self) -> None:
        text = self.PUBLISH.read_text(encoding="utf-8")
        self.assertIn("wine:", text)
        self.assertIn("native:", text)
        self.assertIn("Dockerfile", text)
        self.assertIn("Dockerfile.native", text)
        self.assertIn("scripts/runtime/**", text)
        self.assertIn("scripts/native/**", text)
        self.assertIn("startsWith(github.ref, 'refs/tags/v')", text)
        self.assertRegex(text, r"needs\.changes\.outputs\[matrix\.change_filter\]")

    def test_workflows_bound_runtime_and_scan_published_images(self) -> None:
        publish = self.PUBLISH.read_text(encoding="utf-8")
        validate = self.VALIDATE.read_text(encoding="utf-8")
        self.assertIn("concurrency:", publish)
        self.assertIn("cancel-in-progress: true", publish)
        self.assertRegex(publish, r"timeout-minutes:\s*\d+")
        self.assertIn("aquasecurity/trivy-action", publish)
        self.assertIn("CRITICAL,HIGH", publish)
        self.assertIn("ignore-unfixed: true", publish)
        self.assertIn("push-by-digest=true", publish)
        self.assertIn(
            "tags: ${{ github.event_name == 'pull_request' && format('conan-ci:{0}', matrix.variant) || '' }}",
            publish,
        )
        self.assertNotIn("tags: conan-ci:${{ matrix.variant }}", publish)
        self.assertIn("steps.build-scan.outputs.digest", publish)
        self.assertLess(
            publish.index("Scan exact image artifact for fixed high-impact vulnerabilities"),
            publish.index("Promote scanned digest to release and channel tags"),
        )
        self.assertEqual(publish.count("docker/build-push-action@"), 1)
        self.assertIn("pull-requests: read", publish)
        self.assertIn("Validate release tag against VERSION", publish)
        self.assertIn("concurrency:", validate)
        self.assertRegex(validate, r"timeout-minutes:\s*\d+")

    def test_dependency_automation_and_community_files_exist(self) -> None:
        dependabot = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        for ecosystem in ("github-actions", "docker", "pip"):
            self.assertIn(f'package-ecosystem: "{ecosystem}"', dependabot)
        for relative in (
            "SECURITY.md",
            "CONTRIBUTING.md",
            ".github/ISSUE_TEMPLATE/bug-report.yml",
            ".github/ISSUE_TEMPLATE/feature-request.yml",
            ".github/PULL_REQUEST_TEMPLATE.md",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_release_version_has_one_checked_source(self) -> None:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^[0-9]+\.[0-9]+\.[0-9]+$")
        checker = ROOT / "scripts" / "sync-project-version.py"
        self.assertTrue(checker.is_file())
        result = subprocess.run(
            ["python3", str(checker), "--check"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        publish = (ROOT / ".github" / "workflows" / "docker-publish.yml").read_text(encoding="utf-8")
        self.assertNotIn("tr -d '[:space:]'", publish)
        self.assertIn("^${version_pattern}$", publish)
        self.assertIn('version="$(cat VERSION)"', publish)
        version_gate = publish.split("- name: Validate release tag against VERSION", 1)[1].split("\n      - name:", 1)[0]
        self.assertNotIn("\n        if:", version_gate)


class WineImageSecurityTests(unittest.TestCase):
    def test_wine_image_removes_unused_vulnerable_usb_print_daemon(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("apt-get purge -y ipp-usb", dockerfile)


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.source = root / "wine"
        self.destination = root / "native"
        saved = self.source / "ConanSandbox" / "Saved"
        config = saved / "Config" / "WindowsServer"
        config.mkdir(parents=True)
        connection = sqlite3.connect(saved / "game_0.db")
        connection.execute("CREATE TABLE migration (value TEXT)")
        connection.execute("INSERT INTO migration VALUES ('ok')")
        connection.commit()
        connection.close()
        (config / "Engine.ini").write_text(
            "[OnlineSubsystem]\nServerName=Migration Test\n"
            "[OnlineSubsystemSteam]\nbUseBuildIdOverride=True\nBuildIdOverride=123\n",
            encoding="utf-8",
        )
        (config / "Game.ini").write_text("[RconPlugin]\nRconEnabled=False\n", encoding="utf-8")
        (config / "ServerSettings.ini").write_text("[ServerSettings]\nPVPEnabled=False\n", encoding="utf-8")

    def run_migration(self, *extra: str) -> subprocess.CompletedProcess[str]:
        self.assertTrue(MIGRATE.is_file(), f"missing migration tool: {MIGRATE}")
        return subprocess.run(
            ["bash", str(MIGRATE), "--source", str(self.source), "--destination", str(self.destination), *extra],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_migration_defaults_to_dry_run(self) -> None:
        result = self.run_migration()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dry-run", result.stdout.lower())
        self.assertFalse(self.destination.exists())

    def test_apply_requires_explicit_stopped_source_evidence(self) -> None:
        result = self.run_migration("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source-stopped", result.stderr)
        self.assertFalse(self.destination.exists())

    def test_apply_snapshots_world_without_activating_windows_ini(self) -> None:
        result = self.run_migration("--apply", "--source-stopped")
        self.assertEqual(result.returncode, 0, result.stderr)
        database = self.destination / "ConanSandbox" / "Saved" / "game_0.db"
        linux_config = self.destination / "ConanSandbox" / "Saved" / "Config" / "LinuxServer"
        self.assertTrue(database.is_file())
        self.assertFalse(linux_config.exists())
        self.assertTrue((self.destination / ".migration" / "README.txt").is_file())
        self.assertIn("rendered from your .env", result.stdout)
        self.assertIn("Rollback: stop Native and restart Wine", result.stdout)
        archives = list(self.destination.parent.glob("wine-pre-native-*.tar.gz"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].stat().st_mode & 0o777, 0o600)
        connection = sqlite3.connect(database)
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(connection.execute("SELECT value FROM migration").fetchone()[0], "ok")

    def test_rollback_archive_uses_exclusive_random_name(self) -> None:
        text = MIGRATE.read_text(encoding="utf-8")
        self.assertIn("mktemp", text)
        self.assertIn("wine-pre-native-", text)
        self.assertNotIn('backup_path="${destination_parent}/wine-pre-native-${timestamp}.tar.gz"', text)

    def test_apply_refuses_nonempty_destination(self) -> None:
        self.destination.mkdir(parents=True)
        (self.destination / "keep.txt").write_text("keep", encoding="utf-8")
        result = self.run_migration("--apply", "--source-stopped")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not empty", result.stderr)
        self.assertEqual((self.destination / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_apply_supports_compose_saved_root_volumes_and_external_backup_dir(self) -> None:
        root = Path(self.temporary.name)
        source_saved = root / "wine-config-volume"
        destination_saved = root / "native-save-volume"
        backup_dir = root / "migration-state"
        shutil.copytree(self.source / "ConanSandbox" / "Saved", source_saved)

        result = subprocess.run(
            [
                "bash",
                str(MIGRATE),
                "--source",
                str(source_saved),
                "--destination",
                str(destination_saved),
                "--source-saved-root",
                "--destination-saved-root",
                "--backup-dir",
                str(backup_dir),
                "--source-stopped",
                "--apply",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((destination_saved / "game_0.db").is_file())
        self.assertTrue((destination_saved / ".migration" / "README.txt").is_file())
        self.assertFalse((destination_saved / "ConanSandbox").exists())
        archives = list(backup_dir.glob("wine-pre-native-*.tar.gz"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].stat().st_mode & 0o777, 0o600)


class ComposeMigrationWrapperTests(unittest.TestCase):
    def load_module(self):
        self.assertTrue(COMPOSE_MIGRATE.is_file(), f"missing Compose migration module: {COMPOSE_MIGRATE}")
        spec = importlib.util.spec_from_file_location("compose_migration", COMPOSE_MIGRATE)
        if spec is None or spec.loader is None:
            self.fail(f"cannot import Compose migration module: {COMPOSE_MIGRATE}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def compose_fixture(runtime: str) -> dict:
        if runtime == "wine":
            return {
                "name": "fixture",
                "services": {
                    "conan": {
                        "image": "ghcr.io/balnaimi/conan-exiles-server:latest",
                        "labels": {"com.balnaimi.conan.runtime": "wine"},
                        "volumes": [
                            {"type": "volume", "source": "game-data", "target": "/conanexiles"},
                            {"type": "volume", "source": "config-data", "target": "/conanexiles/ConanSandbox/Saved"},
                        ],
                    }
                },
                "volumes": {
                    "game-data": {"name": "fixture_game-data"},
                    "config-data": {"name": "fixture_config-data"},
                },
            }
        return {
            "name": "fixture",
            "services": {
                "conan-native": {
                    "image": "ghcr.io/balnaimi/conan-exiles-server:native",
                    "labels": {"com.balnaimi.conan.runtime": "native-linux"},
                    "volumes": [
                        {"type": "volume", "source": "native-game-data", "target": "/data/server"},
                        {"type": "volume", "source": "native-save-data", "target": "/data/server/ConanSandbox/Saved"},
                    ],
                }
            },
            "volumes": {
                "native-game-data": {"name": "fixture_native-game-data"},
                "native-save-data": {"name": "fixture_native-save-data"},
            },
        }

    def test_discovers_runtime_labels_and_exact_nested_save_volumes(self) -> None:
        module = self.load_module()
        layout = module.discover_layout(self.compose_fixture("wine"), self.compose_fixture("native"))
        self.assertEqual(layout.project_name, "fixture")
        self.assertEqual(layout.wine_service, "conan")
        self.assertEqual(layout.native_service, "conan-native")
        self.assertEqual(layout.wine_save_volume, "fixture_config-data")
        self.assertEqual(layout.native_save_volume, "fixture_native-save-data")
        self.assertEqual(layout.helper_image, "ghcr.io/balnaimi/conan-exiles-server:native")

    def test_discovery_rejects_bind_mount_or_shared_save_volume(self) -> None:
        module = self.load_module()
        wine = self.compose_fixture("wine")
        native = self.compose_fixture("native")
        wine["services"]["conan"]["volumes"][1]["type"] = "bind"
        with self.assertRaises(module.MigrationError):
            module.discover_layout(wine, native)

        wine = self.compose_fixture("wine")
        native = self.compose_fixture("native")
        native["volumes"]["native-save-data"]["name"] = "fixture_config-data"
        with self.assertRaises(module.MigrationError):
            module.discover_layout(wine, native)

    def rollback_fixture(self):
        module = self.load_module()
        args = SimpleNamespace(
            project_directory=Path("/tmp/project"),
            project_name="demo",
            wine_compose=Path("/tmp/project/docker-compose.yml"),
            native_compose=Path("/tmp/project/docker-compose.native.yml"),
        )
        layout = module.MigrationLayout(
            "demo",
            "conan",
            "conan-native",
            "demo_config-data",
            "demo_native-save-data",
            "example/native:test",
        )
        return module, args, layout

    @staticmethod
    def completed(stdout: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["docker"], 0, stdout, "")

    @staticmethod
    def stub_rollback_source(module, source_hash: str = "a" * 64) -> None:
        setattr(module, "volume_exists", Mock(return_value=True))
        setattr(module, "run_helper_plan", Mock(return_value=source_hash))

    def test_rollback_requires_native_stop_command_to_succeed(self) -> None:
        module, args, layout = self.rollback_fixture()
        self.stub_rollback_source(module)
        docker = Mock()
        docker.run.side_effect = module.MigrationError("stop failed")

        with self.assertRaisesRegex(module.MigrationError, "stop failed"):
            module.stop_native_start_wine(docker, args, layout)
        self.assertEqual(docker.run.call_count, 1)

    def test_rollback_refuses_to_start_wine_while_native_is_still_running(self) -> None:
        module, args, layout = self.rollback_fixture()
        self.stub_rollback_source(module)
        docker = Mock()
        docker.run.side_effect = [
            self.completed(),
            self.completed("native-container\n"),
            self.completed('{"Status":"restarting"}'),
        ]

        with self.assertRaisesRegex(module.MigrationError, "could not prove Native stopped"):
            module.stop_native_start_wine(docker, args, layout)
        self.assertEqual(docker.run.call_count, 3)

    def test_rollback_treats_paused_native_as_active(self) -> None:
        module, args, layout = self.rollback_fixture()
        self.stub_rollback_source(module)
        docker = Mock()
        docker.run.side_effect = [
            self.completed(),
            self.completed("native-container\n"),
            self.completed('{"Status":"paused"}'),
        ]

        with self.assertRaisesRegex(module.MigrationError, "could not prove Native stopped"):
            module.stop_native_start_wine(docker, args, layout)
        self.assertEqual(docker.run.call_count, 3)

    def test_rollback_requires_wine_container_to_be_running(self) -> None:
        module, args, layout = self.rollback_fixture()
        self.stub_rollback_source(module)
        docker = Mock()
        docker.run.side_effect = [
            self.completed(),
            self.completed(),
            self.completed(),
            self.completed(),
        ]

        with self.assertRaisesRegex(module.MigrationError, "could not prove its container is running"):
            module.stop_native_start_wine(docker, args, layout)
        self.assertEqual(docker.run.call_count, 4)

    def test_rollback_succeeds_only_after_native_stops_and_wine_runs(self) -> None:
        module, args, layout = self.rollback_fixture()
        self.stub_rollback_source(module)
        docker = Mock()
        docker.run.side_effect = [
            self.completed(),
            self.completed(),
            self.completed(),
            self.completed("wine-container\n"),
        ]

        module.stop_native_start_wine(docker, args, layout)
        self.assertEqual(docker.run.call_count, 4)

    def test_rollback_refuses_missing_source_before_stopping_native(self) -> None:
        module, args, layout = self.rollback_fixture()
        setattr(module, "volume_exists", Mock(return_value=False))
        docker = Mock()

        with self.assertRaisesRegex(module.MigrationError, "source volume is missing"):
            module.stop_native_start_wine(docker, args, layout, "a" * 64)
        docker.run.assert_not_called()

    def test_rollback_refuses_changed_source_before_stopping_native(self) -> None:
        module, args, layout = self.rollback_fixture()
        self.stub_rollback_source(module, "b" * 64)
        docker = Mock()

        with self.assertRaisesRegex(module.MigrationError, "no longer matches"):
            module.stop_native_start_wine(docker, args, layout, "a" * 64)
        docker.run.assert_not_called()

    def test_apply_records_rollback_failed_when_automatic_recovery_fails(self) -> None:
        module, args, layout = self.rollback_fixture()
        docker = Mock()
        docker.run.side_effect = module.MigrationError("Wine stop failed after a partial effect")
        setattr(module, "volume_exists", Mock(return_value=True))
        setattr(module, "running_service_ids", Mock(return_value=["wine-container"]))
        setattr(module, "active_service_ids", Mock(return_value=[]))
        setattr(module, "run_helper_plan", Mock(return_value="a" * 64))
        setattr(
            module,
            "stop_native_start_wine",
            Mock(side_effect=module.MigrationError("recovery failed")),
        )

        with tempfile.TemporaryDirectory() as temporary:
            args.state_dir = Path(temporary)
            state_path = args.state_dir / "state.json"
            with self.assertRaisesRegex(module.MigrationError, "automatic rollback also failed"):
                module.action_apply(docker, args, layout, state_path)
            self.assertEqual(module.read_state(state_path)["status"], "rollback-failed")

    def test_explicit_rollback_failure_records_recovery_required_state(self) -> None:
        module, args, layout = self.rollback_fixture()
        docker = Mock()
        setattr(
            module,
            "stop_native_start_wine",
            Mock(side_effect=module.MigrationError("Native stop failed")),
        )

        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "state.json"
            module.write_state(
                state_path,
                module.state_for(layout, "native-running-pending-acceptance", "a" * 64),
            )
            with self.assertRaisesRegex(module.MigrationError, "Native stop failed"):
                module.action_rollback(docker, args, layout, state_path)
            self.assertEqual(module.read_state(state_path)["status"], "rollback-failed")

    def test_state_paths_reject_symlinks(self) -> None:
        module = self.load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            state_link = root / "state-link"
            state_link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(module.MigrationError, "must not contain symlinks"):
                module.prepare_state_directory(state_link)

            state_file = target / "real-state.json"
            state_file.write_text('{"version":1}', encoding="utf-8")
            state_file_link = root / "state.json"
            state_file_link.symlink_to(state_file)
            with self.assertRaisesRegex(module.MigrationError, "must not be a symlink"):
                module.read_state(state_file_link)

            dangling_target = root / "missing-state-target.json"
            dangling_state = target / "state.json"
            dangling_state.symlink_to(dangling_target)
            docker = Mock()
            module_args, layout = self.rollback_fixture()[1:]
            module_args.state_dir = target
            with self.assertRaisesRegex(module.MigrationError, "must not be a symlink"):
                module.action_apply(docker, module_args, layout, dangling_state)
            docker.run.assert_not_called()
            self.assertTrue(dangling_state.is_symlink())
            self.assertFalse(dangling_target.exists())

            with self.assertRaisesRegex(module.MigrationError, "must not be a symlink"):
                module.read_state(dangling_state)

            with self.assertRaisesRegex(module.MigrationError, "must not be a symlink"):
                module.write_state(dangling_state, {"version": 1})
            self.assertTrue(dangling_state.is_symlink())
            self.assertFalse(dangling_target.exists())

    def test_launcher_and_module_forbid_destructive_volume_removal(self) -> None:
        self.assertTrue(COMPOSE_MIGRATE_LAUNCHER.is_file())
        launcher = COMPOSE_MIGRATE_LAUNCHER.read_text(encoding="utf-8")
        module = COMPOSE_MIGRATE.read_text(encoding="utf-8")
        self.assertIn("exec python3", launcher)
        self.assertNotIn("down -v", launcher + module)
        self.assertNotIn("volume rm", launcher + module)
        self.assertNotIn("shell=True", module)
        for marker in ("plan", "apply", "rollback", "source volume", "Native health"):
            self.assertIn(marker, module)


if __name__ == "__main__":
    unittest.main(verbosity=2)
