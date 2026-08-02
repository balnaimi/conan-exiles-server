#!/usr/bin/env python3
"""Behavior tests for the shared Conan INI renderer.

These tests protect the existing Wine/WindowsServer output while allowing the
same renderer to target Native LinuxServer paths.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "scripts" / "runtime" / "configure-server.sh"
FIXTURE = ROOT / "tests" / "fixtures" / "config" / "base.env"
ENTRYPOINT = ROOT / "entrypoint.sh"


def fixture_environment() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if not raw_line or raw_line.startswith("#"):
            continue
        key, value = raw_line.split("=", 1)
        values[key] = value
    return values


class RuntimeConfigTests(unittest.TestCase):
    maxDiff = None

    def render(self, platform: str) -> Path:
        self.assertTrue(RENDERER.is_file(), f"missing renderer: {RENDERER}")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        game_dir = Path(temporary.name) / "server"
        env = os.environ.copy()
        env.update(fixture_environment())
        env.update(
            {
                "GAME_DIR": str(game_dir),
                "CONFIG_PLATFORM": platform,
                "CONFIG_RENDER_QUIET": "1",
            }
        )
        completed = subprocess.run(
            ["bash", str(RENDERER)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"renderer failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        return game_dir / "ConanSandbox" / "Saved" / "Config" / platform

    def test_windows_renderer_preserves_core_wine_contract(self) -> None:
        config = self.render("WindowsServer")
        engine = (config / "Engine.ini").read_text(encoding="utf-8")
        settings = (config / "ServerSettings.ini").read_text(encoding="utf-8")
        game = (config / "Game.ini").read_text(encoding="utf-8")

        self.assertIn("[URL]\nPort=7787", engine)
        self.assertIn("[OnlineSubsystem]\nServerName=Wine Baseline Server", engine)
        self.assertIn("ServerPassword=join secret #1", engine)
        self.assertIn("MaxClientRate=25000", engine)
        self.assertIn("AdminPassword=admin secret", settings)
        self.assertIn("PVPEnabled=True", settings)
        self.assertIn("serverRegion=2", settings)
        self.assertIn("ServerModList=3722881816,3720904511", settings)
        self.assertIn("[/Script/Engine.GameSession]\nMaxPlayers=17", game)
        self.assertIn("[RconPlugin]\nRconEnabled=True", game)
        self.assertIn("RconPassword=rcon secret", game)
        self.assertIn("RconPort=25675", game)
        for path in (config / "Engine.ini", config / "ServerSettings.ini", config / "Game.ini"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_renderer_rejects_multiline_ini_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = os.environ.copy()
            env.update(
                {
                    "GAME_DIR": temporary,
                    "CONFIG_PLATFORM": "LinuxServer",
                    "SERVER_NAME": "safe-name\ninjected=value",
                    "CONFIG_RENDER_QUIET": "1",
                }
            )
            completed = subprocess.run(
                ["bash", str(RENDERER)],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("single-line", completed.stderr)

    def test_linux_renderer_uses_linuxserver_path_with_same_contract(self) -> None:
        config = self.render("LinuxServer")
        self.assertTrue((config / "Engine.ini").is_file())
        self.assertTrue((config / "ServerSettings.ini").is_file())
        self.assertTrue((config / "Game.ini").is_file())
        self.assertFalse(
            (config.parents[0] / "WindowsServer").exists(),
            "Linux rendering must not create a WindowsServer directory",
        )

    def test_renderer_rejects_unknown_platform(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        env = os.environ.copy()
        env.update(
            {
                "GAME_DIR": temporary.name,
                "CONFIG_PLATFORM": "UntrustedPath",
                "CONFIG_RENDER_QUIET": "1",
            }
        )
        completed = subprocess.run(
            ["bash", str(RENDERER)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("CONFIG_PLATFORM", completed.stderr)

    def test_wine_entrypoint_delegates_to_shared_renderer(self) -> None:
        text = ENTRYPOINT.read_text(encoding="utf-8")
        self.assertIn("scripts/runtime/configure-server.sh", text)
        self.assertIn("CONFIG_PLATFORM=WindowsServer", text)

    def test_wine_entrypoint_uses_shared_secrets_and_atomic_mod_installer(self) -> None:
        text = ENTRYPOINT.read_text(encoding="utf-8")
        self.assertIn("scripts/runtime/secrets.sh", text)
        self.assertIn("resolve_server_secrets", text)
        self.assertIn("scripts/runtime/install-mods.sh", text)
        self.assertIn("install_mods_atomic", text)
        self.assertNotIn(": > \"$modlist_file\"", text)

    def test_native_entrypoint_uses_shared_secrets_and_atomic_mod_installer(self) -> None:
        text = (ROOT / "scripts" / "native" / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("scripts/runtime/secrets.sh", text)
        self.assertIn("resolve_server_secrets", text)
        self.assertIn("scripts/runtime/install-mods.sh", text)
        self.assertIn("restore-required-workshop-ids", text)
        self.assertIn('if [ -e "$restore_mod_marker" ] || [ -L "$restore_mod_marker" ]', text)
        self.assertIn("SERVER_MOD_LIST", text)
        self.assertIn("install_mods_atomic", text)
        self.assertIn('rm -f -- "$restore_mod_marker"', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
