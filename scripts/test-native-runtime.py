#!/usr/bin/env python3
"""Unit tests for the Native Linux runtime shell components."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import struct
import subprocess
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "scripts" / "native"
PREFLIGHT = NATIVE / "preflight.sh"
INSTALLER = NATIVE / "install-server.sh"
FAKE_STEAMCMD = ROOT / "tests" / "fakes" / "steamcmd"
CPU_FIXTURES = ROOT / "tests" / "fixtures" / "cpu"


def run_script(path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.update(env)
    return subprocess.run(
        ["bash", str(path)],
        cwd=ROOT,
        env=merged,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class CpuPreflightTests(unittest.TestCase):
    def run_fixture(self, name: str) -> subprocess.CompletedProcess[str]:
        self.assertTrue(PREFLIGHT.is_file(), f"missing preflight: {PREFLIGHT}")
        return run_script(
            PREFLIGHT,
            {
                "CPU_FLAGS_FILE": str(CPU_FIXTURES / name),
                "NATIVE_PREFLIGHT_SKIP_RESOURCES": "1",
            },
        )

    def test_modern_cpu_passes_and_reports_flags(self) -> None:
        result = self.run_fixture("modern.flags")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sse4_2=yes", result.stdout)
        self.assertIn("avx=yes", result.stdout)
        self.assertIn("avx2=yes", result.stdout)

    def test_cpu_without_sse42_fails_with_virtual_cpu_guidance(self) -> None:
        result = self.run_fixture("no-sse42.flags")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sse4_2", result.stderr)
        self.assertIn("VPS", result.stderr)

    def test_sse42_without_avx2_warns_but_passes(self) -> None:
        result = self.run_fixture("sse42-no-avx2.flags")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sse4_2=yes", result.stdout)
        self.assertIn("avx2=no", result.stdout)
        self.assertIn("not a universal UE5 requirement", result.stderr)


class NativeInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.game_dir = root / "server"
        self.steam_dir = root / "steam"
        self.lock_file = root / "locks" / "steam-install.lock"

    def environment(self, **extra: str) -> dict[str, str]:
        env = {
            "GAME_DIR": str(self.game_dir),
            "STEAM_DATA_DIR": str(self.steam_dir),
            "STEAMCMD_BIN": str(FAKE_STEAMCMD),
            "STEAM_INSTALL_LOCK": str(self.lock_file),
            "NATIVE_VALIDATE_SERVER": "false",
        }
        env.update(extra)
        return env

    def test_fresh_install_creates_native_binary_and_manifest(self) -> None:
        self.assertTrue(INSTALLER.is_file(), f"missing installer: {INSTALLER}")
        result = run_script(INSTALLER, self.environment())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.game_dir / "ConanSandboxServer.sh").is_file())
        shipping = self.game_dir / "ConanSandbox/Binaries/Linux/ConanSandboxServer-Linux-Shipping"
        self.assertTrue(shipping.is_file())
        self.assertTrue(os.access(shipping, os.X_OK))
        manifest = self.steam_dir / "steamapps/appmanifest_443030.acf"
        self.assertIn('"buildid" "24383534"', manifest.read_text(encoding="utf-8"))
        self.assertIn("installed_build_id=24383534", result.stdout)

    def test_failed_update_preserves_existing_working_install(self) -> None:
        shipping = self.game_dir / "ConanSandbox/Binaries/Linux/ConanSandboxServer-Linux-Shipping"
        shipping.parent.mkdir(parents=True)
        shipping.write_text("known-good", encoding="utf-8")
        shipping.chmod(0o755)
        result = run_script(INSTALLER, self.environment(FAKE_STEAMCMD_FAIL="1"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(shipping.read_text(encoding="utf-8"), "known-good")
        self.assertIn("preserved", result.stderr)

    def test_invalid_validate_value_is_rejected_before_steamcmd(self) -> None:
        result = run_script(INSTALLER, self.environment(NATIVE_VALIDATE_SERVER="sometimes"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("NATIVE_VALIDATE_SERVER", result.stderr)
        self.assertFalse((self.game_dir / "ConanSandboxServer.sh").exists())


class SecretResolutionTests(unittest.TestCase):
    SECRETS = ROOT / "scripts" / "runtime" / "secrets.sh"

    def run_secret_shell(self, body: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        merged = os.environ.copy()
        merged.update(env)
        return subprocess.run(
            ["bash", "-c", f'set -euo pipefail; source "{self.SECRETS}"; {body}'],
            cwd=ROOT,
            env=merged,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_file_backed_secret_resolves_without_printing_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            secret_file = Path(temporary) / "rcon"
            secret_file.write_text("file-secret\n", encoding="utf-8")
            result = self.run_secret_shell(
                'resolve_secret RCON_PASSWORD; [ "$RCON_PASSWORD" = file-secret ]; printf resolved',
                {"RCON_PASSWORD_FILE": str(secret_file)},
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "resolved")
        self.assertNotIn("file-secret", result.stderr)

    def test_direct_and_file_secret_are_rejected_as_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            secret_file = Path(temporary) / "rcon"
            secret_file.write_text("file-secret\n", encoding="utf-8")
            result = self.run_secret_shell(
                "resolve_secret RCON_PASSWORD",
                {"RCON_PASSWORD": "direct-secret", "RCON_PASSWORD_FILE": str(secret_file)},
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("both", result.stderr)
        self.assertNotIn("direct-secret", result.stderr)
        self.assertNotIn("file-secret", result.stderr)

    def test_multiline_secret_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            secret_file = Path(temporary) / "rcon"
            secret_file.write_text("line-one\nline-two\n", encoding="utf-8")
            result = self.run_secret_shell(
                "resolve_secret RCON_PASSWORD",
                {"RCON_PASSWORD_FILE": str(secret_file)},
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("one logical line", result.stderr)


class AtomicModInstallTests(unittest.TestCase):
    INSTALL_MODS = ROOT / "scripts" / "runtime" / "install-mods.sh"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.game_dir = root / "server"
        self.steam_dir = root / "steam"
        self.mods_dir = self.game_dir / "ConanSandbox" / "Mods"
        self.workshop_root = self.steam_dir / "workshop" / "440900"
        self.mods_dir.mkdir(parents=True)
        self.workshop_root.mkdir(parents=True)

    def environment(self, mod_list: str, **extra: str) -> dict[str, str]:
        env = {
            "GAME_DIR": str(self.game_dir),
            "STEAM_DATA_DIR": str(self.steam_dir),
            "STEAMCMD_BIN": str(FAKE_STEAMCMD),
            "STEAM_WORKSHOP_ROOT": str(self.workshop_root),
            "FAKE_WORKSHOP_ROOT": str(self.workshop_root),
            "SERVER_MOD_LIST": mod_list,
            "MOD_INSTALL_LOCK": str(Path(self.temporary.name) / "mod-install.lock"),
            "NATIVE_PRUNE_REMOVED_MODS": "false",
        }
        env.update(extra)
        return env

    def run_installer(self, mod_list: str, **extra: str) -> subprocess.CompletedProcess[str]:
        self.assertTrue(self.INSTALL_MODS.is_file(), f"missing installer: {self.INSTALL_MODS}")
        return run_script(self.INSTALL_MODS, self.environment(mod_list, **extra))

    def seed_previous(self) -> bytes:
        previous = b"*KnownGood.pak\n"
        (self.mods_dir / "modlist.txt").write_bytes(previous)
        (self.mods_dir / "KnownGood.pak").write_text("known-good", encoding="utf-8")
        (self.mods_dir / ".managed-mods.tsv").write_text("99\tKnownGood.pak\n", encoding="utf-8")
        return previous

    def test_two_mods_activate_atomically_in_requested_order(self) -> None:
        result = self.run_installer("3722881816,3720904511")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.mods_dir / "modlist.txt").read_text(encoding="utf-8"),
            "*StayBloody.pak\n*BetterThralls.pak\n",
        )
        self.assertTrue((self.mods_dir / "StayBloody.pak").is_file())
        self.assertTrue((self.mods_dir / "BetterThralls.pak").is_file())
        self.assertEqual(
            (self.mods_dir / ".managed-mods.tsv").read_text(encoding="utf-8"),
            "3722881816\tStayBloody.pak\n3720904511\tBetterThralls.pak\n",
        )

    def test_failed_workshop_item_preserves_last_known_good_list(self) -> None:
        previous = self.seed_previous()
        result = self.run_installer(
            "3722881816,1", FAKE_WORKSHOP_FAIL_ID="1"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.mods_dir / "modlist.txt").read_bytes(), previous)
        self.assertEqual((self.mods_dir / "KnownGood.pak").read_text(encoding="utf-8"), "known-good")

    def test_invalid_id_fails_before_touching_live_list(self) -> None:
        previous = self.seed_previous()
        result = self.run_installer("3722881816,not-a-number")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.mods_dir / "modlist.txt").read_bytes(), previous)

    def test_ambiguous_multiple_paks_preserve_live_list(self) -> None:
        previous = self.seed_previous()
        result = self.run_installer(
            "3722881816", FAKE_WORKSHOP_AMBIGUOUS_ID="3722881816"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly one", result.stderr)
        self.assertEqual((self.mods_dir / "modlist.txt").read_bytes(), previous)

    def test_prune_removes_only_previously_managed_stale_paks(self) -> None:
        self.seed_previous()
        (self.mods_dir / "ManualUnmanaged.pak").write_text("manual", encoding="utf-8")
        result = self.run_installer(
            "3722881816", NATIVE_PRUNE_REMOVED_MODS="true"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.mods_dir / "KnownGood.pak").exists())
        self.assertTrue((self.mods_dir / "ManualUnmanaged.pak").is_file())
        self.assertTrue((self.mods_dir / "StayBloody.pak").is_file())

    def test_prune_disabled_leaves_stale_managed_file_inactive(self) -> None:
        self.seed_previous()
        result = self.run_installer("3722881816")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.mods_dir / "KnownGood.pak").is_file())
        self.assertNotIn("KnownGood.pak", (self.mods_dir / "modlist.txt").read_text(encoding="utf-8"))


class ReadinessAndLifecycleTests(unittest.TestCase):
    A2S = ROOT / "scripts" / "native" / "a2s-info.py"
    RCON = ROOT / "scripts" / "native" / "rcon.py"
    SUPERVISOR = ROOT / "scripts" / "native" / "supervisor.sh"
    HEALTHCHECK = ROOT / "scripts" / "native" / "healthcheck.sh"

    def test_a2s_probe_parses_real_info_response(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

        def server() -> None:
            try:
                _, address = sock.recvfrom(2048)
                payload = (
                    b"\xff\xff\xff\xffI"
                    + bytes([17])
                    + b"Native Test\x00ConanSandbox\x00\x00Conan Exiles\x00"
                    + struct.pack("<H", 0)
                    + bytes([0, 10, 0])
                    + b"dl"
                    + bytes([0, 0])
                    + b"5.6.1\x00"
                    + bytes([0])
                )
                sock.sendto(payload, address)
            finally:
                sock.close()

        thread = threading.Thread(target=server, daemon=True)
        thread.start()
        result = subprocess.run(
            ["python3", str(self.A2S), "127.0.0.1", str(port), "--timeout", "1", "--json"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        thread.join(timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        info = json.loads(result.stdout)
        self.assertEqual(info["name"], "Native Test")
        self.assertEqual(info["map"], "ConanSandbox")
        self.assertEqual(info["max_players"], 10)
        self.assertEqual(info["environment"], "l")

    def test_a2s_timeout_is_unhealthy(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        result = subprocess.run(
            ["python3", str(self.A2S), "127.0.0.1", str(port), "--timeout", "0.1"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("A2S", result.stderr)

    def test_rcon_client_authenticates_without_password_argument(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        observed: dict[str, object] = {}

        def read_packet(connection: socket.socket) -> tuple[int, int, str]:
            size = struct.unpack("<i", connection.recv(4))[0]
            data = b""
            while len(data) < size:
                data += connection.recv(size - len(data))
            request_id, packet_type = struct.unpack("<ii", data[:8])
            return request_id, packet_type, data[8:-2].decode()

        def send_packet(connection: socket.socket, request_id: int, packet_type: int, body: str) -> None:
            payload = struct.pack("<ii", request_id, packet_type) + body.encode() + b"\x00\x00"
            connection.sendall(struct.pack("<i", len(payload)) + payload)

        def server() -> None:
            connection, _ = listener.accept()
            with connection:
                auth_id, auth_type, password = read_packet(connection)
                observed.update(auth_type=auth_type, password=password)
                send_packet(connection, auth_id, 2, "")
                command_id, command_type, command = read_packet(connection)
                observed.update(command_type=command_type, command=command)
                send_packet(connection, command_id, 0, "command-ok")
            listener.close()

        thread = threading.Thread(target=server, daemon=True)
        thread.start()
        env = os.environ.copy()
        env["RCON_PASSWORD"] = "unit-secret"
        result = subprocess.run(
            ["python3", str(self.RCON), "--host", "127.0.0.1", "--port", str(port), "help"],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        thread.join(timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "command-ok")
        self.assertEqual(observed["password"], "unit-secret")
        self.assertEqual(observed["command"], "help")
        self.assertNotIn("unit-secret", " ".join(result.args))
        help_result = subprocess.run(
            ["python3", str(self.RCON), "--help"], text=True, stdout=subprocess.PIPE, check=False
        )
        self.assertNotIn("--password", help_result.stdout)

    def test_supervisor_and_healthcheck_use_protocol_probes(self) -> None:
        supervisor = self.SUPERVISOR.read_text(encoding="utf-8")
        healthcheck = self.HEALTHCHECK.read_text(encoding="utf-8")
        entrypoint = (ROOT / "scripts" / "native" / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("rcon.py", supervisor)
        self.assertIn("server_pid", supervisor)
        self.assertNotIn("--password", supervisor)
        self.assertIn("a2s-info.py", healthcheck)
        self.assertIn("supervisor.sh", entrypoint)
        self.assertNotIn('exec "$GAME_DIR/ConanSandboxServer.sh"', entrypoint)


class BackupRestoreTests(unittest.TestCase):
    BACKUP = ROOT / "scripts" / "native" / "backup.py"
    RESTORE = ROOT / "scripts" / "native" / "restore.py"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.game_dir = root / "server"
        self.saved = self.game_dir / "ConanSandbox" / "Saved"
        self.config = self.saved / "Config" / "LinuxServer"
        self.mods = self.game_dir / "ConanSandbox" / "Mods"
        self.backups = root / "backups"
        self.config.mkdir(parents=True)
        self.mods.mkdir(parents=True)
        self.backups.mkdir()
        (self.config / "Engine.ini").write_text("[OnlineSubsystem]\nServerName=Backup Test\n", encoding="utf-8")
        (self.config / "Game.ini").write_text("[RconPlugin]\nRconEnabled=False\n", encoding="utf-8")
        (self.mods / "modlist.txt").write_text("*StayBloody.pak\n", encoding="utf-8")
        (self.mods / ".managed-mods.tsv").write_text("3722881816\tStayBloody.pak\n", encoding="utf-8")
        (self.mods / "StayBloody.pak").write_bytes(b"test-pak")
        self.connection = sqlite3.connect(self.saved / "game_0.db")
        self.addCleanup(self.connection.close)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("CREATE TABLE players (id INTEGER PRIMARY KEY, name TEXT)")
        self.connection.execute("INSERT INTO players(name) VALUES ('Seren')")
        self.connection.commit()

    def backup(self, mode: str) -> Path:
        self.assertTrue(self.BACKUP.is_file(), f"missing backup tool: {self.BACKUP}")
        env = os.environ.copy()
        env.update(
            {
                "GAME_DIR": str(self.game_dir),
                "BACKUP_DIR": str(self.backups),
                "NATIVE_BACKUP_MODE": mode,
                "NATIVE_BACKUP_RETENTION_COUNT": "10",
                "NATIVE_BACKUP_RETENTION_DAYS": "30",
            }
        )
        result = subprocess.run(
            ["python3", str(self.BACKUP), "--reason", "unit-test"],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        marker = "backup_created="
        line = next(line for line in result.stdout.splitlines() if line.startswith(marker))
        archive = Path(line[len(marker) :])
        self.assertTrue(archive.is_file())
        return archive

    def archive_names(self, archive: Path) -> set[str]:
        with tarfile.open(archive, "r:gz") as bundle:
            return {member.name for member in bundle.getmembers()}

    def test_light_backup_uses_sqlite_snapshot_without_wal_or_paks(self) -> None:
        archive = self.backup("light")
        names = self.archive_names(archive)
        self.assertIn("world/game_0.db", names)
        self.assertIn("config/Engine.ini", names)
        self.assertIn("mods/modlist.txt", names)
        self.assertIn("metadata.json", names)
        self.assertIn("checksums.sha256", names)
        self.assertFalse(any(name.endswith(("-wal", "-shm", ".pak")) for name in names))
        with tempfile.TemporaryDirectory() as extracted:
            with tarfile.open(archive, "r:gz") as bundle:
                bundle.extract("world/game_0.db", path=extracted)
            check = sqlite3.connect(Path(extracted) / "world" / "game_0.db")
            self.addCleanup(check.close)
            self.assertEqual(check.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(check.execute("SELECT name FROM players").fetchone()[0], "Seren")

    def test_full_backup_includes_only_active_managed_paks(self) -> None:
        (self.mods / "Unmanaged.pak").write_bytes(b"manual")
        archive = self.backup("full")
        names = self.archive_names(archive)
        self.assertIn("mods/StayBloody.pak", names)
        self.assertNotIn("mods/Unmanaged.pak", names)

    def test_restore_verify_and_apply_to_fresh_target(self) -> None:
        archive = self.backup("light")
        target = Path(self.temporary.name) / "restored"
        verify = subprocess.run(
            ["python3", str(self.RESTORE), str(archive), "--verify-only"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertFalse(target.exists())
        apply = subprocess.run(
            ["python3", str(self.RESTORE), str(archive), "--target", str(target), "--apply"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(apply.returncode, 0, apply.stderr)
        restored_db = target / "ConanSandbox" / "Saved" / "game_0.db"
        self.assertTrue(restored_db.is_file())
        self.assertTrue((target / "ConanSandbox" / "Saved" / "Config" / "LinuxServer" / "Engine.ini").is_file())
        self.assertTrue((target / "ConanSandbox" / "Mods" / "modlist.txt").is_file())
        connection = sqlite3.connect(restored_db)
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_restore_rejects_path_traversal_member(self) -> None:
        archive = Path(self.temporary.name) / "malicious.tar.gz"
        payload = Path(self.temporary.name) / "payload"
        payload.write_text("bad", encoding="utf-8")
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(payload, arcname="../escape")
        result = subprocess.run(
            ["python3", str(self.RESTORE), str(archive), "--verify-only"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe", result.stderr.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
