# Changelog

## 1.0

- Introduced portable Claude/OpenAI/Codex compatibility as a single packaged skill.
- Added plugin metadata and Codex plugin wrapper support.
- Split packaging into raw and plugin distribution profiles with deterministic zip structure.
- Added compatibility checks for SKILL metadata, `agents/openai.yaml`, and Codex wrapper/marketplace wiring.
- Standard-library-only runtime with explicit XML validation safeguards.
- Audit-first workflow with explicit manual-review gates for semantic corrections.
- Added user-facing bio-note handling for supra validation.
- Improved source verification handling and conservative verification reporting.
- Added runtime CLI guardrails for malformed inputs and unsafe package conditions.
- Added public release documentation for installation, compatibility, and test commands.
