# Design system

## Scene

A Battlecode competitor sits at a laptop with an existing terminal session, inspecting bot results and preparing the next practice match. The requested black-and-white interface stays quiet beside their code and gives potentially rating-changing actions explicit weight.

## Palette

Monochrome by request. Near-black canvas `#0c0c0c`, navigation `#121212`, input surface `#161616`, table headings `#202020`, rules `#333333`, muted copy `#909090`, body `#e8e8e8`. Selection inverts the body and canvas. No gradients, chromatic success/error cues or decorative animation. Errors and match outcomes are always named in text.

## Layout

Persistent masthead, six numbered destinations, one work surface, one status line and a keyboard footer. The top-right project link replaces connection/setup chrome; account state stays in API keys and the status line. Overview places large, heavy three-line ELO/rank numerals above two time-scaled history plots, without metric cards. Bots combines Versions/Upload tabs; Games combines Matches/Local replays; Arena combines Benchmark/Online/Results. Forms scroll vertically. Action bars and wider tables scroll horizontally. Benchmark review/stop remains reachable at 80×24.

## Type and interaction

Use the terminal's own monospace. Bold for team names, section titles and selected rows; muted text for supporting explanations. Navigation is numbered 1 through 6. Tab order follows the visual form order. Enter inspects table rows. Inputs keep typing precedence over navigation shortcuts. Sidebar, table rows, buttons, selectors and replay timeline support mouse input; keyboard access remains complete. No color-only signals.

## States

Keep last successful data during a failed refresh and mark it stale. A rejected credential moves first-time users to Settings and pauses polling. Empty tables offer a next action. Background tasks do not block navigation. Demo data is always labelled and server mutations and key management are blocked. First launch without a connected account focuses a hidden API-key field; offline replay viewing is one click away. Saved profiles have one active ID, and disconnect/delete never activate a fallback. Account changes clear cached data and invalidate pending writes. Confirmations default to Cancel; practice is the default challenge mode. API writes are never automatically retried.

## Arena and history

Local benchmarks require explicit review and use the official judge sandbox, never a native fallback. Both seats and repeatable seeds are defaults. Custom map imports stay local; private opponent source is not promised. Live progress, saved batches, per-map records, complete recorded-move diagnostics and confirmed exports share one Results tab. Interrupted batches keep completed results. Advice is rule-based and names uncertainty.

History uses server data and local observations. Reconstructed rank is labelled inferred because past eligibility and tie ordering are unavailable. Empty history is named, not drawn as fake data. The leaderboard lists all returned teams/members, ELO and win rate only when a denominator exists.

## Replay surface

Use a full-screen map viewport with a compact, stable toolbar, clickable/draggable timeline, named A/B symbols, statistics and a tile inspector. Detailed mode draws terrain edges; compact mode explicitly labels that edges are hidden. Playback changes only after user action. Frame numbers and round numbers are distinct. Files are local copies, not server uploads.
