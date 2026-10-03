# Design system

## Scene

A competitor inspects bot results in a dim evening workspace beside a code editor. The terminal reproduces the supplied dark Battlecode site; a light theme is available for daylight use. Operational facts and potentially rating-changing actions take priority over decoration.

## Palette

Match the saved website's dark tokens: canvas `#0b0d10`, sidebar/input surface `#15181d`, raised surface `#1c2026`, rules `#262b33`, body `#eceef1`, muted copy `#8b93a0`, primary/focus `#5fded2`, chart mint `#9ed8cb`, named success `#3ecf7a` and error `#f0605a`. Accent use is restrained. Light mode uses cool near-white surfaces and darker mint/semantic colors for contrast. No gradients or color-only outcome signals.

## Layout

Persistent website-style sidebar with Overview/Updates, Compete, Build and Manage groups. Arena is immediately beneath Submissions. Keep the sidebar scrollable at 80×24; Ctrl+B reduces it to a three-cell restore rail. Main heading includes the project link and explicit opponent action, followed by one work surface, a status line and keyboard footer.

Overview has a divided facts row, recent battles and nearby returned ladder, then rating/rank history panels. Team profiles follow the saved page's facts/charts/recent-battles/member structure, with eligibility, join-request and settings tabs. Missing or unavailable fields display n/a; do not embed reference-team data. Initial profile focus must not scroll past the facts row.

Submissions retains Versions/Upload; Games distinguishes All games, Your games and local replays; Arena retains Simulation/Online/Results. Public match tables have search, mode filters and server-page pagination. Tournaments expose a bounded expandable bracket with battle links. Documentation has the complete 23-topic outline, local original technical notes and inert-text refresh of the current official article.

The map editor has terrain/queen brushes, edge direction, spawn gaps, portal pair ID, a scrollable coordinate board and full source editing. Undo/redo, count normalization, validation, preview, confirmed saves and confirmed Arena imports remain available in compact layouts. Validation errors are always visible. Direct source editing retains official-format directives, including END when present.

Forms scroll vertically; action bars and wider tables scroll horizontally. Fixed-size charts must not push the primary table/actions outside an 80×24 screen. Compact pages stack profile/overview panels, shorten charts and preserve map/replay transport controls.

## Type and interaction

Use the terminal's own monospace. Bold headings/selection and heavy three-line rating/rank figures establish hierarchy. The sidebar labels mirror the website, while shortcuts 1–6 remain Overview, Submissions, Your games, Arena, Leaderboard and API keys. Tab order follows visible controls; Enter opens team/submission/battle details. Inputs keep typing precedence. Ctrl+B/Ctrl+T/Ctrl+K remain available from text controls. Mouse and wheel access is supplementary, never required.

## States and account boundaries

Keep successful snapshots during read failures and mark stale/error states. Rejected keys pause polling. Empty states offer a useful next action. Background work must not reopen hidden panes or steal focus after navigation. Demo is labelled, fully synthetic, credential-free, and unable to mutate server/accounts.

First launch without an account focuses hidden key entry. Exactly one saved profile is active; disconnect/delete never choose a fallback. Account changes clear team/submission/battle/member/rating/directory/bracket data and cancel pending account-bound reads. Verify before saving/switching. Consequential reviews default to Cancel; server writes are never retried automatically. Team settings, stars, membership and account changes are website-only, with explicit links rather than invented API operations.

## Arena and history

Only the official judge sandbox runs bots. Both seats and repeatable seeds are defaults. Fifteen unchanged official maps are bundled offline; map sets and checkbox subsets are native terminal controls. Source/map snapshots, sequential execution, stop, saved batches, recorded diagnostics and reviewed exports preserve reproducibility. Finished-game replay copies are automatically added to the local library, with originals retained if library copying fails.

Local game/history/replay labels are Simulation, never Unranked. Your games includes a bounded local Simulation index; complete original batches remain in Arena Results. Private opponent source is not promised. Reports distinguish recorded movement/deaths/queen timing from causal strategy claims or AI advice.

Charts use server history or clearly described local observations. Reconstructed ranks use today's eligible teams and are labelled inferred. Missing history is named, not drawn as invented points. Win rate requires wins + draws + losses; incomplete and empty records display n/a.

## Browser companion

The optional six-page browser dashboard follows the earlier reference layout with the supplied unchanged PNG and bundled Instrument Sans, IBM Plex Mono and Martian Mono fonts. Keep all licenses and trademark notices. Its fixed sidebar, divided facts row, tables/charts and responsive mobile navigation are separate from the full native TUI navigation.

Use native controls and approval dialogs, with Cancel initially focused. Host/Origin/session/CSRF/CSP protections keep the server loopback-only. Escape untrusted text. Saved keys never go into URLs, localStorage or JavaScript state. Source files, keys and user reference files must not enter documentation screenshots.

## Replay surface

A full-screen map viewport has stable transport, clickable/draggable timeline, speed, frame jump, statistics and tile inspection. A/B heads and Q/R queens remain distinguishable without color. Detailed mode draws edges; compact mode explicitly hides them. Light/dark themes preserve contrast. Playback shows round-end states, not an action-by-action simulator. Sonar overlays and portal-link annotations are not implemented.
