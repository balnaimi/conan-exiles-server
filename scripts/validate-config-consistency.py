#!/usr/bin/env python3
"""Validate that the documented settings count matches the generator and .env example."""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"
INDEX_HTML = ROOT / "docs" / "index.html"
README = ROOT / "README.md"
EXPECTED_SETTINGS = 236


def env_keys() -> list[str]:
    keys: list[str] = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"#?\s*([A-Z][A-Z0-9_]+)=", line)
        if match:
            keys.append(match.group(1))
    return keys


def generator_keys() -> list[str]:
    html = INDEX_HTML.read_text(encoding="utf-8")
    return re.findall(r"key:\s*['\"]([A-Z0-9_]+)['\"]", html)


def main() -> int:
    env = env_keys()
    env_counts = Counter(env)
    duplicates = sorted(key for key, count in env_counts.items() if count > 1)
    gen = generator_keys()
    gen_counts = Counter(gen)
    gen_duplicates = sorted(key for key, count in gen_counts.items() if count > 1)

    errors: list[str] = []
    if duplicates:
        errors.append(f".env.example duplicate keys: {', '.join(duplicates)}")
    if gen_duplicates:
        errors.append(f"docs/index.html duplicate generator keys: {', '.join(gen_duplicates)}")
    if len(env) != EXPECTED_SETTINGS:
        errors.append(f".env.example has {len(env)} settings, expected {EXPECTED_SETTINGS}")
    if len(gen) != EXPECTED_SETTINGS:
        errors.append(f"generator has {len(gen)} settings, expected {EXPECTED_SETTINGS}")
    if set(env) != set(gen):
        missing_in_env = sorted(set(gen) - set(env))
        missing_in_generator = sorted(set(env) - set(gen))
        if missing_in_env:
            errors.append("keys missing from .env.example: " + ", ".join(missing_in_env))
        if missing_in_generator:
            errors.append("keys missing from generator: " + ", ".join(missing_in_generator))

    for path in (README, INDEX_HTML):
        text = path.read_text(encoding="utf-8")
        if "237 settings" in text or "237 configurable settings" in text:
            errors.append(f"{path.relative_to(ROOT)} still documents 237 settings")

    if errors:
        print("Configuration consistency check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Configuration consistency OK: {EXPECTED_SETTINGS} unique settings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
