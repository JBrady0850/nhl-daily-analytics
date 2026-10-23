/* NHL Daily Analytics. Plain JavaScript, no build step: runs from GitHub Pages,
   from a downloaded folder (file://) or any static host. The day's numbers come
   from data/latest.js, written by the daily build; your settings (My Teams, the
   stats window) stay in this browser (localStorage). */
(function () {
  "use strict";
  const APP_VERSION = "1.0.0";
  const REPO = "https://github.com/JBrady0850/nhl-daily-analytics";
  const PUBLIC_URL = "https://jbrady0850.github.io/nhl-daily-analytics/";
  const KEY = "nda:v1";
  const HINT = "nda:installHint";
  const TEAMS_ORDER = ["ANA","BOS","BUF","CAR","CBJ","CGY","CHI","COL","DAL","DET","EDM","FLA","LAK","MIN","MTL","NJD",
    "NSH","NYI","NYR","OTT","PHI","PIT","SEA","SJS","STL","TBL","TOR","UTA","VAN","VGK","WPG","WSH"];

  // ---------- state ----------
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; } };
  const saved = load();
  const st = { teams: Array.isArray(saved.teams) ? saved.teams.filter(t => TEAMS_ORDER.includes(t)) : [],
    win: saved.win === "season" ? "season" : "last10", tab: saved.tab || "today" };
  const persist = () => { try { localStorage.setItem(KEY, JSON.stringify({ teams: st.teams, win: st.win, tab: st.tab })); } catch (e) { /* storage blocked: keep working in memory */ } };
  let D = window.NDA_DATA || null;

  // ---------- helpers ----------
  const $ = s => document.querySelector(s);
  const view = $("#view");
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const toast = msg => { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("show"), 3200); };
  const pct = (p, d = 1) => (typeof p === "number" ? (p * 100).toFixed(d) + "%" : "-");
  const num = (v, d = 2) => (typeof v === "number" ? v.toFixed(d) : "-");
  const sv = v => (typeof v === "number" ? v.toFixed(3).replace(/^0/, "") : "-");
  const signed = (v, d = 2) => (typeof v === "number" ? (v > 0 ? "+" : "") + v.toFixed(d) : "-");
  const mmss = s => (typeof s === "number" && s > 0 ? Math.floor(s / 60) + ":" + String(Math.round(s % 60)).padStart(2, "0") : "-");
  const teamName = ab => (D && D.teams && D.teams[ab] && D.teams[ab].name) || ab;
  const localTime = iso => { if (!iso) return ""; try { return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", timeZoneName: "short" }); } catch (e) { return iso; } };
  const localStamp = iso => { if (!iso) return ""; try { return new Date(iso).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }); } catch (e) { return iso; } };
  const longDate = ymd => { if (!ymd) return ""; const [y, m, d] = ymd.split("-").map(Number); return new Date(y, m - 1, d).toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", year: "numeric" }); };
  const tierPill = t => t ? `<span class="pill ${esc(t)}">${esc(t)}</span>` : "";
  const SRC = { DFO_CONFIRMED: ["confirmed", "Confirmed"], DFO_PROJECTED: ["projected", "Projected"], PROBABLE_ROTATION: ["rotation", "Rotation"], UNCONFIRMED: ["rotation", "Unconfirmed"] };
  const srcPill = s => { const v = SRC[s] || ["rotation", s || "-"]; return `<span class="pill ${v[0]}">${esc(v[1])}</span>`; };
  const mine = ab => st.teams.includes(ab);
  const games = () => (D && D.slate && D.slate.games) || [];
  const tbl = (head, rows, cls = "") => `<div class="tablewrap"><table class="${cls}"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`;
  const th = (label, cls = "") => `<th class="${cls}">${esc(label)}</th>`;
  const td = (v, cls = "") => `<td class="${cls}">${v}</td>`;

  // ---------- tabs ----------
  const VIEWS = { today: vToday, myteams: vMyTeams, lines: vLines, goalies: vGoalies, matchups: vMatchups, results: vResults, model: vModel, dfs: vDfs, settings: vSettings, help: vHelp };
  function setTab(t) {
    if (!VIEWS[t]) t = "today";
    st.tab = t;
    document.querySelectorAll("#tabs button").forEach(b => b.setAttribute("aria-selected", String(b.dataset.tab === t)));
    render(); view.focus(); window.scrollTo(0, 0);
  }
  document.addEventListener("click", e => {
    const b = e.target.closest("[data-tab]");
    if (b) { e.preventDefault(); setTab(b.dataset.tab); return; }
    const a = e.target.closest("[data-act]");
    if (a && ACTS[a.dataset.act]) ACTS[a.dataset.act](a.dataset.arg, a);
  });
  function render() {
    view.innerHTML = D ? VIEWS[st.tab]() : noData();
    persist();
  }
  const ACTS = {
    team: ab => { st.teams = mine(ab) ? st.teams.filter(t => t !== ab) : st.teams.concat(ab).sort((a, b) => TEAMS_ORDER.indexOf(a) - TEAMS_ORDER.indexOf(b)); render(); },
    clearTeams: () => { st.teams = []; render(); },
    win: w => { st.win = w === "season" ? "season" : "last10"; render(); },
    reset: () => { if (window.confirm("Clear your saved teams and settings in this browser?")) { try { localStorage.removeItem(KEY); localStorage.removeItem(HINT); } catch (e) { } location.reload(); } },
    install: () => { if (installEvt) { installEvt.prompt(); installEvt = null; } else setTab("settings"); },
    hideInstall: () => { try { localStorage.setItem(HINT, "hide"); } catch (e) { } showInstall(); },
    refresh: () => refresh(true)
  };

  function noData() {
    return `<h1>No data yet</h1><div class="card empty"><p>The daily data file did not load. If you opened this app from a downloaded folder, make sure <code>data/latest.js</code> is next to <code>index.html</code>, or open the live app at <a href="${PUBLIC_URL}">${PUBLIC_URL}</a>.</p></div>`;
  }
  const stamp = () => `<p class="sub">${esc(longDate(D.date))}. Updated ${esc(localStamp(D.generated_utc))}. <button class="linkish noprint" data-act="refresh">Check for newer data</button></p>`;

  // ---------- Today ----------
  function winbar(g) {
    const pa = g.away_win_prob, ph = g.home_win_prob;
    if (typeof ph !== "number") return `<p class="muted small">No probability: a starting goalie or team data was missing.</p>`;
    return `<div class="winbar" role="img" aria-label="${esc(g.away)} ${pct(pa)}, ${esc(g.home)} ${pct(ph)}">
      <i class="away" style="width:${(pa * 100).toFixed(1)}%"></i><i class="home" style="width:${(ph * 100).toFixed(1)}%"></i>
      <span class="l">${esc(g.away)} ${pct(pa)}</span><span class="r">${esc(g.home)} ${pct(ph)}</span></div>`;
  }
  function goalieLine(g) {
    const one = (ab, gk) => gk && gk.name ? `${esc(gk.name)} <span class="muted">(${esc(ab)})</span> ${srcPill(gk.source)}` : `<span class="muted">${esc(ab)}: no starter yet</span>`;
    return `${one(g.away, g.away_side.goalie)} <span class="muted">vs</span> ${one(g.home, g.home_side.goalie)}`;
  }
  function gameCard(g) {
    const fav = g.projected;
    const mk = g.market ? (fav === g.home ? g.market.home_prob : g.market.away_prob) : null;
    const yours = mine(g.home) || mine(g.away);
    return `<article class="card game ${yours ? "edge" : ""}" style="${yours ? "border-left-color:var(--gold)" : ""}">
      <span class="rank">RANK ${esc(g.rank)}</span>
      <p class="match">${esc(g.away)} @ ${esc(g.home)}</p>
      <p class="small muted" style="margin:0">${esc(teamName(g.away))} at ${esc(teamName(g.home))}, ${esc(localTime(g.start_utc))}${g.venue ? ", " + esc(g.venue) : ""}</p>
      ${winbar(g)}
      ${fav ? `<p style="margin:4px 0" class="row"><span class="pick">${esc(teamName(fav))} ${pct(g.win_prob)}</span> ${tierPill(g.tier)} ${g.gate_cleared ? '<span class="pill lead">Gate cleared</span>' : ""} ${yours ? '<span class="pill gold">Your team</span>' : ""}</p>` : ""}
      <dl class="kv"><dt>Market</dt><dd>${typeof mk === "number" ? pct(mk) + ` for ${esc(fav)}` + (g.market.books ? ` <span class="muted">(${esc(g.market.books)} books)</span>` : "") : '<span class="muted">no line yet</span>'}</dd>
      <dt>Goalies</dt><dd>${goalieLine(g)}</dd>
      <dt>Rating gap</dt><dd>${num(g.delta, 1)} <span class="muted">RAA points</span></dd></dl>
      <details class="more"><summary>Why this pick</summary>${pillarTable(g)}${absences(g)}</details>
    </article>`;
  }
  function vToday() {
    const gs = games();
    const sea = (D.results && D.results.season) || {};
    const high = gs.filter(g => g.tier === "HIGH").length;
    const tiles = `<div class="tiles"><div class="tile"><b>${gs.length}</b><span>games today</span></div>
      <div class="tile"><b>${high}</b><span>HIGH-confidence picks</span></div>
      <div class="tile"><b>${(D.slate.gate || {}).cleared || 0}</b><span>cleared the gate</span></div>
      <div class="tile"><b>${sea.games ? pct(sea.accuracy) : "-"}</b><span>season pick rate${sea.games ? ` (${sea.correct} of ${sea.games})` : ""}</span></div></div>`;
    if (!gs.length) {
      const nx = D.slate.next;
      return `<h1>No games today</h1>${stamp()}${tiles}<div class="card"><p>${nx && nx.date ? `Next games: <b>${esc(longDate(nx.date))}</b>, ${esc(nx.game_count)} games.` : "No games are scheduled in the next few weeks."}</p>
        ${nx && nx.matchups ? `<p class="small muted">${nx.matchups.map(esc).join(", ")}</p>` : ""}<p class="small">My Teams, Results and Model still update every day.</p></div>`;
    }
    const intro = st.teams.length ? "" : `<div class="note">Pick your teams in <button class="linkish" data-tab="myteams">My Teams</button> to see their lines every day and spot their games here.</div>`;
    return `<h1>Today's games, ranked</h1>${stamp()}${tiles}${intro}<div class="grid">${gs.map(gameCard).join("")}</div>`;
  }

  // ---------- pillars, form, absences ----------
  const PILLARS = [["p1_form", "P1 Form"], ["p2_absence", "P2 Absence"], ["p3_motivation", "P3 Motivation"], ["p4_gvi", "P4 Goalie"], ["p5_friction", "P5 Friction"], ["adj_raa", "Adj RAA"]];
  function pillarTable(g) {
    const a = g.away_side.pillars || {}, h = g.home_side.pillars || {};
    const rows = PILLARS.map(([k, lab]) => `<tr class="${k === "adj_raa" ? "tot" : ""}">${td(esc(lab))}${td(num(a[k]), "n")}${td(num(h[k]), "n")}${td(typeof a[k] === "number" && typeof h[k] === "number" ? signed(h[k] - a[k]) : "-", "n")}</tr>`).join("");
    return tbl(th("Factor") + th(g.away, "n") + th(g.home, "n") + th("Home edge", "n"), rows);
  }
  function absences(g) {
    const rows = [["away", g.away], ["home", g.home]].flatMap(([s, ab]) => (g[s + "_side"].absences || []).map(a =>
      `<tr>${td(`<b>${esc(ab)}</b>`)}${td(esc(a.name))}${td(esc(a.position))}${td(pct(a.toi_share), "n hm")}${td(esc(a.injury_note || (a.source === "LINEUP_DIFF" ? "Did not dress in the last game" : "")), "small")}</tr>`)).join("");
    return rows ? `<h3>Key absences</h3>${tbl(th("Team") + th("Player") + th("Pos") + th("TOI share", "n hm") + th("Note"), rows)}` : `<p class="small muted">No material absences found.</p>`;
  }
  function formTable(g) {
    const rows = [["away", g.away], ["home", g.home]].map(([s, ab]) => {
      const f = g[s + "_side"].form || {};
      return `<tr>${td(`<b>${esc(ab)}</b>`)}${td(esc(f.venue || "-"))}${td(pct(f.venue_shot_share), "n")}${td(esc(f.venue_games ?? "-"), "n hm")}${td(pct(f.l5_shot_share), "n")}${td(pct(f.l3_shot_share), "n hm")}${td(esc(f.l3_record || "-"), "n hm")}${td(signed(f.l3_vs_l5_momentum, 3), "n")}${td(esc(f.rest_days ?? "-"), "n")}</tr>`;
    }).join("");
    return tbl(th("Team") + th("Tonight") + th("Venue shot%", "n") + th("Venue GP", "n hm") + th("L5 shot%", "n") + th("L3 shot%", "n hm") + th("L3", "n hm") + th("L3 vs L5", "n") + th("Rest", "n"), rows);
  }
  function vMatchups() {
    const gs = games();
    if (!gs.length) return `<h1>Matchups</h1>${stamp()}<div class="card empty">No games today.</div>`;
    return `<h1>Matchups</h1>${stamp()}<p class="small muted">Each team's five pillar scores (rating points above or below neutral), recent form and missing players. The home edge column is home minus away; home ice adds ${num(((D.model || {}).settings_in_use || {}).h, 2)} more.</p>` +
      gs.map(g => `<section class="card"><h2 style="margin-top:0">${esc(g.away)} @ ${esc(g.home)} <span class="small muted">${esc(localTime(g.start_utc))}</span></h2>
        <p class="row">${g.projected ? `<span class="pick">${esc(teamName(g.projected))} ${pct(g.win_prob)}</span> ${tierPill(g.tier)}` : ""}</p>
        <h3>Five pillars</h3>${pillarTable(g)}<h3>Form</h3>${formTable(g)}${absences(g)}</section>`).join("");
  }

  // ---------- lines ----------
  function linesTable(L, opts = {}) {
    const w = st.win;
    if (!L || L.error || !(L.lines || []).length) return `<p class="muted small">Lines are not available for this team right now${L && L.error ? ` (${esc(L.error)})` : ""}.</p>`;
    const groups = (L.lines || []).map(x => ["F" + x.line, x]).concat(opts.forwardsOnly ? [] : (L.pairs || []).map(x => ["D" + x.pair, x]));
    const all = groups.flatMap(([, x]) => x.players);
    const pims = all.map(p => (p[w] || {}).pim).filter(v => typeof v === "number");
    const topPim = pims.length ? Math.max(...pims) : 0;
    const nTop = all.filter(p => (p[w] || {}).pim === topPim).length;
    const head = th("Unit") + th("Pos", "hm") + th("Player") + th("PP", "hm") + th("GP", "n") + th("G", "n") + th("A", "n") + th("P", "n") + th("SOG", "n") + th("PPP", "n hm") + th("TOI/GP", "n hm") + th("PIM", "n");
    const rows = groups.map(([unit, x]) => {
      const lead = (x.leaders || {})[w] || {};
      const tags = [lead.points ? `<span class="pill lead">Most points${lead.points_tied ? " (tied)" : ""}</span>` : "", lead.shots ? `<span class="pill ok">Most shots${lead.shots_tied ? " (tied)" : ""}</span>` : ""].join(" ");
      const body = x.players.map((p, i) => {
        const s = p[w] || {};
        const status = [p.gtd ? '<span class="pill projected">GTD</span>' : "", p.injury ? `<span class="pill bad">${esc(p.injury)}</span>` : ""].join(" ");
        const pimTag = topPim > 0 && s.pim === topPim ? ` <span class="pill bad">Most PIM${nTop > 1 ? " (tied)" : ""}</span>` : "";
        return `<tr class="${lead.points ? "lead" : ""}">${td(i === 0 ? `<span class="unit">${esc(unit)}</span>` : "")}${td(esc(p.pos), "hm")}${td(esc(p.name) + " " + status + pimTag)}${td(esc(p.pp || ""), "hm")}
          ${td(esc(s.gp ?? "-"), "n")}${td(esc(s.g ?? "-"), "n")}${td(esc(s.a ?? "-"), "n")}${td(`<b>${esc(s.p ?? "-")}</b>`, "n")}${td(esc(s.sog ?? "-"), "n")}${td(esc(s.ppp ?? "-"), "n hm")}${td(mmss(s.toi_sec_pg), "n hm")}${td(esc(s.pim ?? "-"), "n")}</tr>`;
      }).join("");
      const t = (x.totals || {})[w] || {};
      return body + `<tr class="tot"><td></td><td class="hm"></td><td>${esc(unit)} total: ${num(t.p_pg, 2)} P/GP, ${num(t.sog_pg, 1)} SOG/GP ${tags}</td><td class="hm"></td>${td("", "n")}${td(esc(t.g ?? ""), "n")}${td(esc(t.a ?? ""), "n")}${td(esc(t.p ?? ""), "n")}${td(esc(t.sog ?? ""), "n")}${td(esc(t.ppp ?? ""), "n hm")}<td class="hm"></td>${td(esc(t.pim ?? ""), "n")}</tr>`;
    }).join("");
    const src = L.source === "daily_faceoff" ? `Combinations from <a href="${esc(L.url)}" target="_blank" rel="noopener">Daily Faceoff</a> (${esc(L.source_name || "projected")}${L.updated_at ? ", updated " + esc(localStamp(L.updated_at)) : ""})`
      : `Combinations rebuilt from the team's last game (${esc(L.source_name || "NHL shift chart")}): players who shared the most ice time`;
    return tbl(head, rows) + `<p class="tiny muted">${src}. Stats from the NHL's game logs, ${w === "season" ? "this season" : "each player's last 10 games this season"}; preseason never counts.</p>`;
  }
  const winToggle = () => `<div class="seg" role="group" aria-label="Stats window"><button data-act="win" data-arg="last10" aria-pressed="${st.win === "last10"}">Last 10</button><button data-act="win" data-arg="season" aria-pressed="${st.win === "season"}">Season</button></div>`;
  function chooser() {
    const divs = {};
    TEAMS_ORDER.forEach(ab => { const t = (D.teams || {})[ab] || {}; (divs[t.division || "Teams"] = divs[t.division || "Teams"] || []).push(ab); });
    return Object.keys(divs).sort().map(dv => `<h3 class="small muted" style="margin:10px 0 6px">${esc(dv)}</h3><div class="chips">${divs[dv].map(ab =>
      `<button class="chip" data-act="team" data-arg="${ab}" aria-pressed="${mine(ab)}" title="${esc(teamName(ab))}">${ab}</button>`).join("")}</div>`).join("") +
      `<p class="small" style="margin-top:10px">${st.teams.length ? `Following: ${st.teams.map(t => esc(teamName(t))).join(", ")}. <button class="linkish" data-act="clearTeams">Clear</button>` : "Tap a team to follow it. Your picks stay in this browser."}</p>`;
  }
  function teamCard(ab) {
    const t = (D.teams || {})[ab] || {};
    const g = games().find(x => x.home === ab || x.away === ab);
    const tonight = g ? `<p class="row small">Tonight: ${esc(g.away)} @ ${esc(g.home)}, ${esc(localTime(g.start_utc))}. ${g.projected ? `Projected winner ${esc(g.projected)} ${pct(g.win_prob)} ${tierPill(g.tier)}` : ""}</p>` : `<p class="small muted">No game today.</p>`;
    return `<section class="card edge"><div class="row between"><h2 style="margin:0">${esc(t.name || ab)}</h2><span class="small muted">${t.record ? esc(t.record) + ", " + esc(t.pts) + " pts" : ""}${t.division ? ", " + esc(t.division) : ""}</span></div>${tonight}${linesTable((D.lines || {})[ab])}</section>`;
  }
  function vMyTeams() {
    const body = st.teams.length ? st.teams.map(teamCard).join("") : `<div class="card empty">No teams picked yet. Choose any of the 32 teams below.</div>`;
    return `<h1>My Teams</h1>${stamp()}<div class="row between noprint" style="margin-bottom:10px">${winToggle()}</div>${body}
      <section class="card noprint"><h2 style="margin-top:0">Choose your teams</h2>${chooser()}</section>`;
  }
  function vLines() {
    const gs = games().filter(g => g.projected);
    if (!gs.length) return `<h1>Projected winners' lines</h1>${stamp()}<div class="card empty">No games today. Every team's lines are in <button class="linkish" data-tab="myteams">My Teams</button>.</div>`;
    return `<h1>Projected winners' lines</h1>${stamp()}<p class="small muted">The forward lines of each game's projected winner, in rank order. The shaded line leads its team in points per game.</p><div class="row noprint" style="margin-bottom:10px">${winToggle()}</div>` +
      gs.map(g => `<section class="card"><h2 style="margin-top:0">#${esc(g.rank)} ${esc(teamName(g.projected))} <span class="small muted">${esc(g.away)} @ ${esc(g.home)}, ${esc(localTime(g.start_utc))}, ${pct(g.win_prob)}</span> ${tierPill(g.tier)}</h2>${linesTable((D.lines || {})[g.projected], { forwardsOnly: true })}</section>`).join("");
  }

  // ---------- goalies ----------
  function vGoalies() {
    const rows = [];
    games().forEach(g => [["away", g.away], ["home", g.home]].forEach(([s, ab]) => rows.push({ ab, g, gk: g[s + "_side"].goalie || {} })));
    const val = v => (typeof v === "number" && !isNaN(v) ? v : null);
    rows.sort((a, b) => {
      const ka = !a.gk.name ? 2 : val(a.gk.season_sv_pct) == null ? 1 : 0, kb = !b.gk.name ? 2 : val(b.gk.season_sv_pct) == null ? 1 : 0;
      if (ka !== kb) return ka - kb;
      return (val(b.gk.season_sv_pct) || 0) - (val(a.gk.season_sv_pct) || 0) || (val(b.gk.gsax) || -99) - (val(a.gk.gsax) || -99) || (val(b.gk.hdsv_pct) || -1) - (val(a.gk.hdsv_pct) || -1) || a.ab.localeCompare(b.ab);
    });
    if (!rows.length) return `<h1>Starting goalies</h1>${stamp()}<div class="card empty">No games today.</div>`;
    const body = rows.map(({ ab, g, gk }) => !gk.name ? `<tr>${td(`<b>${esc(ab)}</b>`)}<td colspan="8" class="muted">No starter found yet</td></tr>` :
      `<tr class="${mine(ab) ? "lead" : ""}">${td(`<b>${esc(ab)}</b><div class="tiny muted">${esc(g.away)} @ ${esc(g.home)}</div>`)}${td(esc(gk.name) + ((gk.window_toi_min || 0) < 180 ? ' <span class="pill thin">Thin</span>' : ""))}
        ${td(sv(gk.season_sv_pct), "n")}${td(num(gk.gsax, 2), "n")}${td(sv(gk.hdsv_pct), "n hm")}${td(Math.round(gk.window_toi_min || 0) + "m", "n hm")}${td(esc(gk.days_rest ?? "-") + "d", "n")}${td(srcPill(gk.source))}
        ${td(gk.reported_by ? (gk.source_url ? `<a href="${esc(gk.source_url)}" target="_blank" rel="noopener">${esc(gk.reported_by)}</a>` : esc(gk.reported_by)) : "-", "hm small")}</tr>`).join("");
    return `<h1>Starting goalies</h1>${stamp()}<p class="small muted">Every probable starter tonight, highest season save percentage first (ties: GSAx, then high-danger save percentage). Projected starters often change by late afternoon; the evening update carries the later confirmations.</p>
      ${tbl(th("Team") + th("Starter") + th("SV%", "n") + th("GSAx", "n") + th("HDSV%", "n hm") + th("Window", "n hm") + th("Rest", "n") + th("Status") + th("Reported by", "hm"), body)}`;
  }

  // ---------- results ----------
  function vResults() {
    const R = D.results || {}, s = R.season || {};
    const last = (R.last_games || []).map(r => {
      const away = (r.matchup || "").split(" @ ")[0];
      const ot = r.last_period && r.last_period !== "REG" ? ` (${esc(r.last_period)})` : "";
      return `<tr>${td(esc(r.matchup))}${td(`<b>${esc(r.projected)}</b>`)}${td(pct(+r.win_prob), "n")}${td(tierPill(r.tier), "hm")}${td(`${esc(away)} ${esc(r.away_score)}, ${esc((r.matchup || "").split(" @ ")[1])} ${esc(r.home_score)}${ot}`, "small")}${td(r.correct === "1" ? '<span class="pill ok">Right</span>' : '<span class="pill bad">Wrong</span>')}</tr>`;
    }).join("");
    const tiers = s.by_tier ? Object.entries(s.by_tier).map(([t, v]) => `<tr>${td(tierPill(t))}${td(v.games, "n")}${td(v.correct, "n")}${td(v.games ? pct(v.correct / v.games) : "-", "n")}</tr>`).join("") : "";
    const m = s.market || {};
    return `<h1>Results</h1>${stamp()}
      <div class="tiles"><div class="tile"><b>${s.games ? pct(s.accuracy) : "-"}</b><span>season pick rate (${esc(s.correct || 0)} of ${esc(s.games || 0)})</span></div>
      <div class="tile"><b>${s.gate_cleared ? pct(s.gate_accuracy) : "-"}</b><span>gate-cleared picks (${esc(s.gate_correct || 0)} of ${esc(s.gate_cleared || 0)})</span></div>
      <div class="tile"><b>${num(s.brier, 3)}</b><span>Brier (0.250 = coin flip)</span></div>
      <div class="tile"><b>${num(s.log_loss, 3)}</b><span>log loss (0.693 = coin flip)</span></div></div>
      <section class="card"><h2 style="margin-top:0">Last games settled${R.last_date ? ": " + esc(longDate(R.last_date)) : ""}</h2>${last ? tbl(th("Matchup") + th("Pick") + th("Win %", "n") + th("Tier", "hm") + th("Final") + th("Result"), last) : '<p class="muted">Nothing settled yet.</p>'}</section>
      <section class="card"><h2 style="margin-top:0">By confidence tier</h2>${tiers ? tbl(th("Tier") + th("Games", "n") + th("Right", "n") + th("Rate", "n"), tiers) : '<p class="muted">No games yet.</p>'}
      ${m.games ? `<p class="small">Against the betting market on ${esc(m.games)} games with a captured line: model log loss ${num(m.model_log_loss, 4)}, market ${num(m.market_log_loss, 4)} (lower is better). The model agreed with the market favorite ${esc(m.agree)} times (right ${esc(m.agree_correct)}) and disagreed ${esc(m.disagree)} times (right ${esc(m.disagree_correct)}).</p>` : ""}
      <p class="tiny muted">Season ${esc(s.first_date || "")} to ${esc(s.last_date || "")}. A pick is right when the projected winner wins, overtime and shootouts included.</p></section>`;
  }

  // ---------- model ----------
  function vModel() {
    const M = D.model || {}, L = M.learning || {}, cur = M.settings_in_use || {}, base = M.baseline || {};
    const names = ["P1 Form", "P2 Absence", "P3 Motivation", "P4 Goalie", "P5 Friction"];
    const row = (lab, a, b, d) => `<tr>${td(esc(lab))}${td(num(a, d), "n")}${td(num(b, d), "n")}</tr>`;
    const set = row("Home ice (rating points)", cur.h, base.h, 3) + row("Slope K", cur.k, base.k, 4) + names.map((n, i) => row(n + " weight", (cur.w || [])[i], (base.w || [])[i], 2)).join("");
    const fw = L.forward || {};
    const status = !fw.games ? "A candidate setting is proposed every 10 settled games and scored on the games that follow." :
      `Candidate under test: scored on ${esc(fw.games)} unseen games (${esc(L.min_forward)} required), ${(fw.mean_gain || 0) > 0 ? "ahead of" : "behind"} the settings in use by ${num(Math.abs(fw.mean_gain || 0), 5)} log loss per game, ${num(Math.abs(fw.t || 0), 1)} standard errors (${num(Math.abs(L.forward_t || 2), 1)} required to go into use).`;
    const cal = (M.calibration || []).map(b => `<tr>${td(`${Math.round(b.lo * 100)}-${Math.round(Math.min(b.hi, 1) * 100)}%`)}${td(b.games, "n")}${td(b.mean_prob != null ? pct(b.mean_prob) : "-", "n")}${td(b.hit_rate != null ? pct(b.hit_rate) : "-", "n")}<td><div class="bar"><i style="width:${b.hit_rate != null ? (b.hit_rate * 100).toFixed(0) : 0}%"></i></div></td></tr>`).join("");
    return `<h1>Model</h1>${stamp()}
      <section class="card"><h2 style="margin-top:0">Learning</h2><p>${esc(L.games || 0)} settled games this season; next learning cycle at ${esc(L.next_at || "-")}. ${esc(L.cycles || 0)} cycles so far, ${esc(L.accepted_cycles || 0)} adopted.</p><p>${status}</p>
      ${L.last ? `<p class="small muted">Last cycle ${esc(L.last.cycle)}: ${esc(L.last.reason || "")}</p>` : ""}
      ${tbl(th("Setting") + th("In use", "n") + th("Baseline", "n"), set)}<p class="tiny muted">Baseline: the 2025-26 calibration on 1,312 games. A change is adopted only after it beats the settings in use on at least 40 games it was not fit on, by two standard errors of log loss.</p></section>
      <section class="card"><h2 style="margin-top:0">Calibration this season</h2><p class="small muted">How often the projected winner won in each probability band. A calibrated model's 60 percent picks win about 60 percent of the time; small bands swing a lot.</p>${cal ? tbl(th("Band") + th("Games", "n") + th("Average", "n") + th("Won", "n") + th(""), cal) : '<p class="muted">No games yet.</p>'}</section>`;
  }

  // ---------- DFS ----------
  function vDfs() {
    const sk = (D.dfs || {}).skaters || [], gk = (D.dfs || {}).goalies || [];
    if (!sk.length) return `<h1>DFS projections</h1>${stamp()}<div class="card empty">No games today.</div>`;
    const sRows = sk.map((p, i) => `<tr class="${mine(p.team) ? "lead" : ""}">${td(i + 1, "n")}${td(esc(p.name))}${td(esc(p.team))}${td(esc(p.opp || ""), "hm")}${td(esc(p.position))}${td(`<b>${num(p.dk_proj, 1)}</b>`, "n")}${td(num(p.proj_goals, 2), "n")}${td(num(p.proj_assists, 2), "n")}${td(num(p.proj_sog, 1), "n hm")}${td(num(p.avg_toi_min, 1), "n hm")}</tr>`).join("");
    const gRows = gk.map((p, i) => `<tr class="${mine(p.team) ? "lead" : ""}">${td(i + 1, "n")}${td(esc(p.name))}${td(esc(p.team))}${td(esc(p.opp || ""), "hm")}${td(pct(p.win_prob), "n")}${td(sv(p.save_pct), "n")}${td(num(p.gsax, 1), "n hm")}${td(`<b>${num(p.dk_proj, 1)}</b>`, "n")}</tr>`).join("");
    return `<h1>DFS projections</h1>${stamp()}<p class="small muted">Projected DraftKings fantasy points for tonight, from each player's season rates, recent ice time and his team's win probability. For research; check confirmed lineups before you set yours.</p>
      <section class="card"><h2 style="margin-top:0">Skaters</h2>${tbl(th("#", "n") + th("Skater") + th("Team") + th("Opp", "hm") + th("Pos") + th("DK", "n") + th("G", "n") + th("A", "n") + th("SOG", "n hm") + th("TOI", "n hm"), sRows)}</section>
      <section class="card"><h2 style="margin-top:0">Goalies</h2>${tbl(th("#", "n") + th("Goalie") + th("Team") + th("Opp", "hm") + th("Win %", "n") + th("SV%", "n") + th("GSAx", "n hm") + th("DK", "n"), gRows)}</section>`;
  }

  // ---------- settings ----------
  let installEvt = null;
  const isStandalone = () => window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  const appleTouch = () => /iPad|iPhone|iPod/.test(navigator.userAgent || "") || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  function installSteps() {
    const hosted = location.protocol.startsWith("http");
    const here = hosted ? location.href.split("#")[0].split("?")[0] : PUBLIC_URL;
    const onPublic = here.startsWith(PUBLIC_URL);
    return `<p>Open <a href="${esc(here)}">${esc(here)}</a> on any phone, tablet or computer. Nothing to sign up for.</p>
      ${!hosted || onPublic ? `<img class="qr" src="qr-app.png" width="160" height="160" alt="QR code for ${esc(PUBLIC_URL)}">` : ""}
      <ol class="steps-list"><li><b>iPhone or iPad:</b> open the link in Safari, tap Share, then Add to Home Screen.</li>
      <li><b>Android:</b> open it in Chrome, tap the menu, then Install app or Add to Home screen.</li>
      <li><b>Computer:</b> in Chrome or Edge, use the install icon in the address bar.</li></ol>
      ${installEvt ? '<p><button class="btn" data-act="install">Install now</button></p>' : ""}`;
  }
  function showInstall() {
    const box = $("#install");
    let hide = false; try { hide = localStorage.getItem(HINT) === "hide"; } catch (e) { }
    const touch = appleTouch() || /Android/.test(navigator.userAgent || "");
    if (hide || isStandalone() || !(installEvt || touch) || !location.protocol.startsWith("http")) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = `<span>Add NHL Daily to your home screen for one-tap access.</span><span class="row"><button class="btn small" data-act="install">${installEvt ? "Install" : "How"}</button><button class="btn small ghost" data-act="hideInstall">Not now</button></span>`;
  }
  function vSettings() {
    const S = D.sources || {}, gl = S.starting_goalies || {}, inj = S.injuries || {}, ln = S.lines || {};
    const lines = `${esc(ln.daily_faceoff || 0)} teams from Daily Faceoff, ${esc(ln.shift_chart || 0)} rebuilt from the last game's shift chart${(ln.missing || []).length ? ", missing: " + ln.missing.map(esc).join(", ") : ""}`;
    return `<h1>Settings</h1>
      <section class="card"><h2 style="margin-top:0">My Teams</h2>${chooser()}<p class="row" style="margin-top:8px"><span class="small muted">Stats window:</span> ${winToggle()}</p></section>
      <section class="card"><h2 style="margin-top:0">Get the app</h2>${installSteps()}</section>
      <section class="card"><h2 style="margin-top:0">Today's data</h2><dl class="kv"><dt>Slate</dt><dd>${esc(longDate(D.date))}, ${esc(D.slate.game_count)} games</dd>
        <dt>Built</dt><dd>${esc(localStamp(D.generated_utc))}</dd>
        <dt>Starting goalies</dt><dd>${gl.loaded ? `${esc(gl.teams)} teams listed by Daily Faceoff, ${esc(gl.confirmed)} confirmed` : "Daily Faceoff not reached; starters are the model's rotation guess"}</dd>
        <dt>Injuries</dt><dd>${inj.loaded ? `${esc(inj.rows)} rows from Hockey-Reference` : "Injury report not reached; absences come from last game's lineup only"}</dd>
        <dt>Betting lines</dt><dd>${esc(S.market_games || 0)} games</dd><dt>Lines</dt><dd>${lines}</dd>
        <dt>Model</dt><dd>RAA model ${esc(D.model_version)}, app ${APP_VERSION}</dd></dl>
        ${(D.problems || []).length ? `<p class="small" style="color:var(--warn)">Notes from this build: ${D.problems.map(esc).join("; ")}</p>` : ""}</section>
      <section class="card"><h2 style="margin-top:0">Privacy</h2><p class="small">This app has no accounts and sends nothing about you anywhere. Your teams and settings are stored only in this browser. <button class="btn small warn" data-act="reset">Clear my settings</button></p></section>`;
  }

  // ---------- help ----------
  function vHelp() {
    const gloss = (window.NDA_GLOSSARY || []).map(([t, d]) => `<dt>${esc(t)}</dt><dd>${esc(d)}</dd>`).join("");
    return `<h1>How it works</h1>
      <section class="card"><p>Twice a day (about 9:00 AM and 5:15 PM US Central) a free GitHub workflow reads the NHL's public schedule, standings and game logs, MoneyPuck's season numbers, Daily Faceoff's starting goalies and line combinations, and Hockey-Reference's injury report. It scores every team on five pillars (form, missing players, playoff-race motivation, the starting goalie and rest), turns the gap between the two teams into a win probability, and publishes one data file that this app reads.</p>
      <p>Each morning it also settles the previous night's games, keeps the season record, and every 10 settled games tests whether a refit of the model would predict better. A refit is adopted only after it proves itself on games it has not seen.</p></section>
      <section class="card"><h2 style="margin-top:0">Limits</h2><ul class="steps-list"><li>Hockey is close to a coin flip: last season the model picked 56 percent of winners on games it had not seen, and about 64 percent on picks that cleared the gate. Treat the numbers as estimates.</li>
      <li>Morning starting goalies are mostly projections; check the evening update or the team's own news before relying on one.</li>
      <li>Line combinations change at the morning skate. When Daily Faceoff cannot be reached, the app rebuilds lines from the last game's ice time.</li>
      <li>Not affiliated with the NHL, MoneyPuck, Daily Faceoff or Hockey-Reference. Nothing here is betting advice.</li></ul></section>
      <section class="card"><h2 style="margin-top:0">Glossary</h2><dl class="gloss">${gloss}</dl></section>`;
  }

  // ---------- data refresh ----------
  async function refresh(manual) {
    if (!location.protocol.startsWith("http")) { if (manual) toast("Open the live app to check for newer data"); return; }
    try {
      const r = await fetch("data/latest.json?ts=" + Date.now(), { cache: "no-cache" });
      if (!r.ok) throw new Error(r.status);
      const n = await r.json();
      if (n && n.format === "nhl-daily-analytics/1" && (!D || n.generated_utc > D.generated_utc)) { D = n; render(); toast("Updated to the latest data"); }
      else if (manual) toast("You have the latest data");
    } catch (e) { if (manual) toast("Could not reach the data file; showing the saved copy"); }
  }

  // ---------- start ----------
  $("#ver").textContent = APP_VERSION;
  window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); installEvt = e; showInstall(); if (st.tab === "settings") render(); });
  if ("serviceWorker" in navigator && location.protocol.startsWith("http")) navigator.serviceWorker.register("sw.js").catch(() => {});
  setTab(st.tab);
  showInstall();
  refresh(false);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refresh(false); });
})();
