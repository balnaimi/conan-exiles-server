#!/usr/bin/env python3
"""Unit tests for the Native Linux runtime shell components."""

from __future__ import annotations

import io
import importlib.util
import hashlib
import fcntl
import json
import os
import socket
import sqlite3
import struct
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "scripts" / "native"
PREFLIGHT = NATIVE / "preflight.sh"
INSTALLER = NATIVE / "install-server.sh"
RUNTIME_STATE = NATIVE / "runtime_state.py"
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

    def test_proc_cpuinfo_with_tabbed_flags_line_passes(self) -> None:
        fixture = ROOT / "tests" / "fixtures" / "cpu" / "proc-cpuinfo.flags"
        source = PREFLIGHT.read_text(encoding="utf-8")
        source = source.replace(
            'flags_file="${CPU_FLAGS_FILE:-/proc/cpuinfo}"',
            f'flags_file="${{CPU_FLAGS_FILE:-{fixture}}}"',
        )
        descriptor, temporary_path = tempfile.mkstemp(prefix="preflight-proc-cpuinfo-", suffix=".sh")
        os.close(descriptor)
        script = Path(temporary_path)
        self.addCleanup(script.unlink, missing_ok=True)
        script.write_text(source, encoding="utf-8")
        script.chmod(0o755)
        environment = os.environ.copy()
        environment.pop("CPU_FLAGS_FILE", None)
        environment["NATIVE_PREFLIGHT_SKIP_RESOURCES"] = "1"
        result = subprocess.run(
            ["bash", str(script)],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("sse4_2=yes", result.stdout)
        self.assertIn("avx2=yes", result.stdout)

    def test_sse42_without_avx2_warns_but_passes(self) -> None:
        result = self.run_fixture("sse42-no-avx2.flags")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sse4_2=yes", result.stdout)
        self.assertIn("avx2=no", result.stdout)
        self.assertIn("not a universal UE5 requirement", result.stderr)

    def run_with_disk_available(self, available_gib: int) -> subprocess.CompletedProcess[str]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        fake_df = fake_bin / "df"
        available_kib = available_gib * 1024 * 1024
        fake_df.write_text(
            "#!/bin/sh\n"
            "printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n'\n"
            f"printf '/dev/test 104857600 0 {available_kib} 0%% /data\\n'\n",
            encoding="utf-8",
        )
        fake_df.chmod(0o755)
        return run_script(
            PREFLIGHT,
            {
                "CPU_FLAGS_FILE": str(CPU_FIXTURES / "modern.flags"),
                "GAME_DIR": str(root),
                "NATIVE_PREFLIGHT_SKIP_RESOURCES": "0",
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
            },
        )

    def run_with_memory_limit(
        self,
        host_memory_gib: int | str,
        cgroup_limit: str,
        cgroup_v1_limit: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        fake_df = fake_bin / "df"
        fake_df.write_text(
            "#!/bin/sh\n"
            "printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n'\n"
            "printf '/dev/test 104857600 0 44040192 0%% /data\\n'\n",
            encoding="utf-8",
        )
        fake_df.chmod(0o755)
        meminfo = root / "meminfo"
        memtotal_kib = (
            host_memory_gib * 1024 * 1024
            if isinstance(host_memory_gib, int)
            else host_memory_gib
        )
        meminfo.write_text(
            f"MemTotal:       {memtotal_kib} kB\n",
            encoding="utf-8",
        )
        cgroup_v2 = root / "memory.max"
        cgroup_v2.write_text(f"{cgroup_limit}\n", encoding="utf-8")
        cgroup_v1 = root / "memory.limit_in_bytes"
        if cgroup_v1_limit is not None:
            cgroup_v1.write_text(f"{cgroup_v1_limit}\n", encoding="utf-8")
        return run_script(
            PREFLIGHT,
            {
                "CPU_FLAGS_FILE": str(CPU_FIXTURES / "modern.flags"),
                "GAME_DIR": str(root),
                "NATIVE_PREFLIGHT_SKIP_RESOURCES": "0",
                "MEMINFO_FILE": str(meminfo),
                "CGROUP_MEMORY_MAX_FILE": str(cgroup_v2),
                "CGROUP_MEMORY_LIMIT_FILE": str(cgroup_v1),
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
            },
        )

    def test_preflight_prefers_finite_cgroup_limit_over_host_memtotal(self) -> None:
        result = self.run_with_memory_limit(62, str(10 * 1024**3))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("memory_gib=10", result.stdout)
        self.assertIn("memory_source=cgroup", result.stdout)
        self.assertIn("headroom is limited", result.stderr)
        self.assertIn("16 GiB is recommended", result.stderr)

    def test_preflight_falls_back_to_memtotal_when_cgroup_v2_is_unlimited(self) -> None:
        result = self.run_with_memory_limit(16, "max")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("memory_gib=16", result.stdout)
        self.assertIn("memory_source=meminfo", result.stdout)
        self.assertNotIn("GiB RAM is visible", result.stderr)

    def test_preflight_uses_finite_cgroup_v1_limit_when_v2_is_unlimited(self) -> None:
        result = self.run_with_memory_limit(62, "max", str(12 * 1024**3))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("memory_gib=12", result.stdout)
        self.assertIn("memory_source=cgroup", result.stdout)
        self.assertIn("headroom is limited", result.stderr)

    def test_preflight_rejects_malformed_memtotal_before_arithmetic(self) -> None:
        result = self.run_with_memory_limit("malformed-value", str(10 * 1024**3))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("memory_gib=10", result.stdout)
        self.assertIn("memory_source=cgroup", result.stdout)

    def test_preflight_ignores_v1_unlimited_sentinel_when_memtotal_is_malformed(self) -> None:
        result = self.run_with_memory_limit(
            "malformed-value",
            "max",
            "9223372036854771712",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("memory_gib=0", result.stdout)
        self.assertIn("memory_source=meminfo", result.stdout)
        self.assertIn("Less than 10 GiB RAM is visible", result.stderr)

    def test_preflight_parses_leading_zero_memtotal_as_decimal(self) -> None:
        result = self.run_with_memory_limit("08", "max")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("memory_gib=0", result.stdout)
        self.assertIn("memory_source=meminfo", result.stdout)

    def test_preflight_ignores_oversized_memtotal_before_arithmetic(self) -> None:
        result = self.run_with_memory_limit("9999999999999999999", "max")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("memory_gib=0", result.stdout)
        self.assertIn("memory_source=meminfo", result.stdout)
        self.assertIn("Less than 10 GiB RAM is visible", result.stderr)

    def test_preflight_parses_leading_zero_cgroup_limit_as_decimal(self) -> None:
        result = self.run_with_memory_limit(62, f"000{10 * 1024**3}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("memory_gib=10", result.stdout)
        self.assertIn("memory_source=cgroup", result.stdout)

    def test_preflight_reports_df_available_column(self) -> None:
        result = self.run_with_disk_available(42)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("disk_available_gib=42", result.stdout)

    def test_less_than_ten_gib_warns_that_free_space_is_very_limited(self) -> None:
        result = self.run_with_disk_available(9)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Less than 10 GiB free", result.stderr)
        self.assertIn("installation or update", result.stderr)
        self.assertNotIn("70 GiB", result.stderr)

    def test_less_than_twenty_gib_warns_about_limited_headroom(self) -> None:
        result = self.run_with_disk_available(15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Less than 20 GiB free", result.stderr)
        self.assertIn("headroom is limited", result.stderr)
        self.assertNotIn("70 GiB", result.stderr)

    def test_twenty_gib_free_has_no_storage_warning(self) -> None:
        result = self.run_with_disk_available(20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("GiB free", result.stderr)


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

    def test_failed_update_fails_closed_without_claiming_transactional_rollback(self) -> None:
        shipping = self.game_dir / "ConanSandbox/Binaries/Linux/ConanSandboxServer-Linux-Shipping"
        shipping.parent.mkdir(parents=True)
        shipping.write_text("known-good", encoding="utf-8")
        shipping.chmod(0o755)
        result = run_script(INSTALLER, self.environment(FAKE_STEAMCMD_FAIL="1"))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("preserved", result.stderr.lower())
        self.assertIn("may be partially updated", result.stderr)

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

    def test_runtime_steam_home_workshop_path_is_discovered(self) -> None:
        runtime_root = self.steam_dir / "Steam" / "steamapps" / "workshop" / "content" / "440900"
        result = self.run_installer(
            "3722881816",
            STEAM_WORKSHOP_ROOT="",
            FAKE_WORKSHOP_ROOT=str(runtime_root),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.mods_dir / "modlist.txt").read_text(encoding="utf-8"),
            "*StayBloody.pak\n",
        )

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

    def test_native_empty_comma_entry_fails_before_touching_live_list(self) -> None:
        previous = self.seed_previous()
        result = self.run_installer("3722881816,,3720904511")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Malformed SERVER_MOD_LIST", result.stderr)
        self.assertEqual((self.mods_dir / "modlist.txt").read_bytes(), previous)

    def test_native_rejects_whitespace_and_all_comma_gap_forms(self) -> None:
        malformed = (
            "   \t ",
            "3722881816, 3720904511",
            "3722881816\t3720904511",
            "3722881816 3720904511",
            "3722881816\r3720904511",
            "3722881816\n3720904511",
            ",3722881816",
            "3722881816,",
            "3722881816,,3720904511",
            ",",
        )
        for value in malformed:
            with self.subTest(value=value):
                previous = self.seed_previous()
                result = self.run_installer(value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("malformed", result.stderr.lower())
                self.assertEqual((self.mods_dir / "modlist.txt").read_bytes(), previous)

    def test_ambiguous_multiple_paks_preserve_live_list(self) -> None:
        previous = self.seed_previous()
        result = self.run_installer(
            "3722881816", FAKE_WORKSHOP_AMBIGUOUS_ID="3722881816"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly one", result.stderr)
        self.assertEqual((self.mods_dir / "modlist.txt").read_bytes(), previous)

    def test_wine_compatibility_keeps_exactly_empty_list_untouched(self) -> None:
        previous = self.seed_previous()
        result = self.run_installer("", MOD_INSTALL_COMPAT_MODE="wine")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.mods_dir / "modlist.txt").read_bytes(), previous)

    def test_wine_compatibility_whitespace_list_deactivates_mods(self) -> None:
        self.seed_previous()
        result = self.run_installer("   \t ", MOD_INSTALL_COMPAT_MODE="wine")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.mods_dir / "modlist.txt").read_text(encoding="utf-8"), "")

    def test_wine_compatibility_skips_empty_comma_entries(self) -> None:
        result = self.run_installer(
            ",3722881816,,3720904511,",
            MOD_INSTALL_COMPAT_MODE="wine",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.mods_dir / "modlist.txt").read_text(encoding="utf-8"),
            "*StayBloody.pak\n*BetterThralls.pak\n",
        )

    def test_wine_compatibility_multiline_uses_only_first_physical_line(self) -> None:
        result = self.run_installer(
            "3722881816\n3720904511",
            MOD_INSTALL_COMPAT_MODE="wine",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.mods_dir / "modlist.txt").read_text(encoding="utf-8"),
            "*StayBloody.pak\n",
        )

    def test_wine_compatibility_selects_deterministic_lexical_pak(self) -> None:
        result = self.run_installer(
            "3722881816",
            MOD_INSTALL_COMPAT_MODE="wine",
            FAKE_WORKSHOP_AMBIGUOUS_ID="3722881816",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.mods_dir / "modlist.txt").read_text(encoding="utf-8"),
            "*SecondPackage.pak\n",
        )
        self.assertIn("selecting lexical first", result.stderr)

    def test_wine_compatibility_allows_duplicate_package_names(self) -> None:
        result = self.run_installer(
            "3722881816,3720904511",
            MOD_INSTALL_COMPAT_MODE="wine",
            FAKE_WORKSHOP_SHARED_PAK_NAME="SharedPackage.pak",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.mods_dir / "modlist.txt").read_text(encoding="utf-8"),
            "*SharedPackage.pak\n*SharedPackage.pak\n",
        )

    def test_wine_compatibility_allows_zero_byte_pak(self) -> None:
        result = self.run_installer(
            "3722881816",
            MOD_INSTALL_COMPAT_MODE="wine",
            FAKE_WORKSHOP_ZERO_BYTE_ID="3722881816",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.mods_dir / "StayBloody.pak").stat().st_size, 0)

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

    def test_prune_true_keeps_a_managed_package_that_remains_active(self) -> None:
        result = self.run_installer("3722881816")
        self.assertEqual(result.returncode, 0, result.stderr)
        first_hash = (self.mods_dir / "StayBloody.pak").read_bytes()
        result = self.run_installer("3722881816", NATIVE_PRUNE_REMOVED_MODS="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.mods_dir / "StayBloody.pak").read_bytes(), first_hash)
        self.assertIn("Activated 1 Workshop mod", result.stdout)

    def test_prune_disabled_leaves_stale_managed_file_inactive(self) -> None:
        self.seed_previous()
        result = self.run_installer("3722881816")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.mods_dir / "KnownGood.pak").is_file())
        self.assertNotIn("KnownGood.pak", (self.mods_dir / "modlist.txt").read_text(encoding="utf-8"))


class RuntimeStateSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.game = self.root / "server"
        self.runtime = self.game / ".runtime"
        self.runtime.mkdir(parents=True)

    def run_state(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(RUNTIME_STATE), *args],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_lock_exec_rejects_symlink_without_truncating_victim(self) -> None:
        victim = self.root / "victim"
        victim.write_bytes(b"do-not-truncate")
        (self.runtime / "operation.lock").symlink_to(victim)
        result = self.run_state(
            "lock-exec", "--game-dir", str(self.game), "--", "/bin/true"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr.lower())
        self.assertEqual(victim.read_bytes(), b"do-not-truncate")

    def test_publish_pid_atomically_replaces_symlink_without_touching_victim(self) -> None:
        victim = self.root / "pid-victim"
        victim.write_bytes(b"keep-me")
        (self.runtime / "server.pid").symlink_to(victim)
        result = self.run_state(
            "publish-pid", "--game-dir", str(self.game), "--pid", "1234"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(victim.read_bytes(), b"keep-me")
        self.assertFalse((self.runtime / "server.pid").is_symlink())
        self.assertEqual((self.runtime / "server.pid").read_text(encoding="ascii"), "1234\n")
        self.assertEqual((self.runtime / "server.pid").stat().st_mode & 0o777, 0o600)

    def test_exec_failure_closes_fd9_and_releases_lock(self) -> None:
        probe = (
            "import fcntl, importlib.util, os, pathlib\n"
            f"p=pathlib.Path({str(RUNTIME_STATE)!r})\n"
            "s=importlib.util.spec_from_file_location('runtime_state_probe', p)\n"
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
            f"g=pathlib.Path({str(self.game)!r})\n"
            "try:\n m.lock_exec(g, ['/definitely/missing-command'])\n"
            "except OSError:\n pass\n"
            "try:\n os.fstat(9); raise SystemExit('fd9 leaked')\n"
            "except OSError:\n pass\n"
            "fd=os.open(g/'.runtime'/'operation.lock', os.O_RDWR|os.O_NOFOLLOW)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB); os.close(fd)\n"
        )
        result = subprocess.run(
            ["python3", "-c", probe],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_verify_lock_rejects_spoofed_environment_without_fd9(self) -> None:
        result = self.run_state("verify-lock", "--game-dir", str(self.game))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fd 9", result.stderr.lower())

    def test_lock_stays_held_until_pid_is_published(self) -> None:
        entered = self.root / "entered"
        child = self.root / "publish-after-delay.sh"
        child.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f": > {entered!s}\n"
            "sleep 0.4\n"
            f"python3 {RUNTIME_STATE!s} publish-pid --game-dir {self.game!s} --pid $$\n"
            "flock -u 9\n"
            "exec 9>&-\n"
            "sleep 0.2\n",
            encoding="utf-8",
        )
        child.chmod(0o755)
        process = subprocess.Popen(
            [
                "python3",
                str(RUNTIME_STATE),
                "lock-exec",
                "--game-dir",
                str(self.game),
                "--",
                str(child),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.monotonic() + 3
        while not entered.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(entered.exists(), "lock holder did not start")
        lock_fd = os.open(self.runtime / "operation.lock", os.O_RDWR | os.O_NOFOLLOW)
        try:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse((self.runtime / "server.pid").exists())
            while not (self.runtime / "server.pid").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue((self.runtime / "server.pid").exists())
            acquired = False
            while not acquired and time.monotonic() < deadline:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    time.sleep(0.01)
            self.assertTrue(acquired, "operation lock was not released after PID publication")
        finally:
            os.close(lock_fd)
        stdout, stderr = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 0, stdout + stderr)


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
                send_packet(connection, 0, 2, "Authenticated.")
                command_id, command_type, command = read_packet(connection)
                observed.update(command_type=command_type, command=command)
                send_packet(connection, auth_id, 0, "command-ok")
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
        self.assertIn('local seconds="$1"\n', supervisor)
        self.assertIn('local ticks=$((seconds * 4))', supervisor)
        self.assertNotIn('local seconds="$1" ticks=', supervisor)
        self.assertNotIn('> "$pid_file"', supervisor)
        self.assertNotIn("--password", supervisor)
        self.assertIn("a2s-info.py", healthcheck)
        self.assertIn("listplayers", healthcheck)
        self.assertIn("NATIVE_HEALTHCHECK_RCON", healthcheck)
        self.assertNotIn('case "${RCON_ENABLED,,}"', healthcheck)
        self.assertNotIn(" help ", healthcheck)
        self.assertIn("supervisor.sh", entrypoint)
        self.assertIn("RUNTIME_RCON_SECRET_FILE", entrypoint)
        self.assertIn("runtime_state.py lock-exec", entrypoint)
        self.assertIn("runtime_state.py verify-lock", entrypoint)
        self.assertNotIn('exec 9> "$GAME_DIR/.runtime/operation.lock"', entrypoint)
        self.assertNotIn("flock -u 9", entrypoint)
        self.assertIn("runtime_state.py publish-pid", supervisor)
        self.assertIn("flock -u 9", supervisor)
        self.assertLess(supervisor.index("runtime_state.py publish-pid"), supervisor.index("flock -u 9"))
        self.assertRegex(supervisor, r'setsid .* 9>&- &')
        self.assertIn("unset ADMIN_PASSWORD SERVER_PASSWORD RCON_PASSWORD", entrypoint)
        self.assertNotIn('exec "$GAME_DIR/ConanSandboxServer.sh"', entrypoint)


class BackupRestoreTests(unittest.TestCase):
    BACKUP = ROOT / "scripts" / "native" / "backup.py"
    RESTORE = ROOT / "scripts" / "native" / "restore.py"
    BACKUP_LOOP = ROOT / "scripts" / "native" / "backup-loop.sh"

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
        (self.config / "Engine.ini").write_text(
            "[OnlineSubsystem]\nServerName=Backup Test\nServerPassword=UNIT_SERVER_SECRET\n",
            encoding="utf-8",
        )
        (self.config / "ServerSettings.ini").write_text(
            "[ServerSettings]\nAdminPassword=UNIT_ADMIN_SECRET\n", encoding="utf-8"
        )
        (self.config / "Game.ini").write_text(
            "[RconPlugin]\nRconEnabled=False\nRconPassword=UNIT_RCON_SECRET\n", encoding="utf-8"
        )
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

    def archive_with_metadata(self, archive: Path, metadata: dict[str, object]) -> Path:
        stage = Path(self.temporary.name) / "tampered-stage"
        stage.mkdir()
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(stage)
        (stage / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        rows = []
        for path in sorted(stage.rglob("*")):
            if path.is_file() and path.name != "checksums.sha256":
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                rows.append(f"{digest}  {path.relative_to(stage).as_posix()}")
        (stage / "checksums.sha256").write_text("\n".join(rows) + "\n", encoding="utf-8")
        tampered = Path(self.temporary.name) / "tampered.tar.gz"
        with tarfile.open(tampered, "w:gz") as bundle:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    bundle.add(path, arcname=path.relative_to(stage).as_posix(), recursive=False)
        return tampered

    def test_light_backup_uses_sqlite_snapshot_without_wal_or_paks(self) -> None:
        archive = self.backup("light")
        names = self.archive_names(archive)
        self.assertIn("world/game_0.db", names)
        self.assertIn("config/Engine.ini", names)
        self.assertIn("mods/modlist.txt", names)
        self.assertIn("metadata.json", names)
        self.assertIn("checksums.sha256", names)
        self.assertFalse(any(name.endswith(("-wal", "-shm", ".pak")) for name in names))
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        config_payload = b""
        with tarfile.open(archive, "r:gz") as bundle:
            for name in ("config/Engine.ini", "config/ServerSettings.ini", "config/Game.ini"):
                extracted = bundle.extractfile(name)
                self.assertIsNotNone(extracted)
                assert extracted is not None
                config_payload += extracted.read() + b"\n"
        for marker in (b"UNIT_SERVER_SECRET", b"UNIT_ADMIN_SECRET", b"UNIT_RCON_SECRET"):
            self.assertNotIn(marker, config_payload)
        self.assertGreaterEqual(config_payload.count(b"[REDACTED]"), 3)
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

    def test_empty_mod_restore_records_explicit_empty_dependency_set(self) -> None:
        for path in (self.mods / "modlist.txt", self.mods / ".managed-mods.tsv", self.mods / "StayBloody.pak"):
            path.unlink()
        for mode in ("light", "full"):
            with self.subTest(mode=mode):
                archive = self.backup(mode)
                target = Path(self.temporary.name) / f"empty-mod-{mode}"
                result = subprocess.run(
                    ["python3", str(self.RESTORE), str(archive), "--target", str(target), "--apply"],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                marker = target / ".runtime" / "restore-required-workshop-ids"
                self.assertTrue(marker.is_file())
                self.assertEqual(marker.read_text(encoding="ascii"), "\n")

    def test_backup_and_restore_hash_files_incrementally(self) -> None:
        self.assertNotIn(".read_bytes()", self.BACKUP.read_text(encoding="utf-8"))
        self.assertNotIn(".read_bytes()", self.RESTORE.read_text(encoding="utf-8"))

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

    def test_restore_existing_world_creates_pre_restore_backup_without_lock_deadlock(self) -> None:
        archive = self.backup("light")
        target = Path(self.temporary.name) / "existing-target"
        target_saved = target / "ConanSandbox" / "Saved"
        target_saved.mkdir(parents=True)
        existing = sqlite3.connect(target_saved / "game_0.db")
        existing.execute("CREATE TABLE old_world (value TEXT)")
        existing.execute("INSERT INTO old_world VALUES ('before-restore')")
        existing.commit()
        existing.close()
        target_backups = Path(self.temporary.name) / "target-backups"
        env = os.environ.copy()
        env["BACKUP_DIR"] = str(target_backups)
        result = subprocess.run(
            ["python3", str(self.RESTORE), str(archive), "--target", str(target), "--apply"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(target_backups.glob("conan-native-*.tar.gz"))), 1)

    def test_backup_uses_shared_target_operation_lock(self) -> None:
        runtime = self.game_dir / ".runtime"
        runtime.mkdir()
        lock_path = runtime / "operation.lock"
        env = os.environ.copy()
        env.update(
            {
                "GAME_DIR": str(self.game_dir),
                "BACKUP_DIR": str(self.backups),
                "NATIVE_BACKUP_MODE": "light",
            }
        )
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(
                ["python3", str(self.BACKUP), "--reason", "locked-test"],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("operation", result.stderr.lower())

    def test_restore_acquires_target_lock_before_archive_processing(self) -> None:
        archive = Path(self.temporary.name) / "locked-malicious.tar.gz"
        payload = Path(self.temporary.name) / "locked-payload"
        payload.write_text("bad", encoding="utf-8")
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(payload, arcname="../escape")
        target = Path(self.temporary.name) / "locked-before-verify"
        runtime = target / ".runtime"
        runtime.mkdir(parents=True)
        with (runtime / "operation.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(
                ["python3", str(self.RESTORE), str(archive), "--target", str(target), "--apply"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("operation", result.stderr.lower())
        self.assertNotIn("unsafe archive", result.stderr.lower())

    def test_restore_rejects_symlinked_operation_lock_without_touching_victim(self) -> None:
        archive = self.backup("light")
        target = Path(self.temporary.name) / "restore-lock-symlink"
        runtime = target / ".runtime"
        runtime.mkdir(parents=True)
        victim = Path(self.temporary.name) / "restore-lock-victim"
        victim.write_bytes(b"unchanged")
        (runtime / "operation.lock").symlink_to(victim)
        result = subprocess.run(
            ["python3", str(self.RESTORE), str(archive), "--target", str(target), "--apply"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr.lower())
        self.assertEqual(victim.read_bytes(), b"unchanged")

    def test_restore_uses_target_scoped_operation_lock(self) -> None:
        archive = self.backup("light")
        target = Path(self.temporary.name) / "locked-target"
        runtime = target / ".runtime"
        runtime.mkdir(parents=True)
        lock_path = runtime / "operation.lock"
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(
                ["python3", str(self.RESTORE), str(archive), "--target", str(target), "--apply"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("operation", result.stderr.lower())
        self.assertFalse((target / "ConanSandbox" / "Saved" / "game_0.db").exists())

    def test_restore_rejects_malformed_metadata_before_target_mutation(self) -> None:
        archive = self.backup("light")
        metadata = {
            "format_version": 1,
            "created_utc": "2026-08-02T00:00:00+00:00",
            "reason": "unit-test",
            "mode": "light",
            "world_database": "game_0.db",
            "workshop_ids": [3722881816],
            "active_packages": ["StayBloody.pak"],
            "config_secrets_redacted": True,
        }
        tampered = self.archive_with_metadata(archive, metadata)
        target = Path(self.temporary.name) / "malformed-target"
        result = subprocess.run(
            ["python3", str(self.RESTORE), str(tampered), "--target", str(target), "--apply"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("metadata", result.stderr.lower())
        self.assertFalse((target / "ConanSandbox" / "Saved" / "game_0.db").exists())

    def test_restore_pid_permission_error_fails_closed(self) -> None:
        spec = importlib.util.spec_from_file_location("native_restore_under_test", self.RESTORE)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader if spec else None)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        pid_file = Path(self.temporary.name) / "server.pid"
        pid_file.write_text("123\n", encoding="ascii")
        with mock.patch.object(module.os, "kill", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(RuntimeError, "cannot verify"):
                module.process_is_active(pid_file)

    def test_restore_rejects_symlinked_target_component(self) -> None:
        archive = self.backup("light")
        target = Path(self.temporary.name) / "symlink-target"
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        target.mkdir()
        (target / "ConanSandbox").symlink_to(outside, target_is_directory=True)
        result = subprocess.run(
            ["python3", str(self.RESTORE), str(archive), "--target", str(target), "--apply"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr.lower())
        self.assertFalse((outside / "Saved" / "game_0.db").exists())

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

    def test_restore_streams_archive_members_before_limit_enforcement(self) -> None:
        source = self.RESTORE.read_text(encoding="utf-8")
        self.assertNotIn("getmembers()", source)
        self.assertIn('tarfile.open(archive, "r|gz")', source)

    def test_restore_rejects_duplicate_archive_members(self) -> None:
        archive = Path(self.temporary.name) / "duplicate.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            for payload in (b"first", b"second"):
                member = tarfile.TarInfo("world/game_0.db")
                member.size = len(payload)
                bundle.addfile(member, io.BytesIO(payload))
        result = subprocess.run(
            ["python3", str(self.RESTORE), str(archive), "--verify-only"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", result.stderr.lower())

    def test_restore_default_expansion_limit_is_conservative(self) -> None:
        spec = importlib.util.spec_from_file_location("native_restore_limits_under_test", self.RESTORE)
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NATIVE_RESTORE_MAX_BYTES", None)
            self.assertLessEqual(
                module.restore_limit("NATIVE_RESTORE_MAX_BYTES", module.DEFAULT_MAX_BYTES, 1024**4),
                20 * 1024**3,
            )

    def test_restore_enforces_archive_expansion_limit(self) -> None:
        archive = Path(self.temporary.name) / "oversized.tar.gz"
        payload = b"oversized"
        with tarfile.open(archive, "w:gz") as bundle:
            member = tarfile.TarInfo("world/game_0.db")
            member.size = len(payload)
            bundle.addfile(member, io.BytesIO(payload))
        env = os.environ.copy()
        env["NATIVE_RESTORE_MAX_BYTES"] = "4"
        result = subprocess.run(
            ["python3", str(self.RESTORE), str(archive), "--verify-only"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("size limit", result.stderr.lower())

    def test_backup_loop_run_once_invokes_configured_tool(self) -> None:
        marker = Path(self.temporary.name) / "backup-loop-marker"
        fake = Path(self.temporary.name) / "fake-backup"
        fake.write_text(f"#!/usr/bin/env bash\nprintf invoked > '{marker}'\n", encoding="utf-8")
        fake.chmod(0o755)
        env = os.environ.copy()
        env.update(
            {
                "NATIVE_BACKUP_ENABLED": "true",
                "NATIVE_BACKUP_RUN_ONCE": "1",
                "NATIVE_BACKUP_INTERVAL_MINUTES": "60",
                "NATIVE_BACKUP_TOOL": str(fake),
            }
        )
        result = subprocess.run(
            ["bash", str(self.BACKUP_LOOP)],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(marker.read_text(encoding="utf-8"), "invoked")

    def test_backup_loop_disabled_exits_without_invoking_tool(self) -> None:
        marker = Path(self.temporary.name) / "disabled-marker"
        env = os.environ.copy()
        env.update(
            {
                "NATIVE_BACKUP_ENABLED": "false",
                "NATIVE_BACKUP_RUN_ONCE": "1",
                "NATIVE_BACKUP_TOOL": f"touch {marker}",
            }
        )
        result = subprocess.run(
            ["bash", str(self.BACKUP_LOOP)],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
