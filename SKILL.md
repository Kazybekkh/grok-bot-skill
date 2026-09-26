---
name: grok-bot
description: >-
  Create and message Grok Bot teammates and native multi-bot groups, read their
  transcripts, or set up a Last Drop merchant/reseller demo from the CLI.
  Use when the user asks to operate Grok Bot or automate its shared conversations.
license: MIT
metadata:
  author: adamanz
  version: "1.2.0"
  homepage: https://github.com/Kazybekkh/grok-bot-skill
  upstream: https://github.com/adamanz/grok-bot-skill
---

# Grok Bot

Talk to existing Grok Bot teammates and create new ones through the local CLI.
Do not drive the Grok Bot desktop UI and do not call the gateway by hand.

## Workflow

1. Run `status`, then `list`.
2. Message an existing bot with `chat` (waits for a reply) or `send` (fire-and-forget).
3. Create a new bot with `create` only when the user asked for a new teammate.
4. Prefer one focused job per bot. Put standing rules in `--description`. Keep first tasks draft-only.

The CLI is `scripts/grokbot.py` next to this file. Resolve that path from the
installed skill directory (often `~/.cursor/skills/grok-bot/scripts/grokbot.py`).

```bash
SCRIPT="$(dirname "$0")/scripts/grokbot.py"
# or, after a global Cursor install:
SCRIPT="$HOME/.cursor/skills/grok-bot/scripts/grokbot.py"

python3 "$SCRIPT" status
python3 "$SCRIPT" list
python3 "$SCRIPT" chat --name Reed --prompt "What are you waiting on from me?"
python3 "$SCRIPT" create --name Reed --title "Chief of staff" --description "Draft-only daily brief. Never send messages."
python3 "$SCRIPT" transcript --name Reed --limit 20
```

Output is JSON. Summarize it for the user. Never paste tokens, gateway URLs, or raw keychain material into chat.

## Create rules

A new bot needs:

- `--name` — short, unique
- `--title` — one job, not "general helper"
- `--description` — outcome, sources, and what it must not do without approval

Do not create a second bot with the same name unless the user asks. The CLI refuses duplicates unless `--force` is passed.

After create, send one concrete first task with `chat` unless the user only wanted the empty teammate.

## Native groups and Last Drop

Use `group-create --name NAME --member-name BOT ... --reuse` for 2–6 existing,
distinct bots. Repeat `--member-id` instead when names are ambiguous. Reuse
requires the exact name and member set; never silently replace members.
`group-info --id ID` returns the verified group and member identities.

Use `group-remove-member --id GROUP_ID --member-id BOT_ID` to remove a bot from
the native room without deleting its profile or conversation history. Repeat
`--member-id` to remove several bots in one membership update. This is safe to
repeat when the requested bots are already absent, and the room must retain at
least one bot. Only remove members within the user's authorized workflow; for
autonomous withdrawal, a controller must verify that the request came from the
departing bot's own native author identity. A different bot cannot authorize
someone else's departure. See [native group details](references/native-groups.md).

For the Last Drop use case, run:

```bash
python3 "$SCRIPT" setup-demo --name "Last Drop Demo"
```

This creates or reuses Last Drop Merchant, Denim Dan, Bargain Bea, Premium Priya,
and their native group. It returns `group`, `members`, `created`, `config`, and
an editable `brief`. It does not send a message or start negotiation. Reused
profiles are never rewritten; current-round constraints belong in the brief.
Use the returned group ID with `send` only when starting a round is authorized.
Read all responses through `transcript`; preserve the real author identities.

`--config-json` accepts `product`, `quantity`, `askingPricePence`,
`floorPricePence`, `denimBudgetPence`, `bargainBudgetPence`,
`premiumBudgetPence`, and `premiumMaxQuantity`. Prices and budgets are integer
pence; budgets are ceilings, not bids. Defaults are fictional demo stock.

The CLI uses native routes for both older computer-backed and newer server-backed
bots. Mixed-runtime groups and non-owned private bots are rejected. Creation is
serialized locally. Inspect uncertain creation or delivery before retrying;
the CLI never blindly resends a mutation. See [native group details](references/native-groups.md).
This community fork is not an official, version-stable Grok Bot SDK.

## Chat rules

- Resolve bots by `--name` when unique; use `--id` if names collide.
- Use `chat` when the user expects an answer in this conversation.
- Use `send` for long-running work the user will check in the Grok Bot app.
- If `stillRunning` is true, say so and offer to poll again. Do not invent a reply.
- Do not send passwords, API keys, or 2FA codes. Tell the user to take over **Agent Computer** in the Grok Bot app.
- Do not approve local-computer access unless the user explicitly asked for it.

## Safety

- The CLI decrypts the Grok Bot session from Keychain and `sand-secrets.json`. Treat that as a secret broker. Never print or log tokens.
- All of a user's bots share one cloud computer. Do not treat separate bots as a security boundary.
- This is not the Cursor Cloud Agents API (`api.cursor.com/v1/agents`). Do not create a coding cloud agent when the user asked for a Grok Bot.

## Troubleshooting

| Symptom | What to do |
|---|---|
| Not signed in / unexpected token format | Ask the user to open Grok Bot and sign in with Cursor |
| Keychain read failed | Ask them to unlock the login keychain and retry |
| Access not granted | Eligible plans: SuperGrok Heavy, Cursor Ultra, Cursor Teams Premium |
| Computer still starting | Wait and rerun `status` until box is running |
| No bot with that name | `list` and confirm the name |

If the CLI is missing a command the user needs, read `scripts/grokbot.py --help`. Do not invent new gateway routes in chat.
