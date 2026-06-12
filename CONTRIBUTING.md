# Contributing

Contributions should keep the checker audit-first and conservative. Do not add
automatic rewrites unless they are deterministic, narrow, and covered by tests.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip build twine
python3 -m pip install .
```

The runtime intentionally has no third-party dependencies.

## Validation

Run these before opening a pull request:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/*.py scripts/core/*.py tests/*.py
python3 scripts/package_agent_skill.py --check
python3 -m build
python3 -m twine check dist/*
```

For Codex plugin changes, also run the plugin validator from the local
`plugin-creator` skill if available:

```bash
python3 /path/to/plugin-creator/scripts/validate_plugin.py .
```

Remove `build/`, `dist/`, `*.egg-info`, and Python cache directories before
committing source changes.
