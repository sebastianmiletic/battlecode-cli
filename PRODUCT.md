# Product

## Register

product

## Users

UNSW Battlecode competitors on macOS, Linux and Windows. They use a terminal beside their bot source code to inspect results, manage versions and prepare practice matches. First-time users should not need to edit credential files or shell aliases.

## Product Purpose

One terminal dashboard for team status, bot versions, local benchmarks, challenges and replay inspection. Six destinations: Overview, Bots, Games, Arena, Leaderboard and API keys. Account changes are explicit, exactly one saved account is active, and local replay viewing works without an API key.

## Brand Personality

Quiet, precise, dependable. The existing monochrome interface puts operational facts ahead of decoration.

## Anti-references

No decorative dashboards, metric-card grids, animated introductions, chromatic status-only signals or accidental ranked requests. Do not ask users to paste keys into public chats or shell command arguments.

## Design Principles

1. Make the current account and consequential actions unambiguous.
2. Verify keys before saving or switching; never silently reconnect after logout.
3. Preserve context on errors, but clear account-specific state when switching accounts.
4. Offer keyboard and mouse routes through every workflow.
5. Keep local replay files and custom maps local; never invent an unsupported server upload endpoint.
6. Separate reproducible local sandbox comparisons from active-bot server challenges.
7. Show observed and inferred history honestly. Missing records are not fake win rates.
8. Remove redundant navigation and copy. ELO, rank, results and next actions take precedence.

## Accessibility & Inclusion

Use the terminal's monospace and high-contrast text. Outcomes and errors are named, not identified only by color. Support an 80-column terminal, scrolling forms, keyboard focus, mouse clicks and wheel scrolling. Animation is limited to user-controlled replay playback.
