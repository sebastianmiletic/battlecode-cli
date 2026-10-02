# Claude / Codex integration proposal

**Status: proposal, not implemented.** v0.2 has no AI chat, MCP server or model credentials.

## Why it would be useful

An assistant could compare submission records, explain observed deaths and build failures, identify map-specific weaknesses, and propose a focused practice plan. With an explicitly selected bot workspace, it could propose source patches instead of making you copy logs between a terminal and a chat.

It should distinguish recorded facts from hypotheses. Round-end replays do not include every decision or sonar overlay, and a win-rate difference alone does not establish that one bot is stronger.

## Route 1: MCP tools for Claude Code / Codex

This is the best first step if you already use an agent in your editor or terminal. A future `battlecode-cli mcp --read-only` process would speak MCP over stdio and reuse the app's API/replay modules and explicitly active account. Claude Code and Codex could call these tools from their own chat interfaces.

Proposed read tools:

| Tool | Result |
| --- | --- |
| `get_team_status` | Current team, rating, rank, active bot |
| `list_submissions` | Version records and completion counts |
| `get_submission_details` | Build log and per-map results |
| `list_battles` / `get_battle` | Recent games and outcomes |
| `get_replay_summary` | Map, round counts, recorded deaths/splits and lengths |

Structured JSON would be easier for agents than scraping the rendered TUI. MCP stdout must contain only protocol messages; diagnostics belong on stderr and must be redacted. API keys are read inside the backend, never returned in tool results or passed as command arguments.

Begin with read-only tools. A later write workflow should produce a **proposal**, not execute an API call. The dashboard would show the exact bot digest, account, opponent, mode and maps; the user would approve it there. Approval must be single-use, short-lived and bound to the current account and exact payload. Switching/disconnecting invalidates outstanding proposals. Ranked mode remains an explicit choice, and writes are never automatically retried.

There is no working MCP command to register yet. Today an existing agent can use the read-only `battlecode-cli status` output and `battlecode-cli replay FILE --info` JSON, with your permission, without being given the Battlecode API key.

## Route 2: chat inside the dashboard

A new Chat view could host a session alongside the existing pages:

1. Choose Claude or Codex/OpenAI, using a supported official SDK or CLI integration.
2. Authenticate separately using the provider's supported API/CLI mechanism. A Battlecode key is not an AI-provider key. Subscription access and API billing are separate where the provider requires them.
3. Explicitly attach a selected submission, build log, battle/replay summary or bot workspace.
4. Stream the response asynchronously, with cancellation and a visible tool/activity history.
5. Show proposed code changes as a diff. Apply only after approval; do not run source code or shell commands by default.

An adapter interface should separate provider-specific authentication/streaming from a shared, policy-enforcing tool layer. Existing API/replay functions can supply data, but the shared layer must enforce the approval rules; a model's instruction to "confirm" is not approval.

This is more work than MCP: provider setup, streaming/cancellation, context selection, token/cost limits, conversation persistence, source-diff review, and cross-platform CLI lifecycle handling all need implementation.

## Privacy and guardrails

- Sending logs, replay summaries or source to a cloud model is opt-in. Local replay import alone never sends data to an AI provider.
- Keep AI-provider secrets separate from Battlecode secrets in the OS keyring. Never include either in prompts, tool output, transcripts or screenshots.
- Build logs, team names, bot descriptions and replay contents are **untrusted data**, not instructions. They must not grant access to tools, additional files or credentials.
- Use the currently connected account only. Do not give the model tools to enumerate secrets, switch accounts, import keys or delete keys.
- Filesystem access is limited to the explicitly chosen workspace. Reject traversal and symlink escapes, and exclude credential/environment files.
- Default to read-only and practice. Server mutations need a fresh human approval; account changes and timeouts invalidate stale approvals.
- Bound context size, tool-call rate, session duration and spending. Keep audit entries to action metadata, not secrets or complete private source.

**Recommendation:** read-only MCP first, then proposal/approval workflows, then an optional native Chat view. This gets useful analysis into Claude Code/Codex quickly without turning an assistant into an autonomous ranked-match launcher.
