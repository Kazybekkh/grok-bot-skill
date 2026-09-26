# Grok Bot Skill

This is a community [Agent Skill](https://agentskills.io) for Grok Bot’s macOS app, forked from [Adam Anzuoni’s MIT-licensed original](https://github.com/adamanz/grok-bot-skill).

Install this fork:

```bash
npx skills add Kazybekkh/grok-bot-skill -g
```

For requests involving Grok Bot teammates, native groups or the Last Drop merchant/reseller demo, follow [SKILL.md](SKILL.md) and use `scripts/grokbot.py`. Read [native group details](references/native-groups.md) for group creation, account ownership, runtime compatibility and uncertain-result handling. Do not use the Cursor Cloud Agents API.

`setup-demo` creates or reuses four bots and a native group, returning an editable brief without starting a negotiation. Starting a round is a separate `send` action. Use configurable quantities, prices and budgets; do not prescribe bids or a winner. Preserve real bot identities when displaying transcripts.

This integration uses the app’s private protocol, not an official SDK. macOS and a signed-in Grok Bot session are still required. Keep credentials local and retain the original MIT attribution.

Run `python3 -m unittest discover -s tests -v` for the mocked regression suite. Tests must not access real credentials, create bots or send messages.
