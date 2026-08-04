## Summary

<!-- What changed and why? -->

## Scope

- [ ] Wine Stable
- [ ] Native Linux Experimental
- [ ] Both runtimes
- [ ] Host-side migration / backup / diagnostics
- [ ] Pages / documentation only
- [ ] CI / release tooling

## Safety and compatibility

- [ ] `latest` still means Wine unless this is an explicitly approved major release.
- [ ] Native and Wine volumes remain isolated.
- [ ] No automatic migration or destructive volume deletion was added.
- [ ] User input, paths, archives, symlinks, and subprocess arguments are validated.
- [ ] No secrets, `.env`, world data, or backup archives are committed or printed.
- [ ] Restore/migration changes fail closed and have rollback or recovery guidance.

## Verification

- [ ] A failing behavior test was observed before implementation.
- [ ] Relevant Python, Bash, Compose, Pages, and browser checks pass.
- [ ] Generated documentation is current.
- [ ] Runtime changes were exercised on a disposable host when required.
- [ ] I reviewed the staged diff and documented user-facing changes.
