# Contributing

Thanks for helping improve the Conan Exiles Enhanced server project.

## Before opening a change

- Search existing issues and releases.
- Keep Wine Stable backward-compatible and attached to `latest`.
- Keep Native Linux Experimental on separate images and volumes.
- Never add automatic Wine-to-Native migration or destructive volume cleanup.
- Use synthetic worlds and isolated random Compose projects in tests.
- Never commit `.env`, passwords, tokens, private keys, world databases, or backup archives.

## Bug reports

Include the runtime, image tag and digest, game build, Docker/Compose versions, CPU flags visible to the guest, RAM/cgroup limit, disk availability, mods, container state/restarts/OOM flag, A2S result, and redacted logs. The `conan-doctor` command can generate a secret-minimized diagnostic report; review it before attaching it.

## Pull requests

1. Add a failing behavior test before implementation.
2. Keep changes focused and document user-visible behavior.
3. Run the relevant checks listed in `docs/guides/development.md`.
4. Rebuild generated Pages and verify `python3 scripts/build-pages-docs.py --check`.
5. Confirm no command uses `docker compose down -v` outside explicit destructive-reset documentation or isolated disposable tests.
6. State whether the change affects Wine, Native, both, Pages only, or host-side tooling.
7. State whether a migration, restore, permissions, secrets, ports, tags, or volume contract changes.

Runtime changes require disposable-host acceptance in addition to mocks. A process existing is not sufficient readiness evidence; use A2S, health, and lifecycle checks.

## Style

- Shell: Bash syntax plus ShellCheck severity `error`.
- Python: standard library where practical, explicit errors, safe subprocess argument lists, no `shell=True`.
- Documentation: short README, focused guides, relative links, and warnings before dangerous commands.
