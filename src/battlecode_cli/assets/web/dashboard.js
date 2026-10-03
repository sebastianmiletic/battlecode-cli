"use strict";

const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
const num = (value) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString(undefined, { maximumFractionDigits: 1 })
    : "n/a";
const date = (value) => {
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime())
    ? parsed.toLocaleString(undefined, {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "n/a";
};
const record = (row) => {
  const r = row.record || row;
  return ["wins", "draws", "losses"].map((key) => num(r[key])).join(" / ");
};
const winrate = (row) => {
  const r = row.record || row;
  const complete = ["wins", "draws", "losses"].every(
    (key) => typeof r[key] === "number",
  );
  const total = r.wins + r.draws + r.losses;
  return complete && total > 0
    ? ((r.wins / total) * 100).toFixed(1) + "%"
    : "n/a";
};
const members = (row) =>
  Array.isArray(row.members)
    ? row.members
        .map((member) =>
          typeof member === "string"
            ? member
            : member.username || member.name || "",
        )
        .filter(Boolean)
        .join(", ")
    : "n/a";
const empty = (columns, text) =>
  `<tr><td colspan="${columns}" class="empty">${esc(text)}</td></tr>`;
let state = null,
  csrf = "",
  page = "overview",
  refreshBusy = false,
  toastTimer,
  lastStateFetch = 0;
let selectedMaps = new Set(),
  mapInitialized = false,
  selectedRun = "",
  runData = null,
  pendingRun = false;
let simulations = [],
  simulationOffset = 0,
  simulationTotal = 0,
  localSignature = "",
  runSignature = "";
const tabs = {
  bots: "bots-versions",
  games: "games-matches",
  arena: "arena-simulation",
};

async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    credentials: "same-origin",
    headers:
      body === undefined
        ? {}
        : { "Content-Type": "application/json", "X-Battlecode-CSRF": csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      data.error ||
        "Could not complete the request. Refresh before trying again.",
    );
  if (data.csrf) csrf = data.csrf;
  return data;
}
function toast(message) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").hidden = false;
  toastTimer = setTimeout(() => {
    $("toast").hidden = true;
  }, 6500);
}
async function busy(button, action) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Working…";
  try {
    return await action();
  } catch (error) {
    toast(error.message);
    return null;
  } finally {
    button.textContent = original;
    button.disabled = false;
  }
}
function options(id, choices, preferred) {
  const select = $(id),
    signature = JSON.stringify(choices);
  if (select.dataset.signature === signature) return;
  const previous = preferred ?? select.value;
  select.innerHTML = choices
    .map(
      ([value, label]) =>
        `<option value="${esc(value)}">${esc(label)}</option>`,
    )
    .join("");
  if (choices.some(([value]) => String(value) === previous))
    select.value = previous;
  select.dataset.signature = signature;
}
function resultClass(value) {
  return /^(win|a|b)$/i.test(value || "")
    ? "win"
    : /loss|error|failed/i.test(value || "")
      ? "loss"
      : "draw";
}
function scoreCells(value) {
  const tokens = String(value).trim().split(/\s+/);
  if (!tokens.length || !tokens.every((item) => /^[WLD.]$/.test(item)))
    return `<small>${esc(value)}</small>`;
  return `<span class="score" aria-label="${esc(tokens.map((item) => ({ W: "Win", L: "Loss", D: "Draw", ".": "Pending" })[item]).join(", "))}">${tokens.map((item) => `<i class="${{ W: "win", L: "loss", D: "draw", ".": "pending" }[item]}"></i>`).join("")}</span>`;
}
function showPage(next) {
  if (
    !["overview", "bots", "games", "arena", "leaderboard", "keys"].includes(
      next,
    )
  )
    next = "overview";
  page = next;
  document.querySelectorAll("main>.page").forEach((element) => {
    element.hidden = element.id !== `page-${page}`;
  });
  document.querySelectorAll("nav a").forEach((element) => {
    if (element.dataset.page === page)
      element.setAttribute("aria-current", "page");
    else element.removeAttribute("aria-current");
  });
  renderHeading();
  if (page === "games")
    loadSimulations().catch((error) => toast(error.message));
  if (page === "arena" && tabs.arena === "arena-results")
    loadRun().catch((error) => toast(error.message));
  document.title = `Battlecode · ${{ overview: "Overview", bots: "Bots", games: "Games", arena: "Arena", leaderboard: "Leaderboard", keys: "API keys" }[page]}`;
}
function showTab(group, panel) {
  tabs[group] = panel;
  const root = $(`page-${group}`);
  root.querySelectorAll(".tab-panel").forEach((element) => {
    element.hidden = element.id !== panel;
  });
  root.querySelectorAll("[role=tab]").forEach((element) => {
    element.setAttribute(
      "aria-selected",
      String(element.dataset.tab === panel),
    );
    element.tabIndex = element.dataset.tab === panel ? 0 : -1;
  });
  if (panel === "arena-results")
    loadRun().catch((error) => toast(error.message));
  if (panel === "games-matches")
    loadSimulations().catch((error) => toast(error.message));
}
function renderHeading() {
  const names = {
    overview: state?.team?.name || "Overview",
    bots: "Bots",
    games: "Games",
    arena: "Arena",
    leaderboard: "Leaderboard",
    keys: "API keys",
  };
  $("page-title").textContent = names[page];
  $("eyebrow").textContent =
    page === "overview" ? "Your team" : state?.team?.name || "Battlecode 2026";
  $("header-action").textContent =
    page === "bots"
      ? "Upload a version"
      : page === "keys"
        ? "Continue offline"
        : page === "games"
          ? "Run a simulation"
          : "Find an opponent";
}
function chart(id, points, isRank = false) {
  const data = points
    .map(([at, value]) => [Date.parse(at), value])
    .filter(([at, value]) => Number.isFinite(at) && Number.isFinite(value));
  if (!data.length) {
    $(id).innerHTML =
      '<p class="muted">History will appear after a recorded observation.</p>';
    return;
  }
  const width = 600,
    height = 184,
    left = 42,
    right = 48,
    top = 15,
    bottom = 33;
  let low = Math.min(...data.map((p) => p[1])),
    high = Math.max(...data.map((p) => p[1]));
  if (low === high) {
    low -= isRank ? 1 : 20;
    high += isRank ? 1 : 20;
  }
  if (isRank) low = Math.max(1, low);
  const start = data[0][0],
    end = data[data.length - 1][0],
    span = end - start;
  const x = (at) =>
    span
      ? left + ((at - start) / span) * (width - left - right)
      : (width + left - right) / 2;
  const y = (value) =>
    top +
    ((isRank ? value - low : high - value) / (high - low)) *
      (height - top - bottom);
  const line = data
    .map(
      ([at, value], i) =>
        `${i ? "L" : "M"}${x(at).toFixed(2)},${y(value).toFixed(2)}`,
    )
    .join(" ");
  const levels = [low, (low + high) / 2, high];
  const ticks = span ? [start, start + span / 2, end] : [start];
  const last = data[data.length - 1];
  $(id).innerHTML =
    `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${isRank ? "Rank" : "ELO"} from ${esc(num(data[0][1]))} to ${esc(num(last[1]))}"><g>${levels.map((v) => `<line class="gridline" x1="${left}" x2="${width - right}" y1="${y(v)}" y2="${y(v)}"/><text x="${left - 8}" y="${y(v) + 4}" text-anchor="end">${esc(num(v))}</text>`).join("")}</g><path class="plot-line" d="${line}"/><circle class="plot-dot" cx="${x(last[0])}" cy="${y(last[1])}" r="3"/><text class="value-label" x="${x(last[0]) + 8}" y="${y(last[1]) - 7}">${esc(num(last[1]))}</text>${ticks.map((at, i) => `<text x="${x(at)}" y="${height - 5}" text-anchor="${i === 0 && span ? "start" : i === ticks.length - 1 && span ? "end" : "middle"}">${esc(new Date(at).toLocaleDateString(undefined, { day: "numeric", month: "short" }))}</text>`).join("")}</svg>`;
}
function renderOverview() {
  const team = state.team,
    rec = team.record || team;
  const rating = team.elo ?? team.rating;
  $("metric-elo").textContent =
    typeof rating === "number" ? String(rating) : "n/a";
  $("metric-rank").textContent = state.rank ? `#${num(state.rank)}` : "n/a";
  $("metric-record").innerHTML = ["wins", "draws", "losses"]
    .map(
      (key, i) =>
        `<div><strong class="${key}">${esc(typeof rec[key] === "number" ? String(rec[key]) : "n/a")}</strong><span>${["W", "D", "L"][i]}</span></div>`,
    )
    .join("");
  $("metric-bot").innerHTML = state.active_bot
    ? `${esc(state.active_bot.name)}<small>${esc(state.active_bot.language)}</small>`
    : "No active bot";
  $("recent-battles").innerHTML =
    state.battles
      .slice(0, 5)
      .map(
        (battle) =>
          `<tr><td>${esc(battle.at)}</td><td>${esc(battle.opponent)}</td><td>${scoreCells(battle.score)}</td><td class="result ${resultClass(battle.result)}">${esc(battle.result.toLowerCase())}</td><td class="numeric ${resultClass(battle.result)}">${esc(battle.change)}</td></tr>`,
      )
      .join("") ||
    empty(
      5,
      state.connected
        ? "No battles yet."
        : "Connect an API key to see your battles.",
    );
  const sorted = [...state.leaderboard].sort(
    (a, b) => (a.rank || Infinity) - (b.rank || Infinity),
  );
  const own = sorted.findIndex((row) => row.id === team.id);
  $("nearby-label").textContent = own >= 0 ? "Around you" : "Top teams";
  const start =
    own >= 0 ? Math.max(0, Math.min(own - 2, sorted.length - 5)) : 0;
  $("nearby-teams").innerHTML =
    sorted
      .slice(start, start + 5)
      .map(
        (row) =>
          `<tr class="${row.id === team.id ? "our-team" : ""}"><td>${esc(row.rank ?? "·")}</td><td><a href="#leaderboard">${esc(row.name)}</a></td><td class="numeric">${esc(num(row.elo ?? row.rating))}</td><td class="numeric"><small>${typeof team.elo === "number" && typeof row.elo === "number" ? (row.elo - team.elo > 0 ? "+" : "") + num(row.elo - team.elo) : "·"}</small></td></tr>`,
      )
      .join("") || empty(4, "Connect an API key to load live standings.");
  chart("elo-chart", state.history.elo);
  chart("rank-chart", state.history.rank, true);
  $("elo-history-origin").textContent = state.history.origin;
  $("rank-history-origin").textContent = state.history.inferred
    ? "Inferred"
    : state.history.rank_origin;
  $("rank-history-note").textContent = state.history.inferred
    ? "Inferred using today's eligible teams; past eligibility is unknown."
    : "Lower is better";
}
function renderBots() {
  $("bot-rows").innerHTML =
    state.submissions
      .map(
        (bot) =>
          `<tr><td class="mono">${esc(bot.version ?? bot.id)}</td><td>${esc(bot.name)} <small>${esc(bot.language)}</small></td><td>${esc(bot.status)}</td><td class="mono">${esc(record(bot))}</td><td class="mono">${esc(winrate(bot))}</td><td><div class="row-actions"><button data-bot-a="${esc(bot.id)}">Arena A</button><button data-bot-b="${esc(bot.id)}">Arena B</button><button data-activate="${esc(bot.id)}" ${state.demo || bot.status === "active" ? "disabled" : ""}>Activate</button></div></td></tr>`,
      )
      .join("") ||
    empty(
      6,
      "Connect an API key to load your versions. Local bots can be used directly in Arena.",
    );
  for (const side of ["a", "b"]) {
    options(`arena-${side}-source`, [
      ["local", "Local ZIP or project folder"],
      ...state.submissions.map((bot) => [
        String(bot.id),
        `${bot.name} · v${bot.version ?? bot.id}`,
      ]),
    ]);
    $(`arena-${side}-local`).hidden =
      $(`arena-${side}-source`).value !== "local";
  }
  $("upload-form").querySelector("button[type=submit]").disabled =
    state.demo || !state.connected;
}
function renderLeaderboard() {
  if (!state) return;
  const search = $("leaderboard-search").value.trim().toLowerCase();
  const filtered = state.leaderboard.filter((row) =>
    `${row.id} ${row.name} ${members(row)}`.toLowerCase().includes(search),
  );
  $("leaderboard-count").textContent = `${filtered.length} teams`;
  $("leaderboard-rows").innerHTML =
    filtered
      .map(
        (row) =>
          `<tr class="${row.id === state.team.id ? "our-team" : ""}"><td class="mono">${esc(row.rank ?? "n/a")}</td><td>${esc(row.name)}</td><td class="mono">${esc(num(row.elo ?? row.rating))}</td><td class="mono">${esc(row.winrate)}</td><td class="mono">${esc(num(row.wins))}</td><td>${esc(members(row))}</td><td><button class="text-button" data-challenge="${esc(row.id)}" ${row.id === state.team.id ? "disabled" : ""}>Challenge</button></td></tr>`,
      )
      .join("") ||
    empty(
      7,
      state.connected
        ? "No teams match your search."
        : "Connect an API key to load the leaderboard.",
    );
}
function visibleMaps() {
  const mode = $("map-set").value;
  return state.maps.filter(
    (map) =>
      mode === "all" ||
      mode === "selection" ||
      (map.origins || []).includes(mode),
  );
}
function renderMaps() {
  if (!mapInitialized) {
    selectedMaps = new Set(
      state.maps
        .filter((map) => map.origins.includes("official"))
        .map((map) => map.id),
    );
    mapInitialized = true;
  }
  const visible = visibleMaps();
  $("map-list").innerHTML =
    visible
      .map(
        (map) =>
          `<label class="map-choice"><input type="checkbox" value="${esc(map.id)}" ${selectedMaps.has(map.id) ? "checked" : ""}><span>${esc(map.name)}<small>${esc(map.width)} × ${esc(map.height)} · ${map.origins.includes("official") ? "Official" : "Custom"}</small></span></label>`,
      )
      .join("") ||
    '<p class="empty">Import a custom map folder or ZIP to build this set.</p>';
  updateSimulationCount();
}
function updateSimulationCount() {
  const count = selectedMaps.size,
    repeats = Number($("arena-repeats").value) || 1,
    seats = $("arena-swap").checked ? 2 : 1;
  $("map-count").textContent = `${count} selected`;
  $("simulation-count").textContent =
    `${num(count * repeats * seats)} games${$("arena-opponents").value.trim() ? " before additional opponents" : ""} · ${count} maps · ${repeats} repeats · ${seats === 2 ? "both seats" : "one seat"}`;
}
function renderArena() {
  $("runner-state").textContent = state.installing
    ? "Installing official runner…"
    : state.install_message ||
      (state.runner
        ? "Official unswbc judge sandbox · local only"
        : "Install the official runner to run simulations.");
  $("install-runner").hidden = state.runner;
  $("install-runner").disabled = state.installing;
  $("run-simulation").disabled =
    state.arena_busy || !state.runner || state.installing;
  options("challenge-map", [
    ["", "Server choice"],
    ...state.online_maps.map((map) => [String(map.id), map.name]),
  ]);
  $("challenge-form").querySelector("button[type=submit]").disabled =
    state.demo || !state.connected;
  const choices = state.runs.map((run) => [
    run.id,
    `${date(run.created_at)} · ${run.status} · ${run.completed}/${run.total} games`,
  ]);
  if (pendingRun && state.running) {
    selectedRun = state.running.id;
    pendingRun = false;
  }
  options(
    "run-select",
    choices.length ? choices : [["", "No simulations yet"]],
    selectedRun,
  );
  if (!selectedRun && $("run-select").value)
    selectedRun = $("run-select").value;
  if (state.running && selectedRun === state.running.id) {
    runData = state.running;
    renderRun();
  }
  if (!state.runs.length && !state.arena_busy) {
    $("run-games").innerHTML = empty(
      7,
      "Run a simulation to save games and inspect replays here.",
    );
  }
  if (state.arena_busy && !state.running) {
    $("run-status").hidden = false;
    $("run-heading").textContent = "Preparing simulation snapshots…";
    $("stop-simulation").hidden = false;
  }
  const signature = JSON.stringify(state.maps.map((map) => map.id));
  if ($("map-list").dataset.signature !== signature) {
    renderMaps();
    $("map-list").dataset.signature = signature;
  }
}
function renderRun() {
  if (!runData) return;
  const run = runData;
  $("run-status").hidden = false;
  $("run-heading").textContent =
    `Simulation · ${run.status} · ${run.games.length}/${run.total} games`;
  $("run-current").textContent =
    run.current ||
    run.error ||
    "Replays are saved automatically. Errors are excluded from win rates.";
  $("run-progress").max = run.total || 1;
  $("run-progress").value = run.games.length;
  $("stop-simulation").hidden = !(
    state?.arena_busy && state?.running?.id === run.id
  );
  $("run-aggregates").innerHTML = run.aggregates
    .map(
      (row) =>
        `<div><h3>${esc(row.label)}</h3><span class="mono">${esc(record(row))}</span> <span class="muted">W / D / L · ${esc(winrate(row))} wins</span><p>${esc(num(row.moves))} moves · ${esc(num(row.growth))} growth · ${esc(num(row.splits))} splits</p></div>`,
    )
    .join("");
  $("run-games").innerHTML =
    run.games
      .map(
        (game) =>
          `<tr><td class="mono">${game.index + 1}</td><td>${esc(game.map)}</td><td>${esc(game.a)}</td><td>${esc(game.b)}</td><td class="${game.error ? "error" : ""}">${esc(game.error ? "Error" : game.winner || "Draw")}</td><td class="mono">${esc(num(game.rounds))}</td><td>${game.replay ? `<button class="text-button" data-replay="${esc(game.replay)}">Watch</button>` : `<small title="${esc(game.error)}">${game.error ? "See report" : "Not saved"}</small>`}</td></tr>`,
      )
      .join("") ||
    empty(
      7,
      "Waiting for the first saved game. You can navigate while the sandbox runs.",
    );
  $("export-json").disabled = false;
  $("export-csv").disabled = false;
  $("run-report").hidden = !run.report;
  if (run.report) $("report-text").textContent = run.report;
}
async function loadRun() {
  if (!selectedRun) return;
  const current = state?.running;
  if (state?.arena_busy && current?.id === selectedRun) {
    runData = current;
    renderRun();
    return;
  }
  const ident = selectedRun;
  const result = await api(`/api/run?id=${encodeURIComponent(ident)}`);
  if (selectedRun === ident) {
    runData = result;
    renderRun();
  }
}
async function loadSimulations() {
  if (!state) return;
  const offset = simulationOffset;
  const result = await api(`/api/simulations?offset=${offset}&limit=100`);
  if (simulationOffset !== offset) return;
  simulations = result.games;
  simulationTotal = result.total;
  renderGames();
}
function renderGames() {
  if (!state) return;
  const filter = $("games-filter").value;
  const online =
    filter === "Simulation"
      ? []
      : state.battles.filter(
          (battle) => filter === "all" || battle.mode === filter,
        );
  const local = filter === "all" || filter === "Simulation" ? simulations : [];
  $("game-count").textContent =
    `${online.length + local.length} shown${local.length ? ` · ${simulationTotal} saved Simulation games` : ""}`;
  const onlineRows = online.map(
    (battle) =>
      `<tr><td>${esc(battle.at)}</td><td>${esc(battle.opponent)}</td><td>${esc(battle.mode)}</td><td class="result ${resultClass(battle.result)}">${esc(battle.result)}</td><td>${scoreCells(battle.score)}</td><td class="mono">${esc(battle.change)}</td><td><button class="text-button" data-online-replay="${esc(battle.id)}">Watch</button></td></tr>`,
  );
  const localRows = local.map(
    (game) =>
      `<tr><td>${esc(date(game.at))}</td><td>${esc(game.a)} vs ${esc(game.b)}</td><td>Simulation</td><td class="result ${game.error ? "error" : "draw"}">${esc(game.error ? "Error" : game.winner ? `Winner ${game.winner}` : "Draw")}</td><td>${esc(game.map)}</td><td class="mono">n/a</td><td>${game.replay ? `<button class="text-button" data-replay="${esc(game.replay)}">Watch</button>` : "Not saved"}</td></tr>`,
  );
  $("game-rows").innerHTML =
    [...onlineRows, ...localRows].join("") ||
    empty(
      7,
      "No games in this view. Start a Simulation in Arena or connect an API key.",
    );
  $("simulation-pagination").hidden =
    !(filter === "all" || filter === "Simulation") || simulationTotal <= 100;
  $("sim-page-label").textContent =
    `Simulation ${simulationOffset + 1}–${Math.min(simulationOffset + 100, simulationTotal)} of ${simulationTotal}`;
  $("sim-previous").disabled = simulationOffset === 0;
  $("sim-next").disabled = simulationOffset + 100 >= simulationTotal;
  $("replay-rows").innerHTML =
    state.replays
      .map(
        (replay) =>
          `<tr><td>${esc(replay.name)}</td><td>${esc(replay.map)}</td><td>${esc(replay.mode || "Replay")}</td><td class="mono">${esc(replay.rounds)}</td><td>${esc(replay.winner)}</td><td><div class="row-actions"><button class="text-button" data-replay="library:${esc(replay.id)}">Watch</button><button class="text-button" data-remove-replay="${esc(replay.id)}">Remove</button></div></td></tr>`,
      )
      .join("") ||
    empty(
      6,
      "Import a replay or run a Simulation. You can also try the sample.",
    );
}
function renderAccounts() {
  $("account-summary").textContent = state.demo
    ? "Demo: synthetic data. Account changes and online mutations are disabled."
    : state.connected
      ? "One account is connected. Switching clears the previous team's data."
      : "Offline. Connect a key for live data, or keep using local simulations and replays.";
  $("account-rows").innerHTML =
    state.accounts
      .map(
        (account) =>
          `<tr><td>${esc(account.label)}</td><td>${esc(account.team)}</td><td>${account.connected ? "ACTIVE" : account.active ? "Selected elsewhere" : "Saved"}</td><td>${account.storage === "keyring" ? "OS keyring" : "Private file (unencrypted)"}</td><td><div class="row-actions"><button data-account-kind="${account.connected ? "disconnect" : "use-account"}" data-account-id="${esc(account.id)}">${account.connected ? "Disconnect" : "Connect"}</button><button data-account-kind="delete-account" data-account-id="${esc(account.id)}">Delete</button></div></td></tr>`,
      )
      .join("") ||
    empty(5, "No saved keys. Add one below, or continue offline.");
  $("key-form")
    .querySelectorAll("button,input")
    .forEach((control) => {
      control.disabled = state.demo && control.id !== "continue-offline";
    });
}
function render() {
  renderHeading();
  renderOverview();
  renderBots();
  renderArena();
  renderGames();
  renderLeaderboard();
  renderAccounts();
  $("notice").hidden = !(state.error || state.demo);
  $("notice").textContent =
    state.error ||
    "Demo: synthetic account data, not your live rating or record. Local simulation results, if any, are real saved runs.";
  $("sync-time").textContent = state.updated
    ? `Updated ${date(state.updated * 1000)}`
    : state.connected
      ? "No successful sync yet"
      : "Offline";
  $("footer-label").textContent = state.demo
    ? "Demo account data · local files stay local"
    : "Unofficial local client · not the Battlecode website";
}
async function refresh(force = false) {
  if (refreshBusy) return;
  refreshBusy = true;
  try {
    const next = await api(
      force ? "/api/refresh" : "/api/state",
      force ? {} : undefined,
    );
    state = next;
    lastStateFetch = Date.now();
    render();
    const local = JSON.stringify(
      state.runs.map((run) => [run.id, run.completed, run.status]),
    );
    if (local !== localSignature) {
      localSignature = local;
      if (page === "games") await loadSimulations();
      if (
        page === "arena" &&
        tabs.arena === "arena-results" &&
        !state.arena_busy
      )
        await loadRun();
    }
    const currentSignature = state.running
      ? `${state.running.id}:${state.running.status}`
      : "";
    if (
      currentSignature !== runSignature &&
      state.running?.status !== "running" &&
      selectedRun === state.running?.id
    )
      await loadRun();
    runSignature = currentSignature;
  } catch (error) {
    $("notice").hidden = false;
    $("notice").textContent = error.message;
  } finally {
    refreshBusy = false;
  }
}
async function review(body) {
  const proposal = await api("/api/review", body);
  $("confirm-title").textContent = proposal.title;
  $("confirm-text").textContent = proposal.text;
  $("confirm-accept").textContent = proposal.label;
  const dialog = $("confirmation");
  dialog.returnValue = "cancel";
  dialog.showModal();
  const approved = await new Promise((resolve) =>
    dialog.addEventListener(
      "close",
      () => resolve(dialog.returnValue === "approve"),
      { once: true },
    ),
  );
  if (!approved) return null;
  const result = await api("/api/approve", { approval: proposal.approval });
  if (result.filename) {
    const blob = new Blob([result.content], {
      type: result.filename.endsWith(".csv") ? "text/csv" : "application/json",
    });
    const url = URL.createObjectURL(blob),
      link = document.createElement("a");
    link.href = url;
    link.download = result.filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }
  return result;
}
function botChoice(side) {
  const value = $(`arena-${side}-source`).value;
  return value === "local"
    ? $(`arena-${side}-path`).value
    : { submission: Number(value) };
}
function formAction(id, action) {
  $(id).addEventListener("submit", (event) => {
    event.preventDefault();
    busy(event.submitter || $(id).querySelector("button[type=submit]"), action);
  });
}

// Native dialog, Canvas board, and a real draggable timeline. Frames are fetched in bounded pages.
let playback = null,
  playTimer = null,
  playing = false,
  playbackSession = 0,
  seekVersion = 0,
  boardTransform = null;
function stopPlaying() {
  playing = false;
  clearTimeout(playTimer);
  $("replay-play").textContent = "Play";
}
async function watch(ident) {
  stopPlaying();
  const session = ++playbackSession;
  const data = await api(
    `/api/replay?id=${encodeURIComponent(ident)}&offset=0&limit=40`,
  );
  if (session !== playbackSession) return;
  playback = {
    id: ident,
    data,
    frames: new Map(data.frames.map((frame, index) => [index, frame])),
    index: 0,
    zoom: 1,
    panX: 0,
    panY: 0,
  };
  $("player-title").textContent = data.summary.map;
  $("player-mode").textContent =
    ident.startsWith("run:") ||
    state?.replays.find((row) => `library:${row.id}` === ident)?.mode ===
      "Simulation"
      ? "Simulation"
      : ident === "sample"
        ? "Sample replay"
        : "Replay";
  $("player-bot-a").textContent = `A · ${data.summary.bots.A}`;
  $("player-bot-b").textContent = `B · ${data.summary.bots.B}`;
  $("player-bot-a").title = data.summary.bots.A;
  $("player-bot-b").title = data.summary.bots.B;
  $("replay-timeline").max = data.summary.frames - 1;
  $("replay-jump").max = data.summary.frames - 1;
  $("replay-outcome").textContent =
    `${data.summary.winner ? `Winner ${data.summary.winner}` : "Draw / unfinished"} · ${data.summary.end_reason} · ${data.summary.rounds} rounds · round-end playback`;
  if (!$("player").open) $("player").showModal();
  await seek(0);
}
async function seek(value) {
  if (!playback) return;
  const version = ++seekVersion,
    session = playbackSession,
    item = playback;
  const index = Math.max(
    0,
    Math.min(item.data.summary.frames - 1, Math.round(Number(value) || 0)),
  );
  if (!item.frames.has(index)) {
    const offset = Math.floor(index / 40) * 40;
    $("replay-position").textContent = "Loading frames…";
    const data = await api(
      `/api/replay?id=${encodeURIComponent(item.id)}&offset=${offset}&limit=40`,
    );
    if (session !== playbackSession) return;
    data.frames.forEach((frame, i) => item.frames.set(offset + i, frame));
  }
  if (session !== playbackSession || version !== seekVersion) return;
  item.index = index;
  const frame = item.frames.get(index);
  $("replay-timeline").value = index;
  $("replay-jump").value = index;
  $("replay-position").textContent =
    `Frame ${index}/${item.data.summary.frames - 1} · ${frame.round < 0 ? "Initial state" : `Round ${frame.round}`}`;
  for (const [side, i] of [
    ["a", 0],
    ["b", 1],
  ])
    $("stats-" + side).innerHTML = [
      ["Living dragons", "living"],
      ["Total length", "total"],
      ["Longest", "longest"],
      ["Queen length", "queen"],
      ["Deaths", "deaths"],
      ["Splits", "splits"],
    ]
      .map(
        ([label, key]) =>
          `<dt>${label}</dt><dd>${esc(frame.stats[i][key])}</dd>`,
      )
      .join("");
  $("replay-events").textContent = frame.events.length
    ? frame.events.join("\n")
    : "No events in this frame.";
  drawBoard();
}
function drawBoard() {
  if (!playback || !$("player").open) return;
  const canvas = $("replay-board"),
    rect = canvas.getBoundingClientRect(),
    dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(rect.width * dpr));
  canvas.height = Math.max(1, Math.round(rect.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const board = playback.data.map,
    frame = playback.frames.get(playback.index);
  const unit =
    Math.min(
      (rect.width - 22) / board.width,
      (rect.height - 22) / board.height,
    ) * playback.zoom;
  const ox = (rect.width - board.width * unit) / 2 + playback.panX,
    oy = (rect.height - board.height * unit) / 2 + playback.panY;
  boardTransform = { ox, oy, unit };
  ctx.fillStyle = "#0c0e12";
  ctx.fillRect(0, 0, rect.width, rect.height);
  ctx.save();
  ctx.beginPath();
  ctx.rect(ox, oy, unit * board.width, unit * board.height);
  ctx.clip();
  if (unit >= 6) {
    ctx.strokeStyle = "#20252c";
    ctx.lineWidth = 0.5;
    ctx.beginPath();
    for (let x = 0; x <= board.width; x++) {
      ctx.moveTo(ox + x * unit, oy);
      ctx.lineTo(ox + x * unit, oy + board.height * unit);
    }
    for (let y = 0; y <= board.height; y++) {
      ctx.moveTo(ox, oy + y * unit);
      ctx.lineTo(ox + board.width * unit, oy + y * unit);
    }
    ctx.stroke();
  }
  for (const [x, y] of board.fountains) {
    ctx.fillStyle = "#253a3d";
    ctx.fillRect(ox + x * unit, oy + y * unit, unit, unit);
  }
  for (const [x, y] of frame.pearls) {
    ctx.fillStyle = "#dedad0";
    ctx.beginPath();
    ctx.arc(
      ox + (x + 0.5) * unit,
      oy + (y + 0.5) * unit,
      Math.max(1, unit * 0.13),
      0,
      Math.PI * 2,
    );
    ctx.fill();
  }
  for (const dragon of frame.dragons) {
    ctx.fillStyle = dragon.team === 0 ? "#85bfc8" : "#d5b28d";
    for (const [x, y] of dragon.body)
      ctx.fillRect(
        ox + (x + 0.16) * unit,
        oy + (y + 0.16) * unit,
        unit * 0.68,
        unit * 0.68,
      );
    const [x, y] = dragon.body[0];
    ctx.fillStyle = dragon.team === 0 ? "#bae3e8" : "#f0d4b0";
    ctx.fillRect(
      ox + (x + 0.08) * unit,
      oy + (y + 0.08) * unit,
      unit * 0.84,
      unit * 0.84,
    );
    if (board.queens.includes(dragon.id)) {
      ctx.strokeStyle = "#eceef1";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(
        ox + (x + 0.08) * unit,
        oy + (y + 0.08) * unit,
        unit * 0.84,
        unit * 0.84,
      );
    }
    if (unit >= 16) {
      ctx.fillStyle = "#15181d";
      ctx.font = `600 ${Math.min(12, unit * 0.5)}px system-ui`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(
        String(dragon.id),
        ox + (x + 0.5) * unit,
        oy + (y + 0.5) * unit,
      );
    }
  }
  ctx.restore();
  for (const [edges, horizontal] of [
    [board.horizontal, true],
    [board.vertical, false],
  ])
    for (const [x, y, type] of edges) {
      ctx.strokeStyle = type === 1 ? "#adb5be" : "#a293c6";
      ctx.lineWidth = Math.max(1, Math.min(2, unit * 0.1));
      ctx.setLineDash(type === 2 ? [3, 3] : []);
      ctx.beginPath();
      ctx.moveTo(ox + x * unit, oy + y * unit);
      ctx.lineTo(
        ox + (x + (horizontal ? 1 : 0)) * unit,
        oy + (y + (horizontal ? 0 : 1)) * unit,
      );
      ctx.stroke();
    }
  ctx.setLineDash([]);
}
async function playTick() {
  if (!playing || !playback) return;
  if (playback.index >= playback.data.summary.frames - 1) {
    stopPlaying();
    return;
  }
  try {
    await seek(playback.index + 1);
  } catch (error) {
    stopPlaying();
    toast(error.message);
    return;
  }
  if (playing)
    playTimer = setTimeout(
      playTick,
      Math.max(30, 200 / Number($("replay-speed").value)),
    );
}
function togglePlay() {
  if (playing) stopPlaying();
  else {
    playing = true;
    $("replay-play").textContent = "Pause";
    if (playback.index >= playback.data.summary.frames - 1)
      seek(0)
        .then(playTick)
        .catch((error) => toast(error.message));
    else playTick();
  }
}

// Events: values from files, sources, server teams, logs, and replays are never interpreted as HTML.
document.addEventListener("click", async (event) => {
  const button = event.target.closest("button"),
    link = event.target.closest('a[href^="#"]');
  if (link && link.hash !== "#main") {
    showPage(link.hash.slice(1));
    return;
  }
  if (!button) return;
  if (button.dataset.tab) {
    showTab(button.dataset.tab.split("-")[0], button.dataset.tab);
    return;
  }
  if (button.dataset.file) {
    $("file-picker").dataset.kind = button.dataset.file;
    $("file-picker").dataset.target = button.dataset.target;
    $("file-picker").accept = {
      bot: ".zip",
      maps: ".zip,.map,.txt",
      replay: ".replay,.gz,.json",
    }[button.dataset.file];
    $("file-picker").value = "";
    $("file-picker").click();
    return;
  }
  if (
    ![
      "replay",
      "onlineReplay",
      "botA",
      "botB",
      "challenge",
      "activate",
      "accountKind",
      "removeReplay",
    ].some((key) => button.dataset[key])
  )
    return;
  await busy(button, async () => {
    if (button.dataset.replay) return watch(button.dataset.replay);
    if (button.dataset.onlineReplay) {
      const result = await api("/api/online-replay", {
        id: Number(button.dataset.onlineReplay),
      });
      await refresh();
      return watch(result.replay);
    }
    if (button.dataset.botA || button.dataset.botB) {
      const side = button.dataset.botA ? "a" : "b";
      location.hash = "arena";
      showPage("arena");
      showTab("arena", "arena-simulation");
      $(`arena-${side}-source`).value =
        button.dataset.botA || button.dataset.botB;
      $(`arena-${side}-local`).hidden = true;
      return;
    }
    if (button.dataset.challenge) {
      location.hash = "arena";
      showPage("arena");
      showTab("arena", "arena-online");
      $("challenge-team").value = button.dataset.challenge;
      $("challenge-mode").value = "unranked";
      $("online-map-label").hidden = false;
      return;
    }
    if (button.dataset.activate) {
      if (
        await review({ kind: "activate", id: Number(button.dataset.activate) })
      ) {
        toast("Activation sent. Refreshing server state.");
        await refresh();
      }
      return;
    }
    if (button.dataset.accountKind) {
      if (
        await review({
          kind: button.dataset.accountKind,
          id: button.dataset.accountId,
        })
      ) {
        toast("Account updated.");
        await refresh();
      }
      return;
    }
    if (button.dataset.removeReplay) {
      if (
        await review({ kind: "remove-replay", id: button.dataset.removeReplay })
      ) {
        toast("Library copy removed; originals preserved.");
        await refresh();
      }
      return;
    }
  });
});
$("file-picker").addEventListener("change", async () => {
  const picker = $("file-picker"),
    file = picker.files[0];
  if (!file) return;
  const maximum = {
    bot: 4 * 1024 * 1024,
    maps: 128 * 1024 * 1024,
    replay: 64 * 1024 * 1024,
  }[picker.dataset.kind];
  if (file.size > maximum) {
    toast(`File exceeds ${maximum / 1024 / 1024} MB.`);
    return;
  }
  try {
    const response = await fetch(`/api/file?kind=${picker.dataset.kind}`, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Battlecode-CSRF": csrf,
        "X-File-Name": encodeURIComponent(file.name),
      },
      body: file,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    $(picker.dataset.target).value = data.path;
    toast(`${file.name} selected. Local only.`);
  } catch (error) {
    toast(error.message);
  }
});
$("header-action").addEventListener("click", () => {
  if (page === "bots") showTab("bots", "bots-upload");
  else if (page === "keys") {
    location.hash = "overview";
    showPage("overview");
  } else {
    location.hash = "arena";
    showPage("arena");
    showTab("arena", page === "games" ? "arena-simulation" : "arena-online");
  }
});
$("refresh").addEventListener("click", () =>
  busy($("refresh"), () => refresh(true)),
);
$("map-set").addEventListener("change", () => {
  const mode = $("map-set").value;
  if (mode !== "selection")
    selectedMaps = new Set(visibleMaps().map((map) => map.id));
  renderMaps();
});
$("map-list").addEventListener("change", (event) => {
  const box = event.target;
  if (box.type !== "checkbox") return;
  if (box.checked) selectedMaps.add(box.value);
  else selectedMaps.delete(box.value);
  $("map-set").value = "selection";
  updateSimulationCount();
});
$("maps-select-all").addEventListener("click", () => {
  selectedMaps = new Set(visibleMaps().map((map) => map.id));
  renderMaps();
});
$("maps-clear").addEventListener("click", () => {
  selectedMaps.clear();
  renderMaps();
});
for (const side of ["a", "b"])
  $(`arena-${side}-source`).addEventListener("change", () => {
    $(`arena-${side}-local`).hidden =
      $(`arena-${side}-source`).value !== "local";
  });
for (const id of [
  "arena-repeats",
  "arena-seed",
  "arena-swap",
  "arena-opponents",
])
  $(id).addEventListener("input", updateSimulationCount);
$("leaderboard-search").addEventListener("input", renderLeaderboard);
$("games-filter").addEventListener("change", renderGames);
$("run-select").addEventListener("change", () => {
  selectedRun = $("run-select").value;
  loadRun().catch((error) => toast(error.message));
});
$("install-runner").addEventListener("click", () =>
  busy($("install-runner"), async () => {
    if (await review({ kind: "install" })) {
      toast("Runner installation started.");
      await refresh();
    }
  }),
);
$("import-maps").addEventListener("click", () =>
  busy($("import-maps"), async () => {
    const result = await review({
      kind: "maps",
      path: $("map-import-path").value,
    });
    if (result) {
      toast(
        `${result.added} added · ${result.duplicates} duplicates · ${result.rejected.length} rejected`,
      );
      await refresh();
      $("map-set").value = "custom";
      selectedMaps = new Set(visibleMaps().map((map) => map.id));
      renderMaps();
    }
  }),
);
formAction("simulation-form", async () => {
  const result = await review({
    kind: "simulation",
    a: botChoice("a"),
    b: botChoice("b"),
    opponents: $("arena-opponents").value,
    maps: [...selectedMaps],
    repeats: Number($("arena-repeats").value),
    swap: $("arena-swap").checked,
    seed: $("arena-seed").value,
    timeout: Number($("arena-timeout").value),
  });
  if (result) {
    pendingRun = true;
    selectedRun = "";
    runData = null;
    showTab("arena", "arena-results");
    toast("Simulation started. Replays are saved after each game.");
    await refresh();
  }
});
formAction("upload-form", async () => {
  if (
    await review({
      kind: "upload",
      path: $("upload-path").value,
      name: $("upload-name").value,
      description: $("upload-description").value,
    })
  ) {
    toast("Upload sent. Check the server build state before retrying.");
    showTab("bots", "bots-versions");
    await refresh();
  }
});
$("challenge-mode").addEventListener("change", () => {
  $("online-map-label").hidden = $("challenge-mode").value === "ranked";
  $("challenge-note").textContent =
    $("challenge-mode").value === "ranked"
      ? "Five server-selected games. Your ELO can change."
      : "Local sources and custom maps are not sent.";
});
formAction("challenge-form", async () => {
  if (
    await review({
      kind: "challenge",
      team: Number($("challenge-team").value),
      ranked: $("challenge-mode").value === "ranked",
      maps: $("challenge-map").value ? [Number($("challenge-map").value)] : [],
    })
  ) {
    toast("Challenge sent. It was not retried.");
    await refresh();
  }
});
formAction("replay-import", async () => {
  const result = await api("/api/import-replay", {
    path: $("replay-path").value,
  });
  toast("Replay saved locally.");
  await refresh();
  await watch(`library:${result.entry.id}`);
});
async function saveKey(activate) {
  const body = {
    key: $("api-key").value,
    label: $("account-label").value,
    activate,
  };
  $("api-key").value = "";
  const result = await api("/api/account", body);
  body.key = "";
  toast(`${result.label}: ${result.connected ? "connected" : "saved only"}.`);
  await refresh();
  if (result.connected) {
    location.hash = "overview";
    showPage("overview");
  }
}
formAction("key-form", () => saveKey(true));
$("key-save-only").addEventListener("click", () =>
  busy($("key-save-only"), () => saveKey(false)),
);
$("continue-offline").addEventListener("click", () => {
  location.hash = "overview";
  showPage("overview");
});
$("sample-replay").addEventListener("click", () =>
  busy($("sample-replay"), () => watch("sample")),
);
$("stop-simulation").addEventListener("click", () =>
  busy($("stop-simulation"), async () => {
    await api("/api/stop", {});
    toast("Stopping the process tree. Completed games are preserved.");
    await refresh();
  }),
);
for (const format of ["json", "csv"])
  $("export-" + format).addEventListener("click", () =>
    busy($("export-" + format), async () => {
      await review({ kind: "export", id: selectedRun, format });
    }),
  );
$("player-close").addEventListener("click", () => $("player").close());
$("player").addEventListener("close", () => {
  stopPlaying();
  playbackSession++;
});
$("replay-play").addEventListener("click", togglePlay);
for (const [id, step] of [
  ["replay-first", "first"],
  ["replay-prev", -1],
  ["replay-next", 1],
  ["replay-last", "last"],
])
  $(id).addEventListener("click", () => {
    stopPlaying();
    seek(
      step === "first"
        ? 0
        : step === "last"
          ? playback.data.summary.frames - 1
          : playback.index + step,
    ).catch((error) => toast(error.message));
  });
for (const id of ["replay-timeline", "replay-jump"])
  $(id).addEventListener("input", () => {
    stopPlaying();
    seek($(id).value).catch((error) => toast(error.message));
  });
let drag = null;
$("replay-board").addEventListener("pointerdown", (event) => {
  if (event.shiftKey && playback) {
    drag = {
      x: event.clientX,
      y: event.clientY,
      panX: playback.panX,
      panY: playback.panY,
    };
    event.target.setPointerCapture(event.pointerId);
  }
});
$("replay-board").addEventListener("pointermove", (event) => {
  if (drag && playback) {
    playback.panX = drag.panX + event.clientX - drag.x;
    playback.panY = drag.panY + event.clientY - drag.y;
    drawBoard();
  }
});
$("replay-board").addEventListener("pointerup", () => {
  drag = null;
});
$("replay-board").addEventListener(
  "wheel",
  (event) => {
    if (!playback) return;
    event.preventDefault();
    playback.zoom = Math.max(
      1,
      Math.min(8, playback.zoom * (event.deltaY < 0 ? 1.12 : 0.89)),
    );
    if (playback.zoom === 1) {
      playback.panX = 0;
      playback.panY = 0;
    }
    drawBoard();
  },
  { passive: false },
);
$("replay-board").addEventListener("click", (event) => {
  if (!playback || !boardTransform || event.shiftKey) return;
  const rect = event.target.getBoundingClientRect(),
    { ox, oy, unit } = boardTransform;
  const x = Math.floor((event.clientX - rect.left - ox) / unit),
    y = Math.floor((event.clientY - rect.top - oy) / unit),
    map = playback.data.map;
  if (x < 0 || y < 0 || x >= map.width || y >= map.height) return;
  const frame = playback.frames.get(playback.index),
    dragon = frame.dragons.find((d) =>
      d.body.some(([bx, by]) => bx === x && by === y),
    );
  $("tile-inspector").textContent =
    `Tile (${x}, ${y}) · ${dragon ? `Team ${dragon.team === 0 ? "A" : "B"}, dragon ${dragon.id}, length ${dragon.body.length}` : frame.pearls.some(([px, py]) => px === x && py === y) ? "Pearl" : "Empty"} · Wheel to zoom, Shift-drag to pan.`;
});
new ResizeObserver(() => drawBoard()).observe($("replay-board"));
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopPlaying();
});
window.addEventListener("hashchange", () => showPage(location.hash.slice(1)));
document.addEventListener("keydown", (event) => {
  if (
    event.target.matches('[role="tab"]') &&
    ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
  ) {
    const choices = [
      ...event.target.parentElement.querySelectorAll('[role="tab"]'),
    ];
    const current = choices.indexOf(event.target);
    const index =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? choices.length - 1
          : (current + (event.key === "ArrowRight" ? 1 : -1) + choices.length) %
            choices.length;
    event.preventDefault();
    const target = choices[index];
    showTab(target.dataset.tab.split("-")[0], target.dataset.tab);
    target.focus();
    return;
  }
  if (
    event.target.matches("input,textarea,select") ||
    event.ctrlKey ||
    event.metaKey ||
    event.altKey
  )
    return;
  if ($("player").open) {
    if (event.key === " ") {
      event.preventDefault();
      togglePlay();
    }
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      stopPlaying();
      seek(playback.index + (event.key === "ArrowRight" ? 1 : -1));
    }
    return;
  }
  if ($("confirmation").open) return;
  if (/^[1-6]$/.test(event.key)) {
    event.preventDefault();
    location.hash = [
      "overview",
      "bots",
      "games",
      "arena",
      "leaderboard",
      "keys",
    ][Number(event.key) - 1];
  }
});
const pagination = document.createElement("div");
pagination.className = "toolbar";
pagination.id = "simulation-pagination";
pagination.hidden = true;
pagination.innerHTML =
  '<button id="sim-previous">Previous</button><span class="muted" id="sim-page-label"></span><button id="sim-next">Next</button>';
$("games-matches").append(pagination);
$("sim-previous").addEventListener("click", () => {
  simulationOffset = Math.max(0, simulationOffset - 100);
  loadSimulations().catch((error) => toast(error.message));
});
$("sim-next").addEventListener("click", () => {
  simulationOffset += 100;
  loadSimulations().catch((error) => toast(error.message));
});
document.querySelectorAll('[role="tab"]').forEach((tab) => {
  tab.id = "tab-" + tab.dataset.tab;
  tab.setAttribute("aria-controls", tab.dataset.tab);
  tab.tabIndex = tab.getAttribute("aria-selected") === "true" ? 0 : -1;
  $(tab.dataset.tab).setAttribute("aria-labelledby", tab.id);
});
showPage(location.hash.slice(1) || "overview");
refresh().then(() => {
  if (!state?.connected && page === "overview") {
    $("notice").hidden = false;
    $("notice").textContent =
      "Offline. Add an API key for live team data. Arena simulations and saved replays work without one.";
  }
});
setInterval(() => {
  if (
    !document.hidden &&
    (state?.arena_busy ||
      state?.installing ||
      Date.now() - lastStateFetch >= (state?.refresh || 45) * 1000)
  )
    refresh();
}, 2000);
