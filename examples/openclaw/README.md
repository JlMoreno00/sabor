# OpenClaw cooking agent example

Optional workspace instructions adapted from the cooking-planner agent. The
persona and two skills are in Spanish; they are not required by the MCP server.

## Setup

1. Run Sabor using the [main quickstart](../../README.md). Verify its MCP endpoint.
2. Create an OpenClaw agent workspace and copy [SOUL.md](SOUL.md),
   [IDENTITY.md](IDENTITY.md), [AGENTS.md](AGENTS.md), [TOOLS.md](TOOLS.md), and
   the [onboarding](skills/onboarding-interview/SKILL.md) and
   [feedback](skills/feedback-collector/SKILL.md) skill directories into it.
3. Using your OpenClaw version's MCP integration or bridge, register **one**
   Streamable HTTP server named `sabor` at
   `http://127.0.0.1:9170/mcp`. Discover its tools before enabling writes.
   Replace the address when OpenClaw runs in another container or host.
4. Remove the old six-server registrations (Mealie, scraper, Spoonacular,
   memory, Bring!, video). They are now one endpoint; do not run duplicate
   server scripts. [TOOLS.md](TOOLS.md) uses the unified tool names.
5. Start a private conversation and run onboarding before planning meals.
   Store the confirmed profile in a private workspace `USER.md`, not this repo.

Exact MCP bridge and channel configuration depends on the installed OpenClaw
version; this example deliberately does not ship a presumed universal config.

## Telegram (optional)

Create a bot through Telegram's BotFather, store its token in OpenClaw's secret
configuration, enable the Telegram channel and bind it to this agent. Require
pairing or an explicit user allowlist before granting tool access. Test in a
private chat. Do not publish tokens or copy them into persona files.

Use inline buttons for choices when the channel supports them; otherwise accept
plain text. Configure reminders only after agreeing on frequency, timezone and
quiet hours. No heartbeat jobs or unsolicited schedules are included.

## Boundaries

Mealie owns recipes and calendar data; Sabor owns its preference memory and
optional nutrition evidence. The agent's private profile is not automatically
synchronized with Sabor memory. Confirm writes and explain partial failures;
never claim a calendar/list update succeeded without checking its response.
Do not import old seed, cleanup, server-launch or bot-install scripts.
