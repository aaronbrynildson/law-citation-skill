# Release Checklist (law-citation-skill)

Use this checklist before creating a public GitHub release for `law-citation-skill`.

## 1) Preflight validation
- [ ] Confirm version in `pyproject.toml` is `1.0`.
- [ ] Confirm skill name in:
  - [ ] `SKILL.md` (`name:`)
  - [ ] `.codex-plugin/plugin.json` (`name`)
  - [ ] `.agents/plugins/marketplace.json` (`plugins[0].name`)
- [ ] Ensure references to old package names are removed from release docs.
- [ ] Run compatibility check:
  - `python3 scripts/package_agent_skill.py --check`
- [ ] Run tests:
  - `python3 -m unittest discover -s tests -v`

## 2) Package artifacts
- [ ] Raw/Claude-style artifact:
  - `python3 scripts/package_agent_skill.py --output law-citation-skill-agent-skill.zip --layout folder --profile raw`
- [ ] Codex plugin artifact:
  - `python3 scripts/package_agent_skill.py --output law-citation-skill-codex-plugin.zip --layout folder --profile plugin`

## 3) GitHub release
- [ ] Commit all release-facing changes.
- [ ] Tag release:
  - `git tag -a v1.0 -m "law-citation-skill 1.0"`
  - `git push origin v1.0`
- [ ] Create GitHub Release draft on tag `v1.0`.
- [ ] Upload:
  - `law-citation-skill-1.0.zip`
  - `law-citation-skill-agent-skill.zip` (optional)
  - `law-citation-skill-codex-plugin.zip` (optional)

## 4) Verify
- [ ] Confirm release notes read as `Version 1.0` in `CHANGELOG.md`.
- [ ] Verify `.gitattributes` enforces expected text/binary handling.
