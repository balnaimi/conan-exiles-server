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
HTML = HTML_PATH.read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


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
    ):
        assert marker in readme_top, f"README top does not prominently expose Native runtime: {marker}"
    assert_contains(
        "Native Linux Experimental Available",
        "Wine Stable",
        "docker-compose.native.yml",
        "ghcr.io/balnaimi/conan-exiles-server:native",
        "Native Linux Quick Start",
        "SSE4.2",
        "8.70 GiB",
        "StayBloody",
        "Better Thralls",
    )


def test_polish_features_exist() -> None:
    assert_contains(
        "Copy command",
        "function enhanceCodeBlocks(",
        "function copyText(",
        "@media (max-width: 480px)",
        "Finding Workshop IDs",
        "View all releases on GitHub",
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
