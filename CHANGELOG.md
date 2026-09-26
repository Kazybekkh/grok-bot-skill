# Changelog

## 1.1.0 — 2026-09-26

Community fork at [Kazybekkh/grok-bot-skill](https://github.com/Kazybekkh/grok-bot-skill), based on Adam Anzuoni’s MIT-licensed upstream.

- Add `group-create` and `group-info` for native groups of 2–6 distinct existing bots, with exact membership verification and optional reuse.
- Add `setup-demo` to create or reuse one merchant and three independent reseller bots, returning their native group and an editable round brief.
- Make lot details, prices, budgets and quantities configurable. Budgets are constraints, not scripted bids or predetermined outcomes.
- Separate setup from sending: suppress new-bot kickoff and introductions, preserve reused profiles, and never start a round automatically.
- Support native server-backed bot and room creation alongside older computer-backed groups; reject incompatible mixed runtimes and ownership conflicts.
- Send to and read freshly created native rooms without opening them manually in the desktop app. Preserve message author identities and decode native transcript bodies locally.
- Reconcile uncertain creations and sends without blindly resending mutations; reuse verified teammates after partial setup.
- Update compatibility for active-account session storage, current safe-storage envelopes and the installed Grok Bot version.
- Add mocked regression coverage for groups, demo setup, native messages, transcripts and credential compatibility.

This remains a macOS community integration of Grok Bot’s private protocol, not an official SDK.

## 1.0.0 — 2026-08-17

Original release by Adam Anzuoni at [adamanz/grok-bot-skill](https://github.com/adamanz/grok-bot-skill).

- First public release of the Grok Bot Agent Skill.
- CLI: `status`, `list`, `create`, `update`, `send`, `chat`, `transcript`.
- macOS session via Grok Bot desktop app, with no third-party Python dependencies.
