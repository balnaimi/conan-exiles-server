#!/usr/bin/env python3
"""Contracts for Native Compose isolation and Wine-to-Native migration."""

from __future__ import annotations

import os
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
        self.assertIn("variant: wine", text)
        self.assertIn("channel: latest", text)
        self.assertIn("dockerfile: Dockerfile", text)
        self.assertIn("variant: native", text)
        self.assertIn("channel: native", text)
        self.assertIn("dockerfile: Dockerfile.native", text)
        self.assertIn("semver_suffix: -native", text)
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

    def test_apply_copies_world_and_translates_config_path(self) -> None:
        result = self.run_migration("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        database = self.destination / "ConanSandbox" / "Saved" / "game_0.db"
        engine = self.destination / "ConanSandbox" / "Saved" / "Config" / "LinuxServer" / "Engine.ini"
        self.assertTrue(database.is_file())
        self.assertTrue(engine.is_file())
        text = engine.read_text(encoding="utf-8")
        self.assertNotIn("BuildIdOverride", text)
        self.assertNotIn("bUseBuildIdOverride", text)
        connection = sqlite3.connect(database)
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(connection.execute("SELECT value FROM migration").fetchone()[0], "ok")

    def test_apply_refuses_nonempty_destination(self) -> None:
        self.destination.mkdir(parents=True)
        (self.destination / "keep.txt").write_text("keep", encoding="utf-8")
        result = self.run_migration("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not empty", result.stderr)
        self.assertEqual((self.destination / "keep.txt").read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main(verbosity=2)
