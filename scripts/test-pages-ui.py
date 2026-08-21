#!/usr/bin/env python3
"""Regression checks for the self-contained GitHub Pages UI."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "docs" / "index.html"
CONFIG_HTML_PATH = ROOT / "docs" / "config" / "index.html"
README_PATH = ROOT / "README.md"
OPERATIONS_PATH = ROOT / "docs" / "guides" / "operations.md"
NATIVE_GUIDE_PATH = ROOT / "docs" / "guides" / "native-linux.md"
QUICK_HTML = HTML_PATH.read_text(encoding="utf-8")
HTML = CONFIG_HTML_PATH.read_text(encoding="utf-8")
README = README_PATH.read_text(encoding="utf-8")
OPERATIONS = OPERATIONS_PATH.read_text(encoding="utf-8")
NATIVE_GUIDE = NATIVE_GUIDE_PATH.read_text(encoding="utf-8")
DETAIL_HTML = "\n".join(
    (ROOT / "docs" / "docs" / slug / "index.html").read_text(encoding="utf-8")
    for slug in ("operations", "native-linux")
)


def test_multi_page_information_architecture_is_fast_and_backward_compatible() -> None:
    quick_path = ROOT / "docs" / "index.html"
    config_path = ROOT / "docs" / "config" / "index.html"
    docs_path = ROOT / "docs" / "docs" / "index.html"
    migrate_path = ROOT / "docs" / "migrate" / "index.html"
    site_js_path = ROOT / "docs" / "assets" / "site.js"

    for path in (quick_path, config_path, docs_path, migrate_path, site_js_path):
        assert path.is_file(), f"Missing Pages route: {path.relative_to(ROOT)}"

    quick = quick_path.read_text(encoding="utf-8")
    config = config_path.read_text(encoding="utf-8")
    docs_home = docs_path.read_text(encoding="utf-8")
    migrate = migrate_path.read_text(encoding="utf-8")
    site_js = site_js_path.read_text(encoding="utf-8")

    assert len(quick.encode("utf-8")) <= 40_000, "Quick Start landing is no longer lightweight"
    for marker in (
        "Start a New Server",
        "Existing Wine Server",
        'href="config/"',
        'href="docs/"',
        'href="migrate/"',
        'id="native-quick-start"',
    ):
        assert marker in quick, f"Quick Start landing is missing: {marker}"
    assert "const CONFIG =" not in quick, "The 250-setting generator leaked back into the landing page"

    for marker in ("Configuration Generator", "const CONFIG =", 'id="output"'):
        assert marker in config, f"Dedicated generator page is missing: {marker}"
    for marker in ("Documentation", "Configuration", "Operations", "Troubleshooting"):
        assert marker in docs_home, f"Documentation hub is missing: {marker}"
    for marker in ("Wine to Native", "dry-run", "rollback", "never deletes"):
        assert marker.lower() in migrate.lower(), f"Migration page is missing: {marker}"

    required_legacy_routes = {
        "#quick-start": "#native-quick-start",
        "#config-generator": "config/",
        "#mods": "docs/mods/",
        "#server-management": "docs/operations/",
        "#about": "docs/",
        "#cpu-compatibility": "docs/operations/#cpu-compatibility-check",
    }
    for old_hash, destination in required_legacy_routes.items():
        assert old_hash in site_js and destination in site_js, (
            f"Legacy route is not preserved: {old_hash} -> {destination}"
        )


def test_wiki_pages_are_generated_from_the_canonical_markdown_guides() -> None:
    builder = ROOT / "scripts" / "build-pages-docs.py"
    requirements = ROOT / "requirements-docs.txt"
    assert builder.is_file(), "Missing documentation builder"
    assert requirements.is_file(), "Missing pinned documentation build dependency"
    assert "Markdown==" in requirements.read_text(encoding="utf-8")

    guide_titles = {
        "configuration": "Configuration",
        "mods": "Steam Workshop Mods",
        "native-linux": "Native Linux — Recommended for New Servers",
        "operations": "Operations and Troubleshooting",
        "compatibility": "Conan Exiles Enhanced Compatibility",
        "development": "Development",
    }
    for slug, title in guide_titles.items():
        source = ROOT / "docs" / "guides" / f"{slug}.md"
        output = ROOT / "docs" / "docs" / slug / "index.html"
        assert source.is_file(), f"Missing canonical guide: {source.relative_to(ROOT)}"
        assert output.is_file(), f"Missing generated wiki page: {output.relative_to(ROOT)}"
        html = output.read_text(encoding="utf-8")
        for marker in (title, '../../assets/site.css', 'class="docs-shell"', 'class="docs-sidebar"'):
            assert marker in html, f"Generated {slug} page is missing: {marker}"
        markdown_links = re.findall(r'href="([^"]+\.md(?:#[^"]*)?)"', html)
        assert all(link.startswith(("https://", "http://")) for link in markdown_links), (
            f"Generated {slug} page still contains a local Markdown link: {markdown_links}"
        )

    check = subprocess.run(
        ["python3", str(builder), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert check.returncode == 0, check.stdout + check.stderr

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
            self.icon_hrefs.append(values["href"] or "")


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"] or "")
        attribute = "href" if tag in {"a", "link"} else "src" if tag in {"img", "script"} else None
        if attribute and values.get(attribute):
            self.references.append(values[attribute] or "")


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
        "Page details": DETAIL_HTML,
        "Operations": OPERATIONS,
        "Native guide": NATIVE_GUIDE,
    }
    for name, text in user_facing.items():
        assert "modern cores" not in text.lower(), f"{name} still uses the undefined 'modern cores' wording"
        for marker in ("SSE4.2", "AVX", "AVX2"):
            assert marker in text, f"{name} does not spell out the {marker} compatibility guidance"
        assert "Funcom has not officially confirmed AVX2" in text, f"{name} is missing the AVX2 disclaimer"

    for name, text in {"Page details": DETAIL_HTML, "Operations": OPERATIONS, "Native guide": NATIVE_GUIDE}.items():
        assert "for flag in sse4_2 avx avx2; do" in text, f"{name} is missing the copyable Linux CPU check"
        assert "grep -qw" in text, f"{name} is missing the per-flag CPU probe"
        assert "lscpu" in text, f"{name} is missing guest-visible CPU model guidance"
        assert "host-passthrough" in text, f"{name} is missing VPS/QEMU host-passthrough guidance"

    markdown_rows = re.findall(r"\|[^\n]+\|[^\n]+\|[^\n]+\|[^\n]+\|", README + "\n" + OPERATIONS)
    html_sizing = DETAIL_HTML
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

    assert "https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-engine-5.2-release-notes" in DETAIL_HTML
    assert 'id="cpu-compatibility-check"' in DETAIL_HTML


def test_storage_guidance_distinguishes_capacity_from_safe_headroom() -> None:
    user_facing = {
        "README": README,
        "Page details": DETAIL_HTML,
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


def test_memory_guidance_distinguishes_practical_start_from_recommended_headroom() -> None:
    user_facing = {
        "README": README,
        "Page details": DETAIL_HTML,
        "Operations": OPERATIONS,
        "Native guide": NATIVE_GUIDE,
    }
    required_guidance = (
        "12 GB is a practical starting allocation for a small vanilla server",
        "16 GB is recommended for typical use, not a hard minimum",
        "hard 10 GiB container cap with no extra swap budget",
        "Wine peaked at 9.19 GiB",
        "Native peaked at 8.69 GiB",
        "The test host remained at 16 GiB, so the 10 GiB cap tested the game budget under pressure but did not reproduce whole-system pressure on a 12 GB VPS",
        "players, larger worlds, and mods can require more memory",
    )
    forbidden_claims = (
        "Use at least 16 GB",
        "16 GB minimum",
        "12 GB minimum",
    )

    for name, text in user_facing.items():
        for guidance in required_guidance:
            assert guidance in text, f"{name} is missing RAM guidance: {guidance}"
        for claim in forbidden_claims:
            assert claim not in text, f"{name} still overstates RAM as a hard minimum: {claim}"

    cgroup_diagnostic = (
        "Native preflight prefers a finite cgroup memory limit when one is exposed; "
        "otherwise it falls back to /proc/meminfo"
    )
    for name, text in {"Operations": OPERATIONS, "Native guide": NATIVE_GUIDE}.items():
        assert cgroup_diagnostic in text, f"{name} is missing cgroup-aware memory diagnostics"


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
        asset = (CONFIG_HTML_PATH.parent / href).resolve()
        assert asset.exists(), f"Missing favicon asset: {href}"


def test_generator_route_opens_the_generator_without_legacy_page_clutter() -> None:
    assert_contains(
        'class="generator-page"',
        "const DEFAULT_TAB = 'config-generator'",
        "const initialTab = hasDeepLink ? hashTab : DEFAULT_TAB",
        'aria-controls="tab-config-generator"',
        '.generator-page .tab-btn:not([data-tab="config-generator"])',
        '.generator-page .tab-content:not(#tab-config-generator)',
        'href="../">← Back to Quick Start</a>',
    )
    assert "event.target.classList.add('active')" not in HTML


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


def test_native_is_recommended_for_every_new_server_without_breaking_existing_wine() -> None:
    readme_top = "\n".join(README.splitlines()[:80])
    config_quick_start = HTML.split("TAB 1: QUICK START", 1)[1].split("TAB 2:", 1)[0]
    wine_landing_card = QUICK_HTML.split('id="wine-existing"', 1)[1]
    for marker in (
        "Native Linux — Recommended for New Servers",
        "Starting a new server? Use Native Linux",
        "Wine — Existing Deployments",
        "docker-compose.native.yml",
        "ghcr.io/balnaimi/conan-exiles-server:native",
        "ghcr.io/balnaimi/conan-exiles-server:2.9.1-native",
        "Updating the default Wine Compose deployment never switches it to Native",
        "Fresh Native deployment",
    ):
        assert marker in readme_top, f"README top does not prominently expose Native runtime: {marker}"

    for marker in (
        "Native Linux",
        "Recommended for new servers",
        "Wine",
        "Existing deployments",
        "docker-compose.native.yml",
        'id="native-quick-start"',
        "Do not attach Wine volumes",
        "Existing Wine Server",
        'href="migrate/"',
    ):
        assert marker in QUICK_HTML, f"Quick Start does not clearly expose the runtime choice: {marker}"
    assert QUICK_HTML.index('id="native-quick-start"') < QUICK_HTML.index('id="wine-existing"')
    for current_surface in (readme_top, QUICK_HTML, NATIVE_GUIDE, config_quick_start):
        assert "experimental" not in current_surface.lower()
    assert "Starting a new server? Use Native Linux" in QUICK_HTML
    assert "latest" in QUICK_HTML and "backward compatibility" in QUICK_HTML
    assert "mkdir conan-server" not in wine_landing_card
    assert "cd /path/to/existing-wine-server" in wine_landing_card
    assert "Starting a new server? Use Native Linux" in config_quick_start
    assert config_quick_start.index('id="native-quick-start"') < config_quick_start.index("Update an Existing Wine Deployment")
    assert "mkdir conan-server" not in config_quick_start
    assert "Use Config Generator for a New Native Server" in config_quick_start
    assert "docker compose -f docker-compose.native.yml up -d" in config_quick_start
    for current_surface in (QUICK_HTML, config_quick_start):
        assert "must stop Wine" not in current_surface
        assert "Plan the migration, stop Wine" not in current_surface
    assert "Run the migration plan while Wine is still running" in QUICK_HTML
    assert "apply owns the stop" in QUICK_HTML
    assert "run the migration plan while Wine is still running" in config_quick_start
    assert "apply owns the stop" in config_quick_start

    for marker in (
        "INI password values are replaced",
        "current secrets are rendered",
        "Docker health uses A2S readiness",
        "RCON is internal-only and is an explicit diagnostic",
        "SSE4.2",
        "StayBloody",
        "Better Thralls",
        "host-passthrough",
    ):
        assert marker in DETAIL_HTML, f"Detailed Pages documentation is missing: {marker}"


def test_operations_commands_match_the_runtime_they_claim_to_manage() -> None:
    daily = OPERATIONS.split("## Daily commands", 1)[1].split("## Readiness", 1)[0]
    diagnostics = OPERATIONS.split("## Diagnostics", 1)[1].split("## Exit code 137", 1)[0]
    wine = OPERATIONS.split("## Headless Wine messages", 1)[1].split("## Sizing guidance", 1)[0]
    reset = OPERATIONS.split("## Full reset", 1)[1]

    for section in (daily, diagnostics):
        compose_commands = [line for line in section.splitlines() if line.startswith("docker compose")]
        assert compose_commands
        assert all("-f docker-compose.native.yml" in line for line in compose_commands)

    assert "Existing Wine deployment only" in wine
    assert "docker compose pull" in wine
    assert "-f docker-compose.native.yml" not in wine

    assert "### Native reset" in reset
    assert "docker compose -f docker-compose.native.yml down -v" in reset
    assert "### Existing Wine reset" in reset
    assert "docker compose down -v" in reset
    assert reset.index("### Native reset") < reset.index("### Existing Wine reset")
    assert 'id="diagnostics"' in DETAIL_HTML
    assert 'id="exit-code-137-and-memory"' in DETAIL_HTML


def test_config_management_defaults_to_native_and_scopes_wine_commands() -> None:
    management = HTML.split("TAB 4: SERVER MANAGEMENT", 1)[1].split("Enhanced Settings Status", 1)[0]
    native_general = management.split("🐧 Native Basic Commands", 1)[1].split("🍷 Existing Wine Startup Troubleshooting", 1)[0]
    native_diagnostics = management.split("🐧 Native Runtime Diagnostics", 1)[1].split("🐧 Native Backup and Restore", 1)[0]

    for section in (native_general, native_diagnostics):
        compose_commands = re.findall(r"docker compose[^<\n]+", section)
        assert compose_commands
        assert all("-f docker-compose.native.yml" in line for line in compose_commands)

    assert "conan-exiles-enhanced-native" in native_diagnostics
    assert "🍷 Existing Wine Backup" in management
    assert "🍷 Existing Wine Restore" in management
    assert "migrate-compose-wine-to-native.sh plan" in management
    assert "migrate-compose-wine-to-native.sh apply" in management
    assert "migrate-compose-wine-to-native.sh --apply" not in management
    assert "🐧 Native Full Reset" in management
    assert "docker compose -f docker-compose.native.yml down -v" in management
    assert "🍷 Existing Wine Full Reset" in management
    assert "docker compose down -v" in management
    assert management.index("🐧 Native Full Reset") < management.index("🍷 Existing Wine Full Reset")
    assert "Wine Stable" not in management


def test_all_internal_page_links_assets_and_fragments_resolve() -> None:
    document_root = ROOT / "docs"
    pages = sorted(document_root.rglob("*.html"))
    assert pages, "No Pages HTML documents found"
    parsed: dict[Path, LinkParser] = {}
    for page in pages:
        parser = LinkParser()
        parser.feed(page.read_text(encoding="utf-8"))
        parsed[page.resolve()] = parser

    for page in pages:
        for reference in parsed[page.resolve()].references:
            parts = urlsplit(reference)
            if parts.scheme or parts.netloc or reference.startswith(("mailto:", "tel:", "data:")):
                continue
            path_text = unquote(parts.path)
            if path_text.startswith("/conan-exiles-server/"):
                target = document_root / path_text.removeprefix("/conan-exiles-server/")
            elif path_text.startswith("/"):
                raise AssertionError(f"Project Pages link must not be host-root-relative: {page}: {reference}")
            elif path_text:
                target = page.parent / path_text
            else:
                target = page
            target = target.resolve()
            assert target.is_relative_to(document_root.resolve()), f"Internal link escapes docs/: {page}: {reference}"
            if path_text.endswith("/") or target.is_dir():
                target = target / "index.html"
            assert target.exists(), f"Broken internal reference in {page.relative_to(ROOT)}: {reference}"
            if parts.fragment and target.suffix == ".html":
                target_parser = parsed.get(target.resolve())
                assert target_parser is not None, f"HTML target was not inventoried: {target}"
                assert unquote(parts.fragment) in target_parser.ids, (
                    f"Broken fragment in {page.relative_to(ROOT)}: {reference}"
                )


def test_polish_features_exist() -> None:
    assert_contains(
        "Copy command",
        "function enhanceCodeBlocks(",
        "function copyText(",
        "@media (max-width: 480px)",
        "Finding Workshop IDs",
        "View all releases on GitHub",
        "v2.8.1 — Native Recommended for New Servers",
        "v2.8.0 — Safe Operations &amp; Supply-Chain Hardening",
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
