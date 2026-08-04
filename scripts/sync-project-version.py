#!/usr/bin/env python3
"""Synchronize current versioned-image examples from the VERSION file."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = r"[0-9]+\.[0-9]+\.[0-9]+"

RULES: dict[str, tuple[tuple[str, str, int], ...]] = {
    "README.md": (
        (rf"ghcr\.io/balnaimi/conan-exiles-server:{VERSION_PATTERN}(?=`)", "ghcr.io/balnaimi/conan-exiles-server:{version}", 1),
        (rf"ghcr\.io/balnaimi/conan-exiles-server:{VERSION_PATTERN}-native(?=`)", "ghcr.io/balnaimi/conan-exiles-server:{version}-native", 1),
    ),
    "docs/guides/native-linux.md": (
        (rf"ghcr\.io/balnaimi/conan-exiles-server:{VERSION_PATTERN}-native(?=`)", "ghcr.io/balnaimi/conan-exiles-server:{version}-native", 1),
    ),
    "docs/config/index.html": (
        (rf"versioned <code>:{VERSION_PATTERN}-native</code>", "versioned <code>:{version}-native</code>", 1),
    ),
    "scripts/test-pages-ui.py": (
        (rf'ghcr\.io/balnaimi/conan-exiles-server:{VERSION_PATTERN}-native', "ghcr.io/balnaimi/conan-exiles-server:{version}-native", 1),
    ),
}


def project_version() -> str:
    value = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(VERSION_PATTERN, value):
        raise RuntimeError("VERSION must contain one MAJOR.MINOR.PATCH value")
    return value


def synchronized_text(relative: str, version: str) -> tuple[str, str]:
    path = ROOT / relative
    original = path.read_text(encoding="utf-8")
    updated = original
    for pattern, replacement, expected_count in RULES[relative]:
        updated, count = re.subn(pattern, replacement.format(version=version), updated)
        if count != expected_count:
            raise RuntimeError(
                f"{relative}: expected {expected_count} version marker(s) for {pattern!r}, found {count}"
            )
    return original, updated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when tracked version examples drift")
    args = parser.parse_args()
    version = project_version()
    drift: list[str] = []
    for relative in RULES:
        original, updated = synchronized_text(relative, version)
        if original == updated:
            continue
        if args.check:
            drift.append(relative)
        else:
            (ROOT / relative).write_text(updated, encoding="utf-8")
            print(f"updated {relative}")
    if drift:
        print("Version drift: " + ", ".join(drift))
        return 1
    print(f"Project version examples are synchronized: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
