# Native group integration

Verified against Grok Bot desktop 0.59.1. This community fork retains the
upstream MIT license and local Keychain credential broker. It uses the same
authenticated native requests as the app; credentials and signed blob URLs
must never be printed or copied into a website.

## Commands

```bash
python3 scripts/grokbot.py group-create --name "Reseller room" \
  --member-name "Last Drop Merchant" --member-name "Denim Dan" \
  --member-name "Bargain Bea" --member-name "Premium Priya" --reuse
python3 scripts/grokbot.py group-info --name "Reseller room"
python3 scripts/grokbot.py setup-demo --name "Last Drop Demo" \
  --config-json '{"quantity":250,"premiumMaxQuantity":100,"floorPricePence":2400}'
```

`group-create` returns `{group,members,created}`. `setup-demo` returns
`{group,members,created:{bots:[ids],group:boolean},brief,config}`. Group objects
include `id`, `name`, `isGroup:true`, `memberIds`, and runtime state when
available. Each demo member also has `role`. No account-specific IDs are
embedded in the skill.

Setup uses one sign-in session, suppresses new-bot introductions, leaves reused
profiles unchanged, and performs no negotiation sends. Repeat setup safely
reuses exact matching rooms. Duplicate names, mismatched members, and unsupported
runtimes fail with instructions instead of changing an existing conversation.

## Native routes

Older computer-backed groups use the native `createGroup` method with
`name`, `description`, and `memberAgentIds`. New server-backed agents use
GrokBotService `CreateGrokBotTemporalAgent` and `CreateGrokBotRoom`, with a UUID
per creation. `ListGrokBotAgents` verifies the returned identity and members;
the public `agentId` is different from the service's internal `id`.

New rooms need no manual desktop opening: `SendGrokBotUserMessage` sends to
server-backed groups and `ListGrokBotTranscriptEntries` reads their responses.
Messages retain a stable UUID during uncertain-send status checks. Inline and
blob-backed transcript bodies are decoded locally; the frontend receives only
normalized messages with their real authors. The app's private protocol can
change, so rerun compatibility tests when updating Grok Bot.

Creation and sending are not retried blindly. On an uncertain group creation,
the CLI checks the roster for exactly one name/member match. If none is verified,
inspect Grok Bot before a manual retry. No server-side idempotency guarantee is
claimed for older group creation.

## Test

```bash
python3 -m unittest discover -s tests -v
```

Tests mock credential and network operations. A live smoke test should create
one room, repeat setup to verify reuse, send one explicitly fictional round,
and verify responses from the distinct native member identities.
