#!/usr/bin/env python3
"""Static contract checks for Dockerfile.native."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile.native"


class NativeImageContractTests(unittest.TestCase):
    def text(self) -> str:
        self.assertTrue(DOCKERFILE.is_file(), f"missing {DOCKERFILE}")
        return DOCKERFILE.read_text(encoding="utf-8")

    def test_image_runs_as_non_root(self) -> None:
        text = self.text()
        users = re.findall(r"^USER\s+(.+)$", text, re.MULTILINE)
        self.assertTrue(users, "Dockerfile.native must set USER")
        self.assertNotIn(users[-1].strip(), {"root", "0", "0:0"})
        self.assertNotRegex(text, r"(?im)^\s*sudo(?:\s|$)")

    def test_image_does_not_install_wine_or_xvfb(self) -> None:
        text = self.text().lower()
        self.assertNotIn("winehq", text)
        self.assertNotRegex(text, r"\bxvfb\b")
        self.assertNotIn("vc_redist", text)

    def test_image_copies_native_runtime_and_declares_healthcheck(self) -> None:
        text = self.text()
        self.assertIn("scripts/native/", text)
        self.assertIn("scripts/runtime/", text)
        self.assertRegex(text, r"(?m)^HEALTHCHECK\s")
        self.assertRegex(text, r"(?m)^ENTRYPOINT\s")

    def test_image_documents_native_variant_for_registry_users(self) -> None:
        text = self.text()
        self.assertIn("org.opencontainers.image.title", text)
        self.assertIn('org.opencontainers.image.licenses="GPL-3.0-only"', text)
        self.assertIn("Native Linux", text)
        self.assertIn("experimental", text.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
