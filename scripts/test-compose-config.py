#!/usr/bin/env python3
"""Contracts for Native Compose isolation and Wine-to-Native migration."""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE_COMPOSE = ROOT / "docker-compose.native.yml"
NATIVE_BUILD_COMPOSE = ROOT / "docker-compose.native.build.yml"
MIGRATE = ROOT / "scripts" / "migrate-wine-to-native.sh"


class NativeComposeTests(unittest.TestCase):
    def test_native_compose_is_obvious_isolated_and_hardened(self) -> None:
        self.assertTrue(NATIVE_COMPOSE.is_file())
        text = NATIVE_COMPOSE.read_text(encoding="utf-8")
        self.assertIn("ghcr.io/balnaimi/conan-exiles-server:native", text)
        self.assertIn("Native Linux Experimental", text)
        self.assertIn("native-game-data", text)
        self.assertIn("native-save-data", text)
        self.assertIn("native-steam-data", text)
        self.assertIn("native-backups", text)
        self.assertIn("platform: linux/amd64", text)
        self.assertIn('com.balnaimi.conan.support-tier: "experimental"', text)
        stable = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("ghcr.io/balnaimi/conan-exiles-server:latest", stable)
        self.assertIn('com.balnaimi.conan.support-tier: "stable"', stable)
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
        self.assertIn("Native Linux Experimental", text)


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
        self.assertIn("variant: wine-stable", text)
        self.assertIn("variant: native-experimental", text)
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
        self.assertEqual(set(blocks), {"wine-stable", "native-experimental"})
        self.assertIn("channel: latest", blocks["wine-stable"])
        self.assertIn("semver_suffix: ''", blocks["wine-stable"])
        self.assertNotIn("channel: latest", blocks["native-experimental"])
        self.assertIn("channel: native", blocks["native-experimental"])
        self.assertIn("semver_suffix: -native", blocks["native-experimental"])
        self.assertIn("title: Conan Exiles Enhanced Dedicated Server — Wine Stable", blocks["wine-stable"])
        self.assertIn("title: Conan Exiles Enhanced Dedicated Server — Native Linux Experimental", blocks["native-experimental"])
        self.assertIn("support_tier: stable", blocks["wine-stable"])
        self.assertIn("support_tier: experimental", blocks["native-experimental"])
        self.assertIn("org.opencontainers.image.title=${{ matrix.title }}", text)
        self.assertIn("com.balnaimi.conan.support-tier=${{ matrix.support_tier }}", text)
        self.assertIn("file: ${{ matrix.dockerfile }}", text)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
