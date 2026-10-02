# Product

## Register

product

## Users

UNSW Battlecode competitors on macOS, Linux and Windows. They use a terminal beside their bot source code to inspect results, manage versions and prepare practice matches. First-time users should not need to edit credential files or shell aliases.

## Product Purpose

One interactive control room for team status, submissions, challenges and replay inspection. Account changes are explicit, exactly one saved account is active, and local replay viewing works without an API key.

## Brand Personality

Quiet, precise, dependable. The existing monochrome interface puts operational facts ahead of decoration.

## Anti-references

No decorative dashboards, metric-card grids, animated introductions, chromatic status-only signals or accidental ranked requests. Do not ask users to paste keys into public chats or shell command arguments.

## Design Principles

1. Make the current account and consequential actions unambiguous.
2. Verify keys before saving or switching; never silently reconnect after logout.
3. Preserve context on errors, but clear account-specific state when switching accounts.
4. Offer keyboard and mouse routes through every workflow.
5. Keep local replay files local; never invent an unsupported server upload endpoint.

## Accessibility & Inclusion

Use the terminal's monospace and high-contrast text. Outcomes and errors are named, not identified only by color. Support an 80-column terminal, scrolling forms, keyboard focus, mouse clicks and wheel scrolling. Animation is limited to user-controlled replay playback.
