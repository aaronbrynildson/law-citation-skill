#!/usr/bin/env python3
"""Validate and package the citation-checker agent skill."""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path


NAME_RE = re.compile(r"^(?=.{1,64}$)[a-z0-9]+(?:-[a-z0-9]+)*$")
XML_TAG_RE = re.compile(r"<[^>\n]+>")
OPENAI_FIELDS = ("display_name", "short_description", "default_prompt")
RAW_SKILL_TOP_LEVEL = {
    "SKILL.md",
    "LICENSE",
    "requirements.txt",
    "agents",
    "references",
    "scripts",
}
PLUGIN_TOP_LEVEL = {
    ".agents",
    ".codex-plugin",
    "SKILL.md",
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "PRIVACY.md",
    "SECURITY.md",
    "MANIFEST.in",
    "pyproject.toml",
    "requirements.txt",
    "agents",
    "references",
    "scripts",
    "skills",
}
PROFILE_TOP_LEVELS = {
    "raw": RAW_SKILL_TOP_LEVEL,
    "plugin": PLUGIN_TOP_LEVEL,
}
IGNORED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}
IGNORED_SUFFIXES = (".egg-info", ".pyc", ".pyo", ".zip")


def default_skill_dir() -> Path:
    source_root = Path(__file__).resolve().parents[1]
    if (source_root / "SKILL.md").is_file():
        return source_root
    return Path.cwd()


def parse_skill_frontmatter(skill_md: Path) -> dict[str, str]:
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with YAML frontmatter")
    end = text.find("\n---", 4)
    if end < 0:
        raise ValueError("SKILL.md frontmatter must end with ---")
    return _parse_simple_yaml(text[4:end])


def _parse_simple_yaml(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    lines = raw.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if line[:1].isspace() or ":" not in line:
            raise ValueError(f"unsupported frontmatter line: {line!r}")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value in {">", ">-", "|", "|-"}:
            block: list[str] = []
            index += 1
            while index < len(lines):
                next_line = lines[index]
                if next_line and not next_line[:1].isspace():
                    break
                block.append(next_line.strip())
                index += 1
            if value.startswith(">"):
                result[key] = " ".join(part for part in block if part).strip()
            else:
                result[key] = "\n".join(block).strip()
            continue
        result[key] = _strip_yaml_quotes(value)
        index += 1
    return result


def _strip_yaml_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def check_skill(skill_dir: str | Path) -> dict:
    root = Path(skill_dir).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    metadata: dict[str, str] = {}

    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        errors.append("missing required SKILL.md")
    else:
        try:
            metadata = parse_skill_frontmatter(skill_md)
        except ValueError as exc:
            errors.append(str(exc))

    name = metadata.get("name", "")
    description = metadata.get("description", "")
    if not name:
        errors.append("SKILL.md frontmatter must include name")
    elif not NAME_RE.fullmatch(name):
        errors.append("skill name must be 1-64 lowercase letters/digits with single internal hyphens")
    if "anthropic" in name or "claude" in name:
        errors.append("skill name must not contain reserved provider names")

    if not description:
        errors.append("SKILL.md frontmatter must include description")
    elif len(description) > 1024:
        errors.append("description must be 1024 characters or fewer for Claude compatibility")
    if XML_TAG_RE.search(description):
        errors.append("description must not contain XML tags")

    for required in ("scripts", "references"):
        if not (root / required).is_dir():
            errors.append(f"missing required runtime directory: {required}/")

    openai_yaml = root / "agents" / "openai.yaml"
    if not openai_yaml.is_file():
        warnings.append("agents/openai.yaml is missing; OpenAI UI metadata will be unavailable")
    else:
        openai_text = openai_yaml.read_text(encoding="utf-8")
        for field in OPENAI_FIELDS:
            if not re.search(rf"^\s*{field}\s*:\s*\S", openai_text, flags=re.MULTILINE):
                errors.append(f"agents/openai.yaml missing non-empty {field}")

    plugin_manifest = root / ".codex-plugin" / "plugin.json"
    if plugin_manifest.is_file():
        _validate_plugin_manifest(root, plugin_manifest, name, errors)
    else:
        warnings.append(".codex-plugin/plugin.json is missing; Codex plugin distribution will be unavailable")
    _validate_marketplace(root, name, errors, warnings)

    ignored_found = sorted(_ignored_runtime_paths(root))
    if ignored_found:
        warnings.append("generated or local-only paths will be excluded from skill zips: " + ", ".join(ignored_found))

    return {
        "ok": not errors,
        "skill_dir": str(root),
        "metadata": {
            "name": name,
            "description_length": len(description),
        },
        "targets": {
            "claude": "SKILL.md frontmatter plus bundled scripts/references; no third-party Python dependencies",
            "openai": "raw skill folder plus Codex plugin manifest, repo marketplace, and plugin wrapper skill",
        },
        "errors": errors,
        "warnings": warnings,
    }


def build_skill_zip(skill_dir: str | Path, output: str | Path, *, layout: str = "folder", profile: str = "raw") -> dict:
    root = Path(skill_dir).resolve()
    output_path = Path(output).resolve()
    if profile not in PROFILE_TOP_LEVELS:
        raise ValueError(f"unsupported package profile: {profile}")
    report = check_skill(root)
    if not report["ok"]:
        raise ValueError("skill compatibility check failed")

    skill_name = report["metadata"]["name"] or root.name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    entries: list[str] = []
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for rel, path in _iter_runtime_files(root, output_path, profile):
            archive_name = rel if layout == "root" else f"{skill_name}/{rel}"
            z.write(path, archive_name)
            entries.append(archive_name)
    return {
        "ok": True,
        "output": str(output_path),
        "layout": layout,
        "profile": profile,
        "entries": len(entries),
        "top_level": skill_name if layout == "folder" else None,
    }


def _iter_runtime_files(root: Path, output_path: Path, profile: str):
    top_level = PROFILE_TOP_LEVELS[profile]
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.resolve() == output_path:
            continue
        rel = path.relative_to(root)
        rel_posix = rel.as_posix()
        if rel.parts[0] not in top_level:
            continue
        if _is_ignored(rel):
            continue
        yield rel_posix, path


def _ignored_runtime_paths(root: Path) -> set[str]:
    ignored: set[str] = set()
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] == ".git":
            continue
        if _is_ignored(rel):
            ignored.add(rel.parts[0])
    return ignored


def _is_ignored(rel: Path) -> bool:
    parts = rel.parts
    if any(part in IGNORED_PARTS for part in parts):
        return True
    if any(part.endswith(IGNORED_SUFFIXES) for part in parts):
        return True
    if rel.name in {".DS_Store"}:
        return True
    return False


def _validate_plugin_manifest(root: Path, manifest_path: Path, skill_name: str, errors: list[str]) -> None:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f".codex-plugin/plugin.json must be valid JSON: {exc}")
        return
    if not isinstance(manifest, dict):
        errors.append(".codex-plugin/plugin.json must contain an object")
        return

    for field in ("name", "version", "description", "skills", "interface"):
        if field not in manifest:
            errors.append(f".codex-plugin/plugin.json missing {field}")
    if manifest.get("name") != skill_name:
        errors.append(".codex-plugin/plugin.json name must match SKILL.md name")
    if manifest.get("skills") != "./skills/":
        errors.append(".codex-plugin/plugin.json skills must be ./skills/")
    if not (root / "skills" / skill_name / "SKILL.md").is_file():
        errors.append(f"missing Codex plugin wrapper skill: skills/{skill_name}/SKILL.md")
    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        errors.append(".codex-plugin/plugin.json interface must be an object")
        return
    for field in ("displayName", "shortDescription", "longDescription", "developerName", "category", "capabilities", "defaultPrompt"):
        value = interface.get(field)
        if value in (None, "", []):
            errors.append(f".codex-plugin/plugin.json interface.{field} must be non-empty")


def _validate_marketplace(root: Path, skill_name: str, errors: list[str], warnings: list[str]) -> None:
    marketplace_path = root / ".agents" / "plugins" / "marketplace.json"
    if not marketplace_path.is_file():
        warnings.append(".agents/plugins/marketplace.json is missing; repo-scoped Codex marketplace install will be unavailable")
        return
    try:
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f".agents/plugins/marketplace.json must be valid JSON: {exc}")
        return
    if not isinstance(marketplace, dict):
        errors.append(".agents/plugins/marketplace.json must contain an object")
        return
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        errors.append(".agents/plugins/marketplace.json plugins must be an array")
        return
    entry = next((item for item in plugins if isinstance(item, dict) and item.get("name") == skill_name), None)
    if entry is None:
        errors.append(".agents/plugins/marketplace.json must include a citation-checker plugin entry")
        return
    source = entry.get("source")
    if not isinstance(source, dict) or source.get("source") != "local":
        errors.append(".agents/plugins/marketplace.json citation-checker source must be local")
        return
    path_value = source.get("path")
    if path_value != "./":
        errors.append(".agents/plugins/marketplace.json citation-checker source.path must be ./ for this repo-root plugin")
        return
    if not (root / ".codex-plugin" / "plugin.json").is_file():
        errors.append(".agents/plugins/marketplace.json source.path does not resolve to a plugin root")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and package the citation-checker agent skill")
    parser.add_argument("--skill-dir", default=str(default_skill_dir()), help="Path to the skill folder")
    parser.add_argument("--check", action="store_true", help="Run compatibility checks")
    parser.add_argument("--output", help="Write a skill zip to this path")
    parser.add_argument("--layout", choices=("folder", "root"), default="folder", help="Zip layout")
    parser.add_argument(
        "--profile",
        choices=tuple(PROFILE_TOP_LEVELS),
        default="raw",
        help="Package profile: raw for Claude/OpenAI skill uploads, plugin for Codex plugin distribution",
    )
    args = parser.parse_args(argv)

    if not args.check and not args.output:
        args.check = True

    if args.check:
        report = check_skill(args.skill_dir)
        print(json.dumps(report, indent=2))
        if not report["ok"]:
            return 1

    if args.output:
        try:
            result = build_skill_zip(args.skill_dir, args.output, layout=args.layout, profile=args.profile)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
