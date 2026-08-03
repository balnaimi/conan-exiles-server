#!/usr/bin/env python3
"""Regression checks for the self-contained GitHub Pages UI."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "docs" / "index.html"
README_PATH = ROOT / "README.md"
OPERATIONS_PATH = ROOT / "docs" / "guides" / "operations.md"
NATIVE_GUIDE_PATH = ROOT / "docs" / "guides" / "native-linux.md"
HTML = HTML_PATH.read_text(encoding="utf-8")
README = README_PATH.read_text(encoding="utf-8")
OPERATIONS = OPERATIONS_PATH.read_text(encoding="utf-8")
NATIVE_GUIDE = NATIVE_GUIDE_PATH.read_text(encoding="utf-8")

class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.h1_count = 0
        self.meta_names: set[str] = set()
        self.meta_properties: set[str] = set()
        self.icon_hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "h1":
            self.h1_count += 1
        if tag == "meta" and values.get("name"):
            self.meta_names.add(values["name"] or "")
        if tag == "meta" and values.get("property"):
            self.meta_properties.add(values["property"] or "")
        if tag == "link" and "icon" in (values.get("rel") or "").split():
            self.icon_hrefs.append(values.get("href") or "")


def assert_contains(*needles: str) -> None:
    for needle in needles:
        assert needle in HTML, f"Missing expected Pages UI marker: {needle}"


def test_readme_stays_short_and_guides_are_reachable() -> None:
    readme_lines = README.splitlines()
    assert len(readme_lines) <= 180, f"README grew beyond the concise 180-line contract: {len(readme_lines)}"
    assert len(README.encode("utf-8")) <= 8_000, "README grew beyond the concise 8 KB contract"
    assert "## 📝 Release Notes" not in README
    assert "## Release Notes" not in README

    guide_dir = ROOT / "docs" / "guides"
    required_guides = {
        "compatibility.md",
        "configuration.md",
        "development.md",
        "mods.md",
        "native-linux.md",
        "operations.md",
    }
    available_guides = {path.name for path in guide_dir.glob("*.md")}
    assert required_guides <= available_guides, f"Missing focused guides: {sorted(required_guides - available_guides)}"

    managed_docs = [ROOT / "README.md", ROOT / "docs" / "README.md"]
    managed_docs.extend(sorted(guide_dir.glob("*.md")))

    link_pattern = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")
    for source in managed_docs:
        assert source.is_file(), f"Missing documentation file: {source.relative_to(ROOT)}"
        text = source.read_text(encoding="utf-8")
        for raw_target in link_pattern.findall(text):
            target = raw_target.strip().split("#", 1)[0].split("?", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (source.parent / target).resolve()
            assert resolved.is_relative_to(ROOT), f"Documentation link escapes repository: {source} -> {raw_target}"
            assert resolved.exists(), f"Broken documentation link: {source.relative_to(ROOT)} -> {raw_target}"


def test_cpu_requirements_are_explicit_and_careful() -> None:
    user_facing = {
        "README": README,
        "Page": HTML,
        "Operations": OPERATIONS,
        "Native guide": NATIVE_GUIDE,
    }
    for name, text in user_facing.items():
        assert "modern cores" not in text.lower(), f"{name} still uses the undefined 'modern cores' wording"
        for marker in ("SSE4.2", "AVX", "AVX2"):
            assert marker in text, f"{name} does not spell out the {marker} compatibility guidance"
        assert "Funcom has not officially confirmed AVX2" in text, f"{name} is missing the AVX2 disclaimer"

    for name, text in {"Page": HTML, "Operations": OPERATIONS, "Native guide": NATIVE_GUIDE}.items():
        assert "for flag in sse4_2 avx avx2; do" in text, f"{name} is missing the copyable Linux CPU check"
        assert "grep -qw" in text, f"{name} is missing the per-flag CPU probe"
        assert "lscpu" in text, f"{name} is missing guest-visible CPU model guidance"
        assert "host-passthrough" in text, f"{name} is missing VPS/QEMU host-passthrough guidance"

    markdown_rows = re.findall(r"\|[^\n]+\|[^\n]+\|[^\n]+\|[^\n]+\|", README + "\n" + OPERATIONS)
    html_sizing = HTML.split("System Requirements", 1)[1].split("CPU Compatibility", 1)[0]
    sizing_tables = "\n".join(markdown_rows) + "\n" + html_sizing
    assert "SSE4.2 visible" not in sizing_tables
    assert "verify AVX/AVX2" not in sizing_tables

    combined = "\n".join(user_facing.values())
    for claim in (
        r"\bAVX2\s+(?:is\s+)?(?:required|mandatory)\b",
        r"\brequires\s+AVX2\b",
        r"\bmust\s+support\s+AVX2\b",
    ):
        assert re.search(claim, combined, re.IGNORECASE) is None, f"Unsupported AVX2 claim matched: {claim}"

    assert "https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-engine-5.2-release-notes" in HTML
    assert 'id="cpu-compatibility" style="scroll-margin-top:' in HTML


def test_storage_guidance_distinguishes_capacity_from_safe_headroom() -> None:
    user_facing = {
        "README": README,
        "Page": HTML,
        "Operations": OPERATIONS,
        "Native guide": NATIVE_GUIDE,
    }
    required_guidance = (
        "20 GB is a practical starting allocation for one runtime",
        "25–35 GB is recommended for updates, world growth, and a simple backup",
        "35–40 GB is a comfortable allocation, not a minimum",
        "The measured clean Wine-to-Native coexistence used about 14 GB on the host",
        "25 GB is a practical migration floor for that scenario",
        "35 GB is recommended for safer migration headroom",
        "70 GB is comfortable, not required",
        "100 GB is a safer recommendation",
        "These are project planning recommendations, not official Funcom requirements or hard limits",
    )
    forbidden_claims = (
        "70 GB minimum",
        "Allocate at least 70 GB",
        "Allocate at least <strong>70 GB</strong>",
        "35–40 GB total storage is not sufficient",
        "35–40 GB is a practical starting allocation",
    )

    for name, text in user_facing.items():
        for guidance in required_guidance:
            assert guidance in text, f"{name} is missing storage guidance: {guidance}"
        for claim in forbidden_claims:
            assert claim not in text, f"{name} still overstates storage as a hard minimum: {claim}"


def extract_js_function(name: str) -> str:
    match = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", HTML)
    assert match, f"Missing JavaScript function: {name}"
    start = match.start()
    brace = HTML.find("{", match.start())
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(brace, len(HTML)):
        char = HTML[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return HTML[start : index + 1]
    raise AssertionError(f"Unclosed JavaScript function: {name}")


def test_document_metadata() -> None:
    parser = PageParser()
    parser.feed(HTML)
    assert parser.h1_count == 1, f"Expected one h1, found {parser.h1_count}"
    assert "description" in parser.meta_names
    assert {"og:title", "og:description", "og:type", "og:url"} <= parser.meta_properties
    assert parser.icon_hrefs, "Missing favicon link"
    for href in parser.icon_hrefs:
        assert (ROOT / "docs" / href).exists(), f"Missing favicon asset: {href}"


def test_tabs_are_semantic_and_deep_linkable() -> None:
    assert_contains(
        'role="tablist"',
        'role="tab"',
        'aria-selected="true"',
        'aria-controls="tab-quick-start"',
        "function activateTab(",
        "hashchange",
    )
    assert "event.target.classList.add('active')" not in HTML
    assert "addEventListener('popstate'" not in HTML


def test_generator_controls_are_accessible() -> None:
    assert_contains(
        '<button type="button" class="section-header"',
        'aria-expanded="',
        'aria-controls="body-${section.id}"',
        'const inputId = `input-${f.key}`',
        'id="${inputId}"',
        'for="${inputId}"',
        'body.hidden = !isOpen',
    )
    assert '<div class="section-header" onclick="toggleSection(this)">' not in HTML


def test_generator_productivity_toolbar_exists() -> None:
    assert_contains(
        'id="settingSearch"',
        'id="changedOnlyFilter"',
        "function filterSettings(",
        "function setAllSections(",
        "function scrollToOutput(",
        'id="viewOutputButton"',
    )


def test_password_fields_and_warnings_exist() -> None:
    assert re.search(r"key: 'SERVER_PASSWORD'.*type: 'password'", HTML)
    assert re.search(r"key: 'ADMIN_PASSWORD'.*type: 'password'", HTML)
    assert re.search(r"key: 'RCON_PASSWORD'.*type: 'password'", HTML)
    assert_contains("function updateSecurityWarning(", 'id="securityWarning"', "togglePasswordVisibility(")


def test_time_formatting_normalizes_boundaries() -> None:
    functions = "\n".join(
        extract_js_function(name) for name in ("formatTime", "formatHours")
    )
    script = f"""
{functions}
console.log(JSON.stringify({{
  seconds59: formatTime(59),
  minute1: formatTime(60),
  hourBoundary: formatTime(3599),
  hour1: formatTime(3600),
  hourMinute: formatTime(3660),
  dayBoundary: formatTime(86399),
  day1: formatTime(86400),
  day15Boundary: formatTime(1295999),
  negativeSeconds: formatTime(-1),
  hours1: formatHours(1),
  hours1_5: formatHours(1.5),
  hours23_5: formatHours(23.5),
  hours24: formatHours(24),
  hours25_5: formatHours(25.5)
}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        text=True,
        capture_output=True,
    )
    assert json.loads(result.stdout) == {
        "seconds59": "59 seconds",
        "minute1": "1 minute",
        "hourBoundary": "1 hour",
        "hour1": "1 hour",
        "hourMinute": "1 hour 1 min",
        "dayBoundary": "1 day",
        "day1": "1 day",
        "day15Boundary": "15 days",
        "negativeSeconds": "Invalid duration",
        "hours1": "1 hour",
        "hours1_5": "1 hour 30 min",
        "hours23_5": "23 hours 30 min",
        "hours24": "1 day",
        "hours25_5": "1 day 1h 30 min",
    }


def test_invalid_numbers_are_rejected() -> None:
    assert_contains(
        "function setNumberValue(",
        "Number.isFinite(parsed)",
        "input.value = String(values[key]",
        "setNumberValue('${f.key}', this)",
    )
    function = extract_js_function("setNumberValue")
    script = """
const values = { MAX_PLAYERS: 40 };
const cfg = { min: 1, max: 70, default: 40 };
const getFieldConfig = () => cfg;
const showToast = () => {};
const formatHours = String;
const formatTime = String;
const document = { getElementById: () => null };
const window = { setTimeout: callback => callback() };
const setValue = (key, value) => { values[key] = value; };
function input(value) {
  return {
    value,
    attrs: {},
    setAttribute(key, val) { this.attrs[key] = val; },
    removeAttribute(key) { delete this.attrs[key]; }
  };
}
""" + function + """
const blank = input('');
setNumberValue('MAX_PLAYERS', blank);
const high = input('100');
setNumberValue('MAX_PLAYERS', high);
const valid = input('12');
setNumberValue('MAX_PLAYERS', valid);
console.log(JSON.stringify({
  blank: { value: blank.value },
  high: { value: high.value },
  valid: { value: valid.value, stored: values.MAX_PLAYERS }
}));
"""
    result = subprocess.run(["node", "-e", script], check=True, text=True, capture_output=True)
    assert json.loads(result.stdout) == {
        "blank": {"value": "40"},
        "high": {"value": "40"},
        "valid": {"value": "12", "stored": 12},
    }


def test_dotenv_values_are_safely_quoted() -> None:
    function = extract_js_function("formatEnvValue")
    script = (
        function
        + "\nconsole.log(JSON.stringify(["
        + "formatEnvValue('hello #1$HOME'),"
        + "formatEnvValue(\"O'Brien\"),"
        + "formatEnvValue(''),"
        + "formatEnvValue('C:\\\\Games')"
        + "]));"
    )
    result = subprocess.run(
        ["node", "-e", script], check=True, text=True, capture_output=True
    )
    actual = result.stdout.strip()
    expected = r'''["'hello #1$HOME'","'O\\'Brien'","''","'C:\\Games'"]'''
    assert actual == expected, f"Unexpected dotenv quoting:\n{actual}\n!=\n{expected}"

    keys = ["PLAIN", "APOSTROPHE", "EMPTY", "BACKSLASH"]
    formatted = json.loads(actual)
    expected_values = {
        "PLAIN": "hello #1$HOME",
        "APOSTROPHE": "O'Brien",
        "EMPTY": "",
        "BACKSLASH": r"C:\Games",
    }
    with tempfile.TemporaryDirectory() as directory:
        directory_path = Path(directory)
        env_path = directory_path / "qa.env"
        compose_path = directory_path / "compose.yml"
        env_path.write_text(
            "\n".join(f"{key}={value}" for key, value in zip(keys, formatted)) + "\n",
            encoding="utf-8",
        )
        compose_path.write_text(
            "services:\n  qa:\n    image: busybox\n    env_file:\n      - qa.env\n",
            encoding="utf-8",
        )
        compose = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_path),
                "-f",
                str(compose_path),
                "config",
                "--environment",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        service_config = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_path),
                "-f",
                str(compose_path),
                "config",
                "--format",
                "json",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
    parsed = {}
    for line in compose.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in expected_values:
            parsed[key] = value
    assert parsed == expected_values, f"Docker Compose dotenv round-trip failed: {parsed!r}"
    service_values = json.loads(service_config.stdout)["services"]["qa"]["environment"]
    normalized_service_values = {
        key: value.replace("$$", "$") for key, value in service_values.items()
    }
    assert normalized_service_values == expected_values, (
        f"Docker Compose env_file round-trip failed: {service_values!r}"
    )


def test_native_linux_is_prominent_and_unambiguous() -> None:
    readme_top = "\n".join(README.splitlines()[:80])
    for marker in (
        "Native Linux Experimental Available",
        "Wine Stable",
        "docker-compose.native.yml",
        "ghcr.io/balnaimi/conan-exiles-server:native",
        "ghcr.io/balnaimi/conan-exiles-server:2.7.2-native",
        "Updating the default Compose deployment never switches it to Native",
        "Fresh Native deployment only",
    ):
        assert marker in readme_top, f"README top does not prominently expose Native runtime: {marker}"
    assert_contains(
        "Native Linux Experimental Available",
        "Wine Stable",
        "docker-compose.native.yml",
        "ghcr.io/balnaimi/conan-exiles-server:native",
        ":2.7.2-native",
        "Native Linux Experimental — Opt-in Quick Start",
        'href="#native-quick-start"',
        'id="native-quick-start"',
        "function openNativeQuickStart(",
        "Conan Exiles Enhanced Server — Wine Stable &amp; Native Linux Experimental",
        "Updating <code>docker-compose.yml</code> never switches it to Native",
        "Password values in backed-up INIs are replaced",
        "A2S health",
        "RCON diagnostic",
        "SSE4.2",
        "8.70 GiB",
        "StayBloody",
        "Better Thralls",
        "CPU Compatibility",
        "host-passthrough",
    )


def test_polish_features_exist() -> None:
    assert_contains(
        "Copy command",
        "function enhanceCodeBlocks(",
        "function copyText(",
        "@media (max-width: 480px)",
        "Finding Workshop IDs",
        "View all releases on GitHub",
        "v2.7.2 — CPU Compatibility Guidance",
        "v2.7.1 — Native Health Documentation Hotfix",
        "v2.7.0 — Native Linux Experimental",
        "v2.6.1 — Duration and Clipboard Reliability Hotfix",
    )
    assert "color:#555" not in HTML.replace(" ", "")


def main() -> None:
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"Pages UI checks OK: {len(tests)} tests")


if __name__ == "__main__":
    main()
