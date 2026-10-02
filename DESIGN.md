# Design system

## Scene

A Battlecode competitor sits at a laptop with an existing terminal session, inspecting bot results and preparing the next practice match. The requested black-and-white interface stays quiet beside their code and gives potentially rating-changing actions explicit weight.

## Palette

Monochrome by request. Near-black canvas `#0c0c0c`, navigation `#121212`, input surface `#161616`, table headings `#202020`, rules `#333333`, muted copy `#909090`, body `#e8e8e8`. Selection inverts the body and canvas. No gradients, chromatic success/error cues or decorative animation. Errors and match outcomes are always named in text.

## Layout

Persistent masthead, numbered navigation, one work surface, one status line and a keyboard footer. Overview uses two unequal table columns, not metric cards. Below 110 terminal columns the secondary overview table is hidden; full submission data remains available in Bots. Forms scroll vertically in small terminals. Tables support horizontal scrolling for wider records.

## Type and interaction

Use the terminal's own monospace. Bold for team names, section titles and selected rows; muted text for supporting explanations. Navigation is numbered 1 through 8. Tab order follows the visual form order. Enter inspects table rows. Inputs keep typing precedence over navigation shortcuts. Sidebar, table rows, buttons, selectors and replay timeline support mouse input; keyboard access remains complete. No color-only signals.

## States

Keep last successful data during a failed refresh and mark it stale. A rejected credential moves first-time users to Settings and pauses polling. Empty tables offer a next action. Background tasks do not block navigation. Demo data is always labelled and server mutations and key management are blocked. First launch without a connected account focuses a hidden API-key field; offline replay viewing is one click away. Saved profiles have one active ID, and disconnect/delete never activate a fallback. Account changes clear cached data and invalidate pending writes. Confirmations default to Cancel; practice is the default challenge mode. API writes are never automatically retried.

## Replay surface

Use a full-screen map viewport with a compact, stable toolbar, clickable/draggable timeline, named A/B symbols, statistics and a tile inspector. Detailed mode draws terrain edges; compact mode explicitly labels that edges are hidden. Playback changes only after user action. Frame numbers and round numbers are distinct. Files are local copies, not server uploads.
