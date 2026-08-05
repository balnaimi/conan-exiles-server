#!/usr/bin/env python3
"""Build the Pages wiki from the canonical Markdown operator guides."""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
GUIDES = ROOT / "docs" / "guides"
OUTPUT = ROOT / "docs" / "docs"

GUIDE_TITLES = {
    "configuration": "Configuration",
    "mods": "Steam Workshop Mods",
    "native-linux": "Native Linux — Recommended for New Servers",
    "operations": "Operations and Troubleshooting",
    "compatibility": "Conan Exiles Enhanced Compatibility",
    "development": "Development",
}


def rewrite_links(source: str) -> str:
    """Translate repository Markdown links into deployed Pages routes."""
    source = source.replace(
        "](../README.md)",
        "](../)",
    )
    source = source.replace(
        "](https://balnaimi.github.io/conan-exiles-server/)",
        "](../../config/)",
    )
    source = source.replace(
        "](../../.env.minimal)",
        "](https://github.com/balnaimi/conan-exiles-server/blob/main/.env.minimal)",
    )
    source = source.replace(
        "](../../.env.example)",
        "](https://github.com/balnaimi/conan-exiles-server/blob/main/.env.example)",
    )
    source = re.sub(
        r"\]\(([a-z0-9-]+)\.md(#[^)]+)?\)",
        lambda match: f"](../{match.group(1)}/{match.group(2) or ''})",
        source,
    )
    return source


def sidebar(active_slug: str) -> str:
    links = []
    for slug, title in GUIDE_TITLES.items():
        current = ' aria-current="page"' if slug == active_slug else ""
        links.append(f'      <a{current} href="../{slug}/">{html.escape(title)}</a>')
    return "\n".join(links)


def render(slug: str) -> str:
    source_path = GUIDES / f"{slug}.md"
    source = rewrite_links(source_path.read_text(encoding="utf-8"))
    body = markdown.markdown(
        source,
        extensions=["fenced_code", "tables", "toc"],
        output_format="html",
    )
    title = GUIDE_TITLES[slug]
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{html.escape(title)} guide for the Conan Exiles Enhanced Docker server.">
  <link rel="icon" type="image/svg+xml" href="../../assets/favicon.svg">
  <link rel="stylesheet" href="../../assets/site.css">
  <title>{html.escape(title)} — Conan Server</title>
</head>
<body>
  <a class="skip-link" href="#main">Skip to content</a>
  <header class="site-header">
    <a class="brand" href="../../">⚔️ Conan Server</a>
    <nav class="top-nav" aria-label="Primary navigation">
      <a href="../../">Quick Start</a><a href="../../config/">Config Generator</a><a href="../../migrate/">Migrate</a><a aria-current="page" href="../">Documentation</a>
    </nav>
  </header>
  <main id="main" class="docs-shell">
    <aside class="docs-sidebar" aria-label="Documentation sections">
      <strong><a href="../">Documentation</a></strong>
{sidebar(slug)}
    </aside>
    <article class="docs-content">
{body}
    </article>
  </main>
  <footer class="site-footer"><span>Generated from <code>docs/guides/{slug}.md</code>.</span><a href="https://github.com/balnaimi/conan-exiles-server/blob/main/docs/guides/{slug}.md">Edit this guide</a></footer>
</body>
</html>
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when generated pages are stale")
    args = parser.parse_args()

    stale: list[Path] = []
    for slug in GUIDE_TITLES:
        output_path = OUTPUT / slug / "index.html"
        expected = render(slug)
        if args.check:
            if not output_path.is_file() or output_path.read_text(encoding="utf-8") != expected:
                stale.append(output_path)
            continue
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(expected, encoding="utf-8")
        print(f"generated {output_path.relative_to(ROOT)}")

    if stale:
        for path in stale:
            print(f"stale generated page: {path.relative_to(ROOT)}")
        print("Run: python3 scripts/build-pages-docs.py")
        return 1
    if args.check:
        print(f"Generated Pages documentation is current: {len(GUIDE_TITLES)} guides")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
