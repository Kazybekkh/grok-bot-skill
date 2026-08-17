# Examples

Resolve the CLI from the installed skill directory:

```bash
SCRIPT="$HOME/.cursor/skills/grok-bot/scripts/grokbot.py"
```

## Check that Grok Bot is signed in

```bash
python3 "$SCRIPT" status
```

## List teammates

```bash
python3 "$SCRIPT" list
python3 "$SCRIPT" list --full
```

## Chat and wait for a reply

```bash
python3 "$SCRIPT" chat --name Reed --prompt "What are you waiting on from me?"
```

## Send without waiting

```bash
python3 "$SCRIPT" send --name Reed --prompt "Draft today's brief. Do not send anything."
```

## Create a focused teammate

```bash
python3 "$SCRIPT" create \
  --name Reed \
  --title "Chief of staff" \
  --description "Draft-only daily brief from calendar and inbox. Never send messages or approve spend."
```

Then give it one first task:

```bash
python3 "$SCRIPT" chat --name Reed --prompt "Ask me for 3-5 priorities, then draft a brief."
```

## Read recent messages

```bash
python3 "$SCRIPT" transcript --name Reed --limit 20
```

## Update standing rules

```bash
python3 "$SCRIPT" update --name Reed --title "Chief of staff" \
  --description "Draft-only. Never send Slack, email, or calendar invites."
```

## Prompts that should trigger this skill

- “Message my Grok Bot”
- “List my Grok Bots”
- “Create a Grok Bot named Reed as chief of staff”
- “What did Reed say last?”
- “Talk to Grok Bot from Cursor, not the desktop app”
