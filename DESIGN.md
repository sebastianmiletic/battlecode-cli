# Design system

## Scene

A Battlecode competitor sits at a laptop with an existing terminal session, inspecting bot results and preparing the next practice match. The requested black-and-white interface stays quiet beside their code and gives potentially rating-changing actions explicit weight.

## Palette

Monochrome by request. Near-black canvas `#0c0c0c`, navigation `#121212`, input surface `#161616`, table headings `#202020`, rules `#333333`, muted copy `#909090`, body `#e8e8e8`. Selection inverts the body and canvas. No gradients, chromatic success/error cues or decorative animation. Errors and match outcomes are always named in text.

## Layout

Persistent masthead, numbered navigation, one work surface, one status line and a keyboard footer. Overview uses two unequal table columns, not metric cards. Below 110 terminal columns the secondary overview table is hidden; full submission data remains available in Bots. Forms scroll vertically in small terminals. Tables support horizontal scrolling for wider records.

## Type and interaction

Use the terminal's own monospace. Bold for team names, section titles and selected rows; muted text for supporting explanations. Navigation is numbered 1 through 7. Tab order follows the visual form order. Enter inspects table rows. Inputs keep typing precedence over navigation shortcuts. No mouse requirement and no color-only signals.

## States

Keep last successful data during a failed refresh and mark it stale. A rejected credential moves first-time users to Settings and pauses polling. Empty tables offer a next action. Background tasks do not block navigation. Demo data is always labelled and mutations are blocked. Confirmations default to Cancel; practice is the default challenge mode. API writes are never automatically retried.
