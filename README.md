# Grok Bot Skill — native groups and Last Drop

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-SKILL.md-111)](https://agentskills.io)

A community skill for Codex, Claude Code and Cursor to create Grok Bot teammates, form native group conversations, send messages and read their real responses from the terminal.

This **1.1.0 fork** extends [Adam Anzuoni’s original `adamanz/grok-bot-skill`](https://github.com/adamanz/grok-bot-skill), retaining his MIT license and attribution. It adds native group creation and a configurable merchant/reseller setup for [Last Drop](https://lastdrop-nine.vercel.app).

## Install

```bash
npx skills add Kazybekkh/grok-bot-skill -g -a codex
npx skills add Kazybekkh/grok-bot-skill -g -a claude-code
npx skills add Kazybekkh/grok-bot-skill -g -a cursor
```

Or install for every supported agent:

```bash
npx skills add Kazybekkh/grok-bot-skill --all
```

For a local checkout:

```bash
git clone https://github.com/Kazybekkh/grok-bot-skill.git
cd grok-bot-skill
python3 scripts/grokbot.py status
python3 scripts/grokbot.py list
```

## Requirements

- macOS with Grok Bot installed and signed in, with access to its cloud computer.
- Python 3; the CLI uses only the standard library.
- The signed-in Mac remains the credential broker. Installing this skill on a website or deploying Last Drop to Vercel does not replace the local connection.

The CLI uses the native app’s authenticated private protocol. This is a community integration, **not an official or version-stable Grok Bot SDK**. Protocol compatibility is documented against Grok Bot 0.59.1. It does not use the Cursor Cloud Agents API.

## Create a native group

Choose two to six distinct existing teammates by their unique names or repeat `--member-id` for exact IDs:

```bash
python3 scripts/grokbot.py group-create --name "Reseller room" \
  --member-name "Last Drop Merchant" --member-name "Denim Dan" \
  --member-name "Bargain Bea" --member-name "Premium Priya" --reuse

python3 scripts/grokbot.py group-info --name "Reseller room"
```

`group-create` returns the group and verified member identities. `--reuse` opens an existing group only when its name and complete member set match. Ambiguous names, different members, non-owned bots and mixed runtimes are rejected instead of silently changing a conversation.

## Set up the Last Drop demo

```bash
python3 scripts/grokbot.py setup-demo --name "Last Drop Demo" \
  --config-json '{"product":"Unworn linen coats","quantity":250,"askingPricePence":3800,"floorPricePence":2400,"denimBudgetPence":850000,"bargainBudgetPence":650000,"premiumBudgetPence":420000,"premiumMaxQuantity":100}'
```

This creates or reuses four independent bots—Last Drop Merchant, Denim Dan, Bargain Bea and Premium Priya—and their native group. The result contains `group`, `members`, `created`, `config` and an editable `brief`. IDs come from the signed-in account; none are embedded in the demo.

Setup **does not start a round or send any messages**. New teammates have automatic kickoff and introductions suppressed. Reused profiles remain unchanged. Repeat setup to reuse the room and recover teammates already created during an interrupted attempt.

Prices and budgets use integer pence. Quantities must be 1–10,000; prices and budgets must be 1–1,000,000,000 pence. The merchant floor cannot exceed the asking price, and the premium buyer’s quantity cannot exceed the lot. Omitted fields use fictional jacket-demo defaults. Budgets are spending ceilings: the bots choose their own offers, quantities and reactions, with no predetermined bids or winner.

Review the returned brief, then explicitly start the round using its group ID:

```bash
python3 scripts/grokbot.py send --id GROUP_ID --prompt "YOUR_REVIEWED_ROUND_BRIEF"
python3 scripts/grokbot.py transcript --id GROUP_ID --limit 80
```

A freshly created native room can be sent messages and read from the CLI without manually opening it in the desktop app. Replies retain each bot’s real identity. The round asks the merchant to compare offers and stop for human approval; it creates no real orders or payments.

## Commands

| Command | Purpose |
|---|---|
| `status` | Check sign-in and cloud computer health |
| `list` | List teammates and conversations |
| `create` / `update` | Create a teammate or edit its profile |
| `send` / `chat` | Send a message, optionally waiting for a response |
| `transcript` | Read recent messages and author identities |
| `group-create` | Create or reuse a native group with 2–6 existing bots |
| `group-info` | Verify a native group and its members |
| `setup-demo` | Create or reuse the four Last Drop bots and group; return a round brief |

See [SKILL.md](SKILL.md) for agent instructions and [native group details](references/native-groups.md) for protocol behavior and output contracts.

## Local session and retries

Credentials remain in macOS Keychain and Grok Bot’s local session store. The CLI never prints tokens, gateway URLs or Keychain material. Do not put passwords, API keys or 2FA codes in prompts. Separate bots share a cloud computer and are not a security boundary.

Mutations are not blindly retried. On an uncertain creation or delivery, the CLI attempts to confirm the result and otherwise asks you to inspect Grok Bot before retrying. Older group creation has no claimed server-side idempotency guarantee.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests load this checkout’s CLI and mock credential, network and mutation operations. No real teammates or messages are created by the test suite.

## License and upstream

[MIT](LICENSE), originally copyright © 2026 Adam Anzuoni. This fork is maintained at [Kazybekkh/grok-bot-skill](https://github.com/Kazybekkh/grok-bot-skill); the original project is [adamanz/grok-bot-skill](https://github.com/adamanz/grok-bot-skill).
