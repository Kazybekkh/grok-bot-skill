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
python3 scripts/grokbot.py group-remove-member --id GROUP_ID --member-id BOT_ID
python3 scripts/grokbot.py setup-demo --name "Last Drop Demo" \
  --config-json '{"quantity":250,"premiumMaxQuantity":100,"floorPricePence":2400}'
```

`group-create` returns `{group,members,created}`. `setup-demo` returns
`{group,members,created:{bots:[ids],group:boolean},brief,config}`. Group objects
include `id`, `name`, `isGroup:true`, `memberIds`, and runtime state when
available. Each demo member also has `role`. No account-specific IDs are
embedded in the skill.

`group-remove-member` accepts repeated `--member-id` flags and returns
`{group,members,removed,removedMemberIds,alreadyAbsentMemberIds}`. It verifies
ownership and current native membership, removes only specified existing bots,
and retains at least one member. It never deletes a bot or room. Single-member
rooms remain inspectable and readable. Repeating removal of an already absent
member performs no mutation. A departed bot is not silently re-added by setup:
choose a fresh room name for another complete four-bot round.

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

`group-info` and `transcript` read a bounded `WatchGrokBotTranscripts` live-state
snapshot for server-backed agents. This provides current `isRunning` even before
a newly created room is opened in the desktop app. Transport failures return
unknown (`null`), never an invented idle state. Transcript rows retain `streaming`
so consumers can wait for a completed message before acting on its contents.

Membership removal follows the desktop's actual member-removal action:
GrokBotService `SetGrokBotRoomMembers` receives `agentId` and the remaining
`memberAgentIds`, and returns the room agent. Older computer-backed rooms use
`setGroupMembers` with `id` and `memberAgentIds`. The desktop prevents removing
the final bot; the CLI enforces the same rule. Native membership supersedes
stale gateway metadata while runtime flags remain available. Native removals
require a successful fresh control-plane roster read.

Membership writes use the same local process lock as setup. The protocol has
no verified compare-and-swap version, so avoid concurrent membership edits
from another client. On a lost or malformed response, the CLI checks the
authoritative member set and does not resend a stale replacement list.

Autonomous exits are application policy, not a new bot impersonation feature:
the application must validate a withdrawal against its native author identity,
or remove non-winning resellers after a human-approved allocation. The CLI
does not infer approval or withdrawals from free-form chat messages.

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
