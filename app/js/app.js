/* NHL Daily Analytics. Plain JavaScript, no build step: runs from GitHub Pages,
   from a downloaded folder (file://) or any static host. The day's numbers come
   from data/latest.js, written by the daily build; your settings (My Teams, the
   stats window) stay in this browser (localStorage). */
(function () {
  "use strict";
  const APP_VERSION = "1.12.1";
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
    win: saved.win === "season" ? "season" : "last10", tab: saved.tab || "today",
    stand: ["wildcard", "division", "conference", "league"].includes(saved.stand) ? saved.stand : "wildcard",
    inj: ["all", "today", "mine"].includes(saved.inj) ? saved.inj : "all", logos: saved.logos !== false };
  const persist = () => { try { localStorage.setItem(KEY, JSON.stringify({ teams: st.teams, win: st.win, tab: st.tab, stand: st.stand, inj: st.inj, logos: st.logos })); } catch (e) { /* storage blocked: keep working in memory */ } };
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

  // ---------- graphics: team colors, badges, rink art ----------
  // Team colors are each club's primary and secondary colors, shown as plain
  // swatches behind the team abbreviation. Each badge also asks the NHL's own image
  // server for the club's official logo (John, 2026-10-26); the logo replaces the
  // badge once it has loaded, and the badge stays when it cannot load (offline, a
  // blocked or missing image, or logos turned off in Settings). No logo files are
  // copied into this app.
  // Checked on 2026-10-23 against teamcolorcodes.com, usteamcolors.com and ESPN's
  // public team feed: Anaheim's orange is the 2024 rebrand's (#FC4C02), Utah's
  // Mountain Blue is #6CACE3, and black is the clubs' #000000 (Los Angeles uses #111111).
  const TEAM_COLORS = {
    ANA: ["#FC4C02", "#000000"], BOS: ["#FFB81C", "#000000"], BUF: ["#003087", "#FFB81C"], CAR: ["#CE1126", "#000000"],
    CBJ: ["#002654", "#CE1126"], CGY: ["#C8102E", "#F1BE48"], CHI: ["#CF0A2C", "#000000"], COL: ["#6F263D", "#236192"],
    DAL: ["#006847", "#8F8F8C"], DET: ["#CE1126", "#FFFFFF"], EDM: ["#041E42", "#FF4C00"], FLA: ["#C8102E", "#041E42"],
    LAK: ["#111111", "#A2AAAD"], MIN: ["#154734", "#A6192E"], MTL: ["#AF1E2D", "#192168"], NJD: ["#CE1126", "#000000"],
    NSH: ["#FFB81C", "#041E42"], NYI: ["#00539B", "#F47D30"], NYR: ["#0038A8", "#CE1126"], OTT: ["#DA1A32", "#B79257"],
    PHI: ["#F74902", "#000000"], PIT: ["#000000", "#FCB514"], SEA: ["#001628", "#99D9D9"], SJS: ["#006D75", "#EA7200"],
    STL: ["#002F87", "#FCB514"], TBL: ["#002868", "#FFFFFF"], TOR: ["#00205B", "#FFFFFF"], UTA: ["#000000", "#6CACE3"],
    VAN: ["#00205B", "#00843D"], VGK: ["#B4975A", "#333F42"], WPG: ["#041E42", "#004C97"], WSH: ["#C8102E", "#041E42"]
  };
  const hexRgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const lum = h => { const c = hexRgb(h).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
  // whichever of white or near-black text reads better on the club color
  const inkOn = h => { const L = lum(h); return 1.05 / (L + 0.05) >= (L + 0.05) / (lum("#111418") + 0.05) ? "#ffffff" : "#111418"; };
  const cols = ab => TEAM_COLORS[ab] || ["#5D6D7E", "#2B3644"];
  const near = (a, b) => { const x = hexRgb(a), y = hexRgb(b); return Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]) < 90; };
  const teamVars = ab => { const [c1, c2] = cols(ab); return `--c1:${c1};--c2:${c2};--tx:${inkOn(c1)}`; };
  // two teams on one sheet of ice: if the colors clash, the home side wears its second color
  function sideColors(away, home) {
    const a = cols(away)[0];
    let h = cols(home)[0];
    if (near(a, h)) h = cols(home)[1];
    if (near(a, h)) h = "#DCE6EF";
    return [a, h];
  }
  const LOGOS = { ok: new Set(), bad: new Set() };
  const logoImg = (ab, lightOnly) => {
    const t = (D && D.teams || {})[ab] || {};
    if (!st.logos || !t.logo || LOGOS.bad.has(ab)) return "";
    const dark = !lightOnly && t.logo_dark ? `<source srcset="${esc(t.logo_dark)}" media="(prefers-color-scheme: dark)">` : "";
    return `<picture class="tm-pic">${dark}<img class="tm-logo" src="${esc(t.logo)}" alt="" referrerpolicy="no-referrer" decoding="async" data-ab="${esc(ab)}"></picture>`;
  };
  // lightOnly: the badge sits on a light field in both themes (the gold pick banner)
  const badge = (ab, cls = "", focus = false, lightOnly = false) => {
    const img = logoImg(ab, lightOnly);
    return `<span class="tm ${cls}${img && LOGOS.ok.has(ab) ? " logo-on" : ""}" style="${teamVars(ab)}" data-tip="${esc(teamName(ab))}."${focus ? ' tabindex="0"' : ""}>${img}<span class="tm-ab">${esc(ab)}</span></span>`;
  };
  // a logo that loads replaces its badge; one that fails is dropped for the session
  document.addEventListener("load", e => {
    const i = e.target;
    if (!i.classList || !i.classList.contains("tm-logo")) return;
    LOGOS.ok.add(i.dataset.ab);
    const t = i.closest(".tm"); if (t) t.classList.add("logo-on");
  }, true);
  document.addEventListener("error", e => {
    const i = e.target;
    if (!i.classList || !i.classList.contains("tm-logo")) return;
    LOGOS.bad.add(i.dataset.ab);
    const t = i.closest(".tm"), p = i.closest("picture");
    if (t) t.classList.remove("logo-on");
    if (p) p.remove();
  }, true);
  const icon = (name, cls = "ico") => `<svg class="${cls}" aria-hidden="true" focusable="false"><use href="#i-${name}"></use></svg>`;
  const head = (ico, title) => `<header class="pagehead"><span class="ph-ico">${icon(ico)}</span><h1>${title}</h1></header>`;
  // a regulation rink seen from above, 200 by 85 feet at 10 units to the foot
  const RINK = `<svg class="rink" viewBox="0 0 2000 850" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false">
    <rect class="r-ice" x="6" y="6" width="1988" height="838" rx="280"/>
    <g class="r-red"><line x1="110" y1="66" x2="110" y2="784"/><line x1="1890" y1="66" x2="1890" y2="784"/>
      <circle cx="310" cy="205" r="150"/><circle cx="310" cy="645" r="150"/><circle cx="1690" cy="205" r="150"/><circle cx="1690" cy="645" r="150"/>
      <path d="M110 315 L6 285 M110 535 L6 565 M1890 315 L1994 285 M1890 535 L1994 565"/></g>
    <g class="r-dot"><circle cx="310" cy="205" r="11"/><circle cx="310" cy="645" r="11"/><circle cx="1690" cy="205" r="11"/><circle cx="1690" cy="645" r="11"/>
      <circle cx="800" cy="205" r="11"/><circle cx="800" cy="645" r="11"/><circle cx="1200" cy="205" r="11"/><circle cx="1200" cy="645" r="11"/></g>
    <path class="r-crease" d="M110 365 A60 60 0 0 1 110 485 Z M1890 365 A60 60 0 0 0 1890 485 Z"/>
    <path class="r-net" d="M110 395 H80 V455 H110 M1890 395 H1920 V455 H1890"/>
    <g class="r-blue"><line x1="750" y1="6" x2="750" y2="844"/><line x1="1250" y1="6" x2="1250" y2="844"/></g>
    <circle class="r-center" cx="1000" cy="425" r="150"/><circle class="r-cdot" cx="1000" cy="425" r="7"/>
    <line class="r-mid" x1="1000" y1="6" x2="1000" y2="844"/>
    <path class="r-ref" d="M900 844 A100 100 0 0 1 1100 844"/>
    <rect class="r-board" x="6" y="6" width="1988" height="838" rx="280"/>
  </svg>`;
  // an empty net, seen from the slot
  const EMPTY_NET = `<svg class="empty-art" viewBox="0 0 240 150" aria-hidden="true" focusable="false">
    <ellipse class="en-ice" cx="120" cy="134" rx="112" ry="12"/>
    <path class="en-crease" d="M34 134 A86 22 0 0 0 206 134 Z"/>
    <g class="en-mesh"><path d="M48 44 V128 M64 40 V130 M80 38 V131 M96 37 V132 M112 36 V132 M128 36 V132 M144 37 V132 M160 38 V131 M176 40 V130 M192 44 V128"/>
      <path d="M40 56 H200 M40 72 H200 M40 88 H200 M40 104 H200 M40 120 H200"/></g>
    <path class="en-frame" d="M38 132 V42 Q38 30 50 30 H190 Q202 30 202 42 V132"/>
    <path class="en-base" d="M38 132 H202"/>
    <g class="en-puck"><ellipse cx="190" cy="140" rx="13" ry="4.5"/><rect x="177" y="134.5" width="26" height="5.5"/><ellipse class="en-top" cx="190" cy="134.5" rx="13" ry="4.5"/></g>
  </svg>`;
  // player names open the player's official NHL.com page (bio and full stats)
  const NHL_PLAYER = "https://www.nhl.com/player/";
  const pname = (name, id) => {
    const pid = /^\d{6,8}$/.test(String(id == null ? "" : id)) ? String(id) : "";
    return pid ? `<a class="plink" href="${NHL_PLAYER}${pid}" target="_blank" rel="noopener noreferrer">${esc(name)}<span class="sr"> (official NHL.com page and stats, opens in a new window)</span></a>` : esc(name);
  };
  const emptyCard = (msg) => `<div class="card empty">${EMPTY_NET}<p>${msg}</p></div>`;

  // ---------- definitions: hover with a mouse, tap on a touch screen, or Tab to it ----------
  // Every colored tag, summary tile, column heading and abbreviation gets a data-tip
  // after each render, from the tables in glossary.js plus team names and the
  // reader's time zone. One floating box shows the definition.
  const TIPS = Object.assign({ pills: {}, heads: {}, tiles: {}, dt: {}, pos: {}, inline: {} }, window.NDA_TIPS || {});
  const tipBox = document.createElement("div");
  tipBox.id = "tipbox"; tipBox.setAttribute("role", "tooltip"); tipBox.hidden = true;
  document.body.appendChild(tipBox);
  let tipEl = null, lastPointer = "mouse", pointerFocus = false, inlineMap = {}, inlineRe = null;
  const reEsc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const SKIP_TEXT = "a,button,script,style,code,abbr,.pill,[data-tip],.gloss,.no-tip,svg,input,select,textarea,#tipbox";
  function tzNames() {
    const out = [];
    const dates = [new Date()].concat(D ? [D.generated_utc].concat(games().map(g => g.start_utc)).filter(Boolean).map(x => new Date(x)) : []);
    dates.forEach(d => {
      try {
        const part = o => new Intl.DateTimeFormat([], { timeZoneName: o }).formatToParts(d).find(p => p.type === "timeZoneName");
        const s = part("short"), l = part("long");
        if (s && l && !out.some(x => x[0] === s.value)) out.push([s.value, l.value]);
      } catch (e) { /* older browser: no time zone names */ }
    });
    return out;
  }
  function buildInline() {
    const map = Object.assign({}, TIPS.inline);
    Object.keys((D && D.teams) || {}).forEach(ab => { if (D.teams[ab] && D.teams[ab].name) map[ab] = D.teams[ab].name + "."; });
    tzNames().forEach(([s, l]) => { if (!map[s]) map[s] = l + ", the time zone of this device."; });
    inlineMap = map;
    const keys = Object.keys(map).sort((a, b) => b.length - a.length).map(reEsc);
    // a capture group instead of a lookbehind, so older iPads can run it
    inlineRe = keys.length ? new RegExp("(^|[^A-Za-z0-9%/])(" + keys.join("|") + ")(?![A-Za-z0-9%/])", "g") : null;
  }
  function setTip(el, text, focusable) {
    if (!text) return;
    el.dataset.tip = text;
    el.classList.add("has-tip");
    if (focusable && !el.matches("a,button,[tabindex]")) el.tabIndex = 0;
  }
  function wrapText(root) {
    if (!inlineRe) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: n => (!n.nodeValue.trim() || !n.parentElement || n.parentElement.closest(SKIP_TEXT)) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(n => {
      const s = n.nodeValue;
      inlineRe.lastIndex = 0;
      if (!inlineRe.test(s)) return;
      const inCell = !!n.parentElement.closest("td,.winbar");
      const frag = document.createDocumentFragment();
      let last = 0, m;
      inlineRe.lastIndex = 0;
      while ((m = inlineRe.exec(s))) {
        const start = m.index + m[1].length, tok = m[2];
        if (start > last) frag.appendChild(document.createTextNode(s.slice(last, start)));
        const a = document.createElement("abbr");
        a.className = "tip";
        a.textContent = tok;
        a.dataset.tip = inlineMap[tok];
        if (!inCell) a.tabIndex = 0;            // table cells repeat; headings and tags carry the keyboard stops
        frag.appendChild(a);
        last = start + tok.length;
        inlineRe.lastIndex = last;
      }
      if (last < s.length) frag.appendChild(document.createTextNode(s.slice(last)));
      // inside a flex box, loose text pieces would each become a flex item and lose
      // their spaces, so keep the run together in one inline span
      const disp = getComputedStyle(n.parentElement).display;
      if (disp.indexOf("flex") >= 0 || disp.indexOf("grid") >= 0) {
        const span = document.createElement("span");
        span.appendChild(frag);
        n.parentNode.replaceChild(span, n);
      } else {
        n.parentNode.replaceChild(frag, n);
      }
    });
  }
  function tileTip(label) {
    const k = label.toLowerCase().replace(/\(.*\)/, "").trim();
    let t = TIPS.tiles[k];
    if (t && k === "cleared the gate" && D) {
      const gate = (D.slate || {}).gate || {}, s = (D.results || {}).season || {};
      if (typeof gate.dynamic_gate === "number") t += ` Today's gate: ${gate.dynamic_gate.toFixed(1)} rating points.`;
      if (s.gate_cleared) t += ` This season ${s.gate_correct} of ${s.gate_cleared} gate-cleared picks were right (${pct(s.gate_accuracy)}).`;
    }
    return t;
  }
  function decorate(root) {
    if (!root) return;
    root.querySelectorAll("table").forEach(t => {
      const heads = [...t.querySelectorAll("thead th")];
      heads.forEach(h => { const k = h.textContent.trim(); setTip(h, h.dataset.tip || TIPS.heads[k] || (/^[A-Z]{3}$/.test(k) ? inlineMap[k] : null), true); });
      const posCol = heads.findIndex(h => h.textContent.trim() === "Pos");
      if (posCol >= 0) t.querySelectorAll("tbody tr").forEach(r => {
        const c = r.children[posCol];
        const k = c && !c.hasAttribute("colspan") ? c.textContent.trim() : "";
        if (TIPS.pos[k]) c.innerHTML = `<abbr class="tip" data-tip="${esc(TIPS.pos[k])}">${esc(k)}</abbr>`;
      });
    });
    root.querySelectorAll(".pill").forEach(p => {
      if (p.dataset.tip) return;
      const up = p.textContent.trim().toUpperCase(), tied = /\s*\(TIED\)$/.test(up), base = up.replace(/\s*\(TIED\)$/, "");
      let d = TIPS.pills[base] || (p.classList.contains("bad") ? TIPS.pills._injury : TIPS.pills._other);
      if (tied && TIPS.pills._tied) d += " " + TIPS.pills._tied;
      setTip(p, d, true);
    });
    root.querySelectorAll(".tile").forEach(tl => { const s = tl.querySelector("span"); if (s) setTip(tl, tileTip(s.textContent), true); });
    root.querySelectorAll(".rank").forEach(r => setTip(r, "Rank on today's slate by the projected winner's win probability, highest first.", true));
    root.querySelectorAll("dl.kv dt").forEach(dt => setTip(dt, TIPS.dt[dt.textContent.trim()], true));
    root.querySelectorAll("button.chip[data-arg]").forEach(b => { const n = teamName(b.dataset.arg); b.removeAttribute("title"); b.dataset.tip = n + "."; b.setAttribute("aria-label", b.dataset.arg + ", " + n); });
    wrapText(root);
  }
  function showTip(el) {
    const d = el && el.dataset.tip;
    if (!d) return;
    if (tipEl && tipEl !== el) tipEl.removeAttribute("aria-describedby");
    tipEl = el;
    const term = el.classList.contains("tile") ? (el.querySelector("span") || el).textContent.replace(/\(.*\)/, "").trim()
      : el.classList.contains("spos") ? el.querySelector(".k").textContent.trim() : (el.textContent || "").trim();
    tipBox.innerHTML = (term && term.length <= 40 ? `<b>${esc(term)}</b>` : "") + `<span>${esc(d)}</span>`;
    tipBox.hidden = false;
    el.setAttribute("aria-describedby", "tipbox");
    const r = el.getBoundingClientRect(), b = tipBox.getBoundingClientRect();
    const vw = document.documentElement.clientWidth, vh = window.innerHeight;
    const left = Math.max(8, Math.min(r.left + r.width / 2 - b.width / 2, vw - b.width - 8));
    let top = r.top - b.height - 8;
    if (top < 8) top = r.bottom + 8;
    if (top + b.height > vh - 8) top = Math.max(8, vh - b.height - 8);
    tipBox.style.left = Math.round(left) + "px";
    tipBox.style.top = Math.round(top) + "px";
  }
  function hideTip() {
    if (tipEl) tipEl.removeAttribute("aria-describedby");
    tipEl = null;
    tipBox.hidden = true;
  }
  const isMouse = t => t === "mouse" || t === "pen";
  document.addEventListener("pointerdown", e => {
    lastPointer = e.pointerType || "mouse";
    pointerFocus = true; setTimeout(() => { pointerFocus = false; }, 700);
    if (tipEl && !(e.target.closest && e.target.closest("[data-tip]") === tipEl)) hideTip();
  }, true);
  document.addEventListener("pointerover", e => {
    if (!isMouse(e.pointerType)) return;
    const el = e.target.closest && e.target.closest("[data-tip]");
    if (el && el !== tipEl) showTip(el);
  });
  document.addEventListener("pointerout", e => {
    if (!isMouse(e.pointerType) || !tipEl) return;
    const el = e.target.closest && e.target.closest("[data-tip]");
    if (el === tipEl && !(e.relatedTarget && el.contains(e.relatedTarget))) hideTip();
  });
  document.addEventListener("focusin", e => {
    if (pointerFocus) return;                          // a tap or click; the handlers above decide
    const el = e.target.closest && e.target.closest("[data-tip]");
    if (el) showTip(el); else hideTip();
  });
  document.addEventListener("focusout", e => { if (tipEl && e.target === tipEl) hideTip(); });
  document.addEventListener("click", e => {
    if (isMouse(lastPointer)) return;                  // mouse readers already see it on hover
    const el = e.target.closest && e.target.closest("[data-tip]");
    if (!el || el.closest("a,button,summary")) return; // links, buttons and toggles keep their own tap
    if (tipEl === el) hideTip(); else showTip(el);
  });
  document.addEventListener("keydown", e => { pointerFocus = false; if (e.key === "Escape") hideTip(); });
  // with a mouse, a player's page opens in its own window, reused for the next
  // name; on a phone or tablet, or if pop-ups are blocked, it opens in a new tab
  document.addEventListener("click", e => {
    const a = e.target.closest && e.target.closest("a.plink");
    if (!a || e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || !isMouse(lastPointer)) return;
    const w = Math.min(1200, (screen.availWidth || 1200) - 40), h = Math.min(950, (screen.availHeight || 950) - 40);
    const left = Math.max(0, (screen.availWidth || w) - w - 20), top = 20;
    let win = null;
    try { win = window.open(a.href, "nhl-player", `popup=yes,width=${w},height=${h},left=${left},top=${top}`); } catch (x) { win = null; }
    if (win) {
      e.preventDefault();
      try { win.opener = null; win.focus(); } catch (x) { /* another site's window */ }
    }
  });
  // A scroll or resize moves the open definition with its term, and closes it
  // only once the term leaves the screen. (Focusing a term below the fold scrolls
  // the page; hiding on every scroll closed the definition a keyboard user had
  // just opened.)
  function followTip() {
    if (!tipEl) return;
    if (!document.body.contains(tipEl)) { hideTip(); return; }
    const r = tipEl.getBoundingClientRect();
    if (r.bottom < 0 || r.top > window.innerHeight || r.width === 0) { hideTip(); return; }
    showTip(tipEl);
  }
  window.addEventListener("scroll", followTip, true);
  window.addEventListener("resize", followTip);

  // ---------- tabs ----------
  const VIEWS = { today: vToday, myteams: vMyTeams, lines: vLines, goalies: vGoalies, injuries: vInjuries, matchups: vMatchups, standings: vStandings, results: vResults, model: vModel, dfs: vDfs, settings: vSettings, help: vHelp };
  function setTab(t) {
    if (!VIEWS[t]) t = "today";
    st.tab = t;
    document.querySelectorAll("#tabs button").forEach(b => b.setAttribute("aria-selected", String(b.dataset.tab === t)));
    render(); view.focus(); window.scrollTo(0, 0);
    // on a phone the tab strip scrolls sideways: bring the chosen tab into view
    const nav = $("#tabs"), btn = nav && nav.querySelector(`button[data-tab="${t}"]`);
    if (btn && nav.scrollWidth > nav.clientWidth) {
      const l = btn.offsetLeft - nav.offsetLeft;
      if (l < nav.scrollLeft || l + btn.offsetWidth > nav.scrollLeft + nav.clientWidth) nav.scrollLeft = Math.max(0, l - 24);
    }
  }
  document.addEventListener("click", e => {
    const b = e.target.closest("[data-tab]");
    if (b) { e.preventDefault(); setTab(b.dataset.tab); return; }
    const a = e.target.closest("[data-act]");
    if (a && ACTS[a.dataset.act]) ACTS[a.dataset.act](a.dataset.arg, a);
  });
  function render() {
    hideTip();
    view.innerHTML = D ? VIEWS[st.tab]() : noData();
    buildInline();
    decorate(view);
    persist();
  }
  const ACTS = {
    team: ab => { st.teams = mine(ab) ? st.teams.filter(t => t !== ab) : st.teams.concat(ab).sort((a, b) => TEAMS_ORDER.indexOf(a) - TEAMS_ORDER.indexOf(b)); render(); },
    clearTeams: () => { st.teams = []; render(); },
    win: w => { st.win = w === "season" ? "season" : "last10"; render(); },
    logos: v => { st.logos = v !== "off"; if (st.logos) LOGOS.bad.clear(); render(); },
    stand: v => { st.stand = ["wildcard", "division", "conference", "league"].includes(v) ? v : "wildcard"; render(); },
    inj: v => { st.inj = ["all", "today", "mine"].includes(v) ? v : "all"; render(); },
    reset: () => { if (window.confirm("Clear your saved teams and settings in this browser?")) { try { localStorage.removeItem(KEY); localStorage.removeItem(HINT); } catch (e) { } location.reload(); } },
    install: () => getApp(),
    getApp: () => getApp(),
    closeApp: () => closeSheet(),
    hideInstall: () => { try { localStorage.setItem(HINT, String(Date.now() + SNOOZE_MS)); } catch (e) { } showInstall(); },
    refresh: () => refresh(true)
  };

  function noData() {
    return `${head("today", "No data yet")}<div class="card empty">${EMPTY_NET}<p>The daily data file did not load. If you opened this app from a downloaded folder, make sure <code>data/latest.js</code> is next to <code>index.html</code>, or open the live app at <a href="${PUBLIC_URL}">${PUBLIC_URL}</a>.</p></div>`;
  }
  // ---------- update schedule ----------
  // The Daily data workflow builds at these US Central wall-clock times all year
  // (nhlda/schedule.py MAIN_CENTRAL); their UTC times move with daylight time. A run
  // can still arrive late, so an update counts as "due" for 3 hours before it is "late".
  const RUNS_CENTRAL = [[9, 0], [16, 45]];
  const CENTRAL = "America/Chicago";
  const LATE_MIN = 180;
  const CHECK_MIN = { due: 5, late: 15 };     // how often an open page looks for the new file
  const isLive = () => location.protocol.startsWith("http");
  const centralFmt = (() => { try { return new Intl.DateTimeFormat("en-US", { timeZone: CENTRAL, hourCycle: "h23", year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric" }); } catch (e) { return null; } })();
  function centralParts(ms) {                 // [year, month 0-11, day, hour, minute] on the US Central clock
    if (centralFmt) {
      try { const p = centralFmt.formatToParts(new Date(ms)), g = k => +(p.find(x => x.type === k) || {}).value; return [g("year"), g("month") - 1, g("day"), g("hour") % 24, g("minute")]; } catch (e) { /* fall through */ }
    }
    const t = new Date(ms - 6 * 3600000);       // no time zone support: standard time
    return [t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate(), t.getUTCHours(), t.getUTCMinutes()];
  }
  function centralUtc(y, mo, d, h, mi) {      // the instant of a US Central wall-clock time
    for (const off of [5, 6]) {
      const t = Date.UTC(y, mo, d, h + off, mi), p = centralParts(t);
      if (p[0] === y && p[1] === mo && p[2] === d && p[3] === h && p[4] === mi) return t;
    }
    return Date.UTC(y, mo, d, h + 6, mi);
  }
  function runTimes(now) {
    const [y, mo, d] = centralParts(now), out = [];
    for (let k = -1; k <= 1; k++) {
      const day = new Date(Date.UTC(y, mo, d + k));
      RUNS_CENTRAL.forEach(([h, m]) => out.push(centralUtc(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), h, m)));
    }
    return out.sort((a, b) => a - b);
  }
  function updState(now = Date.now()) {
    const t = runTimes(now), last = t.filter(x => x <= now).pop(), next = t.find(x => x > now);
    const built = Date.parse((D && D.generated_utc) || "");
    if (!isLive()) return { kind: "copy", now, next };
    if (!isFinite(built) || built >= last) return { kind: "fresh", now, next };
    return { kind: now - last >= LATE_MIN * 60000 ? "late" : "due", now, last, next };
  }
  const dur = ms => { const m = Math.round(ms / 60000); if (m < 1) return "less than a minute"; const h = Math.floor(m / 60), r = m % 60; return [h ? h + " h" : "", r ? r + " min" : ""].filter(Boolean).join(" "); };
  const runAt = ms => localTime(new Date(ms).toISOString());
  const dailyRuns = () => runTimes(Date.now()).slice(2, 4).map(t => new Date(t)).sort((a, b) => (a.getHours() * 60 + a.getMinutes()) - (b.getHours() * 60 + b.getMinutes())).map(d => runAt(d.getTime()));
  function updText(s) {
    if (s.kind === "copy") return "This saved copy does not update; open the live app for newer data.";
    if (s.kind === "fresh") return `Next scheduled update about ${runAt(s.next)}, in ${dur(s.next - s.now)}.`;
    if (s.kind === "due") return `New data is due: the ${runAt(s.last)} update has not posted yet. GitHub often runs late; this page checks every ${CHECK_MIN.due} minutes.`;
    return `The ${runAt(s.last)} update is ${dur(s.now - s.last)} late; GitHub may have skipped it. This page keeps checking; the next scheduled update is about ${runAt(s.next)}.`;
  }
  const updSpan = () => { const s = updState(); return `<span class="upd ${s.kind}" data-upd>${esc(updText(s))}</span>`; };
  const updated = () => `Updated ${esc(localStamp(D.generated_utc))}. <span class="upd-line">${updSpan()} <button class="linkish noprint" data-act="refresh">Check for newer data</button></span>`;
  const stamp = () => `<p class="sub">${esc(longDate(D.date))}. ${updated()}</p>`;

  // ---------- Today ----------
  // the signature: each game is a sheet of ice split at the away team's win
  // probability; the puck sits on the split, pushed into the underdog's end
  // the win marker is yellow; on a gold club color it turns white so it still stands out
  const goldish = h => { const [r, g, b] = hexRgb(h); return r > 190 && g > 140 && b < 110; };
  function winbar(g) {
    const pa = g.away_win_prob, ph = g.home_win_prob;
    if (typeof ph !== "number") return `<p class="muted small">No probability: a starting goalie or team data was missing.</p>`;
    const [ca, cb] = sideColors(g.away, g.home);
    return `<div class="winbar" role="img" aria-label="${esc(teamName(g.away))} ${pct(pa)}, ${esc(teamName(g.home))} ${pct(ph)}" style="--ca:${ca};--cb:${cb};--split:${(pa * 100).toFixed(1)}%">
      <i class="away"></i><i class="home"></i><i class="mk gl gl1"></i><i class="mk gl gl2"></i><i class="mk bl bl1"></i><i class="mk bl bl2"></i><i class="mk cl"></i><i class="mk cc"></i><i class="puck${goldish(ca) || goldish(cb) ? " lt" : ""}"></i>
      <span class="l">${esc(g.away)} ${pct(pa)}</span><span class="r">${esc(g.home)} ${pct(ph)}</span></div>`;
  }
  function goalieLine(g) {
    // one goalie per row: the name and team in the first column, the status tag beside
    // them in the second, so a tag never wraps away from its goalie
    const one = (ab, gk) => gk && gk.name ? `<span class="gk-n">${pname(gk.name, gk.player_id)}&nbsp;<span class="muted">(${esc(ab)})</span></span><span class="gk-s">${srcPill(gk.source)}</span>`
      : `<span class="gk-n muted">${esc(ab)}: no starter yet</span><span class="gk-s"></span>`;
    return `<span class="gks">${one(g.away, g.away_side.goalie)}${one(g.home, g.home_side.goalie)}</span>`;
  }
  // the pick: a gold banner with a star crest, the one solid yellow field in the app.
  // Hockey names its three stars after every game; the pick is today's first star.
  const pickBanner = (ab, p) => `<div class="pickline">
      <span class="pk-star" aria-hidden="true">${icon("star", "pk-ico")}</span>
      <span class="pk-body"><span class="pk-tag"><span class="pill mypick">My pick</span> ${badge(ab, "", false, true)}</span><span class="pick">${esc(teamName(ab))}</span></span>
      <span class="pick-pct"><b>${pct(p)}</b><span>to win</span></span></div>`;
  // a finished game: the final score on a scoreboard strip under the banner, and
  // whether the pick won or lost (overtime and shootout results count)
  function finalStrip(g) {
    const r = g.result;
    if (!r) return "";
    const won = r.pick === "win", per = r.last_period && r.last_period !== "REG" ? "/" + r.last_period : "";
    const side = (ab, sc) => `<span class="fs-team${r.winner === ab ? " w" : ""}"><span>${esc(ab)}</span> <b>${esc(sc)}</b></span>`;
    return `<div class="finalbar ${won ? "won" : "lost"}" aria-label="Final${per ? " in " + (r.last_period === "OT" ? "overtime" : "a shootout") : ""}: ${esc(teamName(g.away))} ${esc(r.away_score)}, ${esc(teamName(g.home))} ${esc(r.home_score)}. My pick ${won ? "won" : "lost"}.">
      <span class="fs-lab">Final${esc(per)}</span>${side(g.away, r.away_score)}${side(g.home, r.home_score)}<span class="pill ${won ? "win" : "loss"}">${won ? "Win" : "Loss"}</span></div>`;
  }
  function gameCard(g) {
    const fav = g.projected;
    const mk = g.market ? (fav === g.home ? g.market.home_prob : g.market.away_prob) : null;
    const yours = mine(g.home) || mine(g.away);
    const [ca, cb] = sideColors(g.away, g.home);
    return `<article class="card game ${yours ? "edge" : ""}" style="--ca:${ca};--cb:${cb};${yours ? "border-left-color:var(--gold)" : ""}">
      <span class="rank">RANK ${esc(g.rank)}</span>
      <p class="match">${badge(g.away, "big", true)} <span class="at">@</span> ${badge(g.home, "big", true)}</p>
      <p class="small muted" style="margin:0">${esc(teamName(g.away))} at ${esc(teamName(g.home))}, ${esc(localTime(g.start_utc))}${g.venue ? ", " + esc(g.venue) : ""}</p>
      ${winbar(g)}
      ${fav ? pickBanner(fav, g.win_prob) + finalStrip(g) + `
      <p style="margin:4px 0" class="row">${tierPill(g.tier)} ${g.gate_cleared ? '<span class="pill lead">Gate cleared</span>' : ""} ${yours ? '<span class="pill gold">Your team</span>' : ""}</p>` : ""}
      <dl class="kv"><dt>Market</dt><dd>${typeof mk === "number" ? pct(mk) + ` for ${esc(fav)}` + (g.market.books ? ` <span class="muted">(${esc(g.market.books)} books)</span>` : "") : '<span class="muted">no line yet</span>'}</dd>
      <dt class="gk-dt">Goalies</dt><dd class="gk-dd">${goalieLine(g)}</dd>
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
    const hero = title => `<section class="hero">${RINK}<div class="hero-in"><p class="eyebrow">${esc(longDate(D.date))}</p><h1>${title}</h1><p class="sub">${updated()}</p>${tiles}</div></section>`;
    if (!gs.length) {
      const nx = D.slate.next;
      return `${hero("No games today")}<div class="card empty">${EMPTY_NET}<p>${nx && nx.date ? `Next games: <b>${esc(longDate(nx.date))}</b>, ${esc(nx.game_count)} games.` : "No games are scheduled in the next few weeks."}</p>
        ${nx && nx.matchups ? `<p class="small muted">${nx.matchups.map(esc).join(", ")}</p>` : ""}<p class="small">My Teams, Results and Model still update every day.</p></div>`;
    }
    const intro = st.teams.length ? "" : `<div class="note">Pick your teams in <button class="linkish" data-tab="myteams">My Teams</button> to see their lines every day and spot their games here.</div>`;
    return `${hero("Today's games, ranked")}${dataNotes()}${intro}<div class="grid">${gs.map(gameCard).join("")}</div>`;
  }

  // ---------- pillars, form, absences ----------
  const PILLARS = [["p1_form", "P1 Form"], ["p2_absence", "P2 Absence"], ["p3_motivation", "P3 Motivation"], ["p4_gvi", "P4 Goalie"], ["p5_friction", "P5 Friction"], ["adj_raa", "Adj RAA"]];
  function pillarTable(g) {
    const a = g.away_side.pillars || {}, h = g.home_side.pillars || {};
    const rows = PILLARS.map(([k, lab]) => `<tr class="${k === "adj_raa" ? "tot" : ""}">${td(esc(lab))}${td(num(a[k]), "n")}${td(num(h[k]), "n")}${td(typeof a[k] === "number" && typeof h[k] === "number" ? signed(h[k] - a[k]) : "-", "n")}</tr>`).join("");
    return tbl(th("Factor") + th(g.away, "n") + th(g.home, "n") + th("Home edge", "n"), rows);
  }
  // where a missing player came from: the last lineup, and which injury report
  function absNote(a) {
    const src = String(a.source || "");
    const by = src.indexOf("ESPN") >= 0 ? "ESPN" : src.indexOf("DFO") >= 0 ? "Daily Faceoff" : src.indexOf("HR") >= 0 ? "Hockey-Reference" : "";
    const note = a.injury_note || (src === "LINEUP_DIFF" ? "Did not dress in the last game" : "");
    return esc(note) + (by && a.injury_note ? ` <span class="muted">(${by})</span>` : "");
  }
  // build notes (a source that could not be reached, a fallback in use) shown where they matter
  const dataNotes = () => (D.problems || []).length ? `<div class="note warn" role="status"><b>Data note:</b> ${D.problems.map(esc).join(" ")}</div>` : "";
  function absences(g) {
    const rows = [["away", g.away], ["home", g.home]].flatMap(([s, ab]) => (g[s + "_side"].absences || []).map(a =>
      `<tr>${td(`<b>${esc(ab)}</b>`)}${td(pname(a.name, a.player_id))}${td(esc(a.position))}${td(pct(a.toi_share), "n hm")}${td(absNote(a), "small")}</tr>`)).join("");
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
    if (!gs.length) return `${head("matchups", "Matchups")}${stamp()}${emptyCard("No games today.")}`;
    return `${head("matchups", "Matchups")}${stamp()}${dataNotes()}<p class="small muted">Each team's five pillar scores (rating points above or below neutral), recent form and missing players. The home edge column is home minus away; home ice adds ${num(((D.model || {}).settings_in_use || {}).h, 2)} more.</p>` +
      gs.map(g => `<section class="card"><h2 class="teamline" style="margin-top:0">${badge(g.away, "", true)} <span class="at">@</span> ${badge(g.home, "", true)} <span class="small muted">${esc(localTime(g.start_utc))}</span></h2>
        <p class="row">${g.projected ? `<span class="pick">${esc(teamName(g.projected))} ${pct(g.win_prob)}</span> ${tierPill(g.tier)}` : ""}</p>
        <h3>Five pillars</h3>${pillarTable(g)}<h3>Form</h3>${formTable(g)}${absences(g)}</section>`).join("");
  }

  // ---------- lines ----------
  function linesTable(L) {
    const w = st.win;
    if (!L || L.error || !(L.lines || []).length) return `<p class="muted small">Lines are not available for this team right now${L && L.error ? ` (${esc(L.error)})` : ""}.</p>`;
    const groups = (L.lines || []).map(x => ["F" + x.line, x]).concat((L.pairs || []).map(x => ["D" + x.pair, x]));
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
        return `<tr class="${lead.points ? "lead" : ""}">${td(i === 0 ? `<span class="unit">${esc(unit)}</span>` : "")}${td(esc(p.pos), "hm")}${td(pname(p.name, p.nhl_id) + " " + status + pimTag)}${td(esc(p.pp || ""), "hm")}
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
      `<button class="chip" data-act="team" data-arg="${ab}" aria-pressed="${mine(ab)}" style="${teamVars(ab)}">${ab}</button>`).join("")}</div>`).join("") +
      `<p class="small" style="margin-top:10px">${st.teams.length ? `Following: ${st.teams.map(t => esc(teamName(t))).join(", ")}. <button class="linkish" data-act="clearTeams">Clear</button>` : "Tap a team to follow it. Your picks stay in this browser."}</p>`;
  }
  // ---------- a team's places in the standings (My Teams) ----------
  const ordinal = n => { const v = n % 100; return n + (v >= 11 && v <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th")); };
  const ptsText = n => `${n} point${n === 1 ? "" : "s"}`;
  function standPlaces(ab) {
    const rows = (D.standings || {}).teams || [], r = rows.find(x => x.team === ab);
    if (!r || !r.div_rank) return "";
    const conf = rows.filter(x => x.conference === r.conference);
    const wcPts = k => { const t = conf.find(x => x.wc_rank === k); return t ? t.pts : null; };
    let wcBig, wcSub;
    if (!r.wc_rank) { wcBig = "Top 3"; wcSub = `in the ${r.division}: a division playoff spot`; }
    else if (r.wc_rank <= 2) {
      const out = wcPts(3), gap = out == null ? null : r.pts - out;
      wcBig = ordinal(r.wc_rank); wcSub = `wild card, ${gap == null ? "in a playoff spot" : gap > 0 ? ptsText(gap) + " above the line" : "level on points with the first team out"}`;
    } else {
      const last = wcPts(2), gap = last == null ? null : last - r.pts;
      wcBig = ordinal(r.wc_rank); wcSub = `in the wild-card race, ${gap == null ? "outside the playoff spots" : gap > 0 ? ptsText(gap) + " out" : "level on points with the second wild card"}`;
    }
    const place = (k, big, sub, tip) => `<div class="spos" data-tip="${esc(tip)}" tabindex="0"><span class="k">${esc(k)}</span><b>${esc(big)}</b><span class="s">${esc(sub)}</span></div>`;
    return `<div class="splaces">
      ${place("Wild Card", wcBig, wcSub, `Where the team stands in the ${r.conference} Conference playoff race: the top three in each division make the playoffs, then the next two teams in the conference (the wild cards). The playoff line sits under the second wild card.`)}
      ${place("Division", ordinal(r.div_rank), `of ${rows.filter(x => x.division === r.division).length} in the ${r.division}`, `Place in the ${r.division} Division, in the NHL's order: points, then the tiebreakers.`)}
      ${place("Conference", ordinal(r.conf_rank), `of ${conf.length} in the ${r.conference}`, `Place in the ${r.conference} Conference, in the NHL's order: points, then the tiebreakers.`)}
      ${place("League", ordinal(r.league_rank), `of ${rows.length} teams`, "Place among all NHL teams, in the NHL's order: points, then the tiebreakers.")}
    </div><p class="tiny muted" style="margin:2px 0 8px">Standings ${(D.standings || {}).as_of_utc ? "as of " + esc(localStamp(D.standings.as_of_utc)) : "from today's data"}. <button class="linkish" data-tab="standings">Open the full standings</button></p>`;
  }
  // ---------- My Teams: last game, Game Score by player ----------
  // Each player's game in two thin bars on one axis: offense on top, defense below,
  // each split into production, play driving, special teams and usage. Parts above
  // zero stack right of the zero line, parts below it stack left.
  const GS_PARTS = [["prod", "Production"], ["drive", "Play driving"], ["st", "Special teams"], ["use", "Usage"]];
  const GS_RESULT = { W: "Won", L: "Lost", OTL: "OT loss" };
  function gsTrack(vals, side, lo, span) {
    let pos = 0, neg = 0, out = "";
    const segs = vals.map((v, i) => ({ v, i })).filter(s => Math.abs(s.v) >= 0.005);
    const ends = { p: segs.filter(s => s.v > 0).pop(), n: segs.filter(s => s.v < 0).pop() };
    segs.forEach(s => {
      const w = Math.abs(s.v) / span * 100;
      const left = s.v > 0 ? (0 - lo + pos) / span * 100 : (0 - lo - neg - Math.abs(s.v)) / span * 100;
      if (s.v > 0) pos += s.v; else neg += -s.v;
      const end = s === (s.v > 0 ? ends.p : ends.n) ? (s.v > 0 ? " endp" : " endn") : "";
      out += `<i class="gs-seg gs-${GS_PARTS[s.i][0]}${s.v < 0 ? " neg" : ""}${end}" style="left:${left.toFixed(2)}%;width:${w.toFixed(2)}%" data-tip="${esc(GS_PARTS[s.i][1])}, ${side}: ${signed(s.v)}"></i>`;
    });
    return out;
  }
  function gsRow(p, lo, span) {
    const say = (lab, v) => `${lab} ${GS_PARTS.map(([, n], i) => `${n.toLowerCase()} ${signed(v[i])}`).join(", ")}`;
    return `<div class="gs-row"><span class="gs-name">${pname(p.name, p.id)}</span><b class="gs-val" data-tip="Game Score ${signed(p.gs)}: offense ${signed(p.o.reduce((a, b) => a + b, 0))} plus defense ${signed(p.d.reduce((a, b) => a + b, 0))}. Above 0 he helped his team; below 0 he hurt it.">${signed(p.gs)}</b>
      <div class="gs-bars" role="img" aria-label="${esc(p.name)}: ${signed(p.gs)}. ${esc(say("Offense", p.o))}. ${esc(say("Defense", p.d))}.">
      <span class="gs-od" aria-hidden="true"><span class="o">Offense</span><span class="d">Defense</span></span>
      <div class="gs-plot" style="--z:${((0 - lo) / span * 100).toFixed(2)}%"><span class="gs-trk o">${gsTrack(p.o, "offense", lo, span)}</span><span class="gs-trk d">${gsTrack(p.d, "defense", lo, span)}</span></div></div></div>`;
  }
  // the goalie is measured in goals saved, not Game Score, so he gets a plain sentence
  // instead of a bar on the skaters' scale
  function gsGoalie(g) {
    const has = typeof g.gsax === "number";
    const say = !has ? `Faced ${esc(g.shots)} shots and allowed ${esc(g.ga)} goals; expected goals were not available for this game.`
      : `Allowed ${esc(g.ga)} goal${g.ga === 1 ? "" : "s"} on ${esc(g.shots)} shots. An average goalie would have allowed ${num(g.xga, 2)} on those shots, so he ${g.gsax >= 0 ? "saved" : "allowed"} ${num(Math.abs(g.gsax), 2)} goals more than expected.`;
    return `<div class="gs-row gk"><span class="gs-name">${pname(g.name, g.id)}</span><b class="gs-val" data-tip="Goals saved above expected: the goals an average goalie would have allowed on his shots minus the goals he allowed. Above 0 he stopped more than expected.">${has ? signed(g.gsax) : "-"}</b><span class="gs-gk-txt small">${say}</span></div>`;
  }
  function lastGame(ab) {
    const t = ((D.gamescore || {}).teams || {})[ab];
    if (!t || !Array.isArray(t.skaters)) return "";
    const sk = t.skaters, gk = t.goalies || [];
    const ends = sk.flatMap(p => [p.o, p.d].map(v => [v.filter(x => x < 0).reduce((a, b) => a + b, 0), v.filter(x => x > 0).reduce((a, b) => a + b, 0)]));
    let lo = Math.min(0, ...ends.map(e => e[0])), hi = Math.max(0, ...ends.map(e => e[1]));
    // keep the zero line between 30% and 70% of the width, so the Hurt and Helped tags
    // beside it always fit, even on a phone
    lo = Math.floor(lo * 2) / 2; hi = Math.ceil(hi * 2) / 2;
    if (hi - lo < 1) hi = lo + 1;
    for (let k = 0; k < 40 && -lo / (hi - lo) > 0.7; k++) hi += 0.5;
    for (let k = 0; k < 40 && -lo / (hi - lo) < 0.3; k++) lo -= 0.5;
    const span = hi - lo;
    const per = t.last_period && t.last_period !== "REG" ? "/" + t.last_period : "";
    const us = `<span class="fs-team${t.result === "W" ? " w" : ""}"><span>${esc(ab)}</span> <b>${esc(t.for)}</b></span>`, them = `<span class="fs-team${t.result !== "W" ? " w" : ""}"><span>${esc(t.opp)}</span> <b>${esc(t.against)}</b></span>`;
    const strip = `<div class="finalbar team ${t.result === "W" ? "won" : "lost"}"><span class="fs-lab">Final${esc(per)}</span>${t.home ? them + us : us + them}<span class="pill ${t.result === "W" ? "win" : "loss"}">${GS_RESULT[t.result] || esc(t.result)}</span></div>`;
    const group = (lab, rows) => rows ? `<p class="gs-grp">${lab}</p>${rows}` : "";
    const fw = sk.filter(p => p.pos !== "D"), dm = sk.filter(p => p.pos === "D");
    const key = GS_PARTS.map(([k, n]) => `<span class="gs-key" data-tip="${esc((TIPS.gs || {})[n] || n)}"><i class="gs-sw gs-${k}"></i>${esc(n)}</span>`).join("");
    const cells = v => v.map(x => td(signed(x), "n")).join("");
    const rows = sk.map(p => `<tr>${td(esc(p.name))}${td(esc(p.pos))}${td(p.toi == null ? "-" : num(p.toi, 1), "n hm")}${td(`<b>${signed(p.gs)}</b>`, "n")}${cells(p.o)}${cells(p.d)}</tr>`).join("");
    const hd = thT("Player", "The player. His name in the chart above opens his NHL.com page.") + thT("Pos", TIPS.heads.Pos) + thT("TOI", "Time on ice in the game, minutes.", "n hm") + thT("Game Score", TIPS.gs["Game Score"], "n") +
      ["Production", "Play driving", "Special teams", "Usage"].map(n => thT("O " + n.split(" ")[0], `Offense, ${n.toLowerCase()}: ${TIPS.gs[n]}`, "n")).join("") +
      ["Production", "Play driving", "Special teams", "Usage"].map(n => thT("D " + n.split(" ")[0], `Defense, ${n.toLowerCase()}: ${TIPS.gs[n]}`, "n")).join("");
    const note = t.inputs === "full" ? "" : `<p class="small muted">Play driving, special teams and usage were not available for this game (the shift charts or shot values were missing), so only production is scored.</p>`;
    return `<details class="lastgame" open><summary><span class="lg-title">Last game</span> <span class="small muted">${esc(shortDay(t.date))}, ${t.home ? "vs" : "at"} ${esc(t.opp)}</span></summary>
      ${strip}
      <p class="small gs-how"><b>Two bars per player: the top bar is his offense</b> (what he created: goals, assists, shots, chances), <b>the bottom bar his defense</b> (what he stopped: blocks, chances against). Parts right of the center line helped his team; parts left of it hurt. The number beside each name is his Game Score, the two bars added together, in goals-like units: above 0 he helped, below 0 he hurt, 0 is an ordinary game.</p>
      <div class="gs-legend">${key}</div>
      <div class="gs-chart"><div class="gs-head"><span class="gs-name">Player</span><span class="gs-val" data-tip="${esc(TIPS.gs["Game Score"])}">Game Score</span>
      <div class="gs-sides" style="--z:${((0 - lo) / span * 100).toFixed(2)}%"><span class="hurt" data-tip="Left of the line: the parts of his game that cost his team.">Hurt</span><span class="helped" data-tip="Right of the line: the parts of his game that helped his team.">Helped</span></div></div>
      ${group("Forwards", fw.map(p => gsRow(p, lo, span)).join(""))}${group("Defense", dm.map(p => gsRow(p, lo, span)).join(""))}</div>
      ${gk.length ? `<div class="gs-goalies"><div class="gs-head"><span class="gs-name">Goalie</span><span class="gs-val" data-tip="${esc(TIPS.gs.Goalie)}">Goals saved</span><span></span></div>${gk.map(gsGoalie).join("")}</div>` : ""}
      ${note}
      <details class="more"><summary>Numbers</summary>${tbl(hd, rows, "gs-table")}</details>
      <p class="tiny muted">This app's own Game Score, built on the public Game Score formula (Dom Luszczyszyn, Hockey Graphs, 2016). Data: NHL play-by-play and shift charts; expected goals and season rates from <a href="https://moneypuck.com" target="_blank" rel="noopener">MoneyPuck.com</a>.</p></details>`;
  }
  function teamCard(ab) {
    const t = (D.teams || {})[ab] || {};
    const g = games().find(x => x.home === ab || x.away === ab);
    const tonight = g ? `<p class="row small">Tonight: ${esc(g.away)} @ ${esc(g.home)}, ${esc(localTime(g.start_utc))}. ${g.projected ? `Projected winner ${esc(g.projected)} ${pct(g.win_prob)} ${tierPill(g.tier)}` : ""}</p>` : `<p class="small muted">No game today.</p>`;
    return `<section class="card edge teamcard" style="${teamVars(ab)}"><div class="tc-head">${badge(ab, "xl", true)}<div><h2>${esc(t.name || ab)}</h2><span class="small muted">${t.record ? esc(t.record) + ", " + esc(t.pts) + " pts" : ""}${t.division ? ", " + esc(t.division) : ""}</span></div></div>${standPlaces(ab)}${tonight}${lastGame(ab)}${linesTable((D.lines || {})[ab])}</section>`;
  }
  function vMyTeams() {
    const body = st.teams.length ? st.teams.map(teamCard).join("") : `<div class="card empty">No teams picked yet. Choose any of the 32 teams below.</div>`;
    return `${head("myteams", "My Teams")}${stamp()}<div class="row between noprint" style="margin-bottom:10px">${winToggle()}</div>${body}
      <section class="card noprint"><h2 style="margin-top:0">Choose your teams</h2>${chooser()}</section>`;
  }
  function vLines() {
    const gs = games().filter(g => g.projected);
    if (!gs.length) return `${head("lines", "Projected winners' lines")}${stamp()}${emptyCard(`No games today. Every team's lines are in <button class="linkish" data-tab="myteams">My Teams</button>.`)}`;
    return `${head("lines", "Projected winners' lines")}${stamp()}<p class="small muted">The four forward lines and three defense pairs of each game's projected winner, in rank order, with every player's stats. The shaded line leads its team's forwards in points per game.</p><div class="row noprint" style="margin-bottom:10px">${winToggle()}</div>` +
      gs.map(g => `<section class="card"><h2 class="teamline" style="margin-top:0">#${esc(g.rank)} ${badge(g.projected, "", true)} ${esc(teamName(g.projected))} <span class="small muted">${esc(g.away)} @ ${esc(g.home)}, ${esc(localTime(g.start_utc))}, ${pct(g.win_prob)}</span> ${tierPill(g.tier)}</h2>${linesTable((D.lines || {})[g.projected])}</section>`).join("");
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
    if (!rows.length) return `${head("goalies", "Starting goalies")}${stamp()}${emptyCard("No games today.")}`;
    const body = rows.map(({ ab, g, gk }) => !gk.name ? `<tr>${td(badge(ab, "sm"))}<td colspan="8" class="muted">No starter found yet</td></tr>` :
      `<tr class="${mine(ab) ? "lead" : ""}">${td(`${badge(ab, "sm")}<div class="tiny muted">${esc(g.away)} @ ${esc(g.home)}</div>`)}${td(pname(gk.name, gk.player_id) + ((gk.window_toi_min || 0) < 180 ? ' <span class="pill thin">Thin</span>' : ""))}
        ${td(sv(gk.season_sv_pct), "n")}${td(num(gk.gsax, 2), "n")}${td(sv(gk.hdsv_pct), "n hm")}${td(Math.round(gk.window_toi_min || 0) + "m", "n hm")}${td(esc(gk.days_rest ?? "-") + "d", "n")}${td(srcPill(gk.source))}
        ${td(gk.reported_by ? (gk.source_url ? `<a href="${esc(gk.source_url)}" target="_blank" rel="noopener">${esc(gk.reported_by)}</a>` : esc(gk.reported_by)) : "-", "hm small")}</tr>`).join("");
    return `${head("goalies", "Starting goalies")}${stamp()}<p class="small muted">Every probable starter tonight, highest season save percentage first (ties: GSAx, then high-danger save percentage). Projected starters often change by late afternoon; the evening update carries the later confirmations.</p>
      ${tbl(th("Team") + th("Starter") + th("SV%", "n") + th("GSAx", "n") + th("HDSV%", "n hm") + th("Window", "n hm") + th("Rest", "n") + th("Status") + th("Reported by", "hm"), body)}`;
  }

  // ---------- injuries ----------
  // Every injured player the injury reports name, league-wide (ESPN first, Daily
  // Faceoff's injured lists for today's teams), grouped by team.
  const INJ_VIEWS = [["all", "All teams"], ["today", "Today's games"], ["mine", "My Teams"]];
  const INJ_CLASS = { GTD: "projected", DTD: "projected", OUT: "bad", IR: "bad", LTIR: "bad", "IR-NR": "rotation", SUSP: "rotation" };
  const injPill = c => `<span class="pill inj ${INJ_CLASS[c] || "bad"}">${esc(c || "OUT")}</span>`;
  const shortDay = ymd => { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(ymd || "")); return m ? new Date(+m[1], +m[2] - 1, +m[3]).toLocaleDateString([], { month: "short", day: "numeric" }) : "-"; };
  const thT = (label, tip, cls = "") => `<th class="${cls}" data-tip="${esc(tip)}">${esc(label)}</th>`;
  function vInjuries() {
    const I = D.injuries;
    const seg = `<div class="seg inj" role="group" aria-label="Injuries view">${INJ_VIEWS.map(([k, lab]) =>
      `<button data-act="inj" data-arg="${k}" aria-pressed="${st.inj === k}">${lab}</button>`).join("")}</div>`;
    const top = `${head("injuries", "Injuries")}${stamp()}<div class="row noprint" style="margin-bottom:10px">${seg}</div>`;
    if (!I || !Array.isArray(I.players)) return top + emptyCard("The injury list is not in this data file yet.");
    const playing = {};
    games().forEach(g => { playing[g.away] = g; playing[g.home] = g; });
    const teams = st.inj === "today" ? games().flatMap(g => [g.away, g.home]) : st.inj === "mine" ? st.teams.slice() : TEAMS_ORDER.slice();
    if (st.inj === "mine" && !teams.length) return top + emptyCard('Pick your teams in <button class="linkish" data-tab="myteams">My Teams</button> to see their injuries here.');
    if (st.inj === "today" && !teams.length) return top + emptyCard("No games today.");
    const rows = I.players.filter(p => teams.includes(p.team));
    const n = (...codes) => rows.filter(p => codes.includes(p.status)).length;
    const tiles = `<div class="tiles"><div class="tile"><b>${rows.length}</b><span>players listed</span></div>
      <div class="tile"><b>${n("DTD", "GTD")}</b><span>day to day</span></div>
      <div class="tile"><b>${n("OUT")}</b><span>out</span></div>
      <div class="tile"><b>${n("IR", "LTIR", "IR-NR")}</b><span>on injured reserve</span></div></div>`;
    const thead = thT("Player", "The injured player; his name opens his official NHL.com page. The line under it is the latest report.") + th("Pos", "hm") +
      thT("Status", "The list he is on: GTD game-time decision, DTD day to day, OUT out, IR injured reserve, LTIR long-term injured reserve, IR-NR injured non-roster, SUSP suspended.") +
      thT("Injury", "The body part the report names; Undisclosed when the team has not said.") +
      thT("Reported", "Date of the latest report on this player.", "n hm") +
      thT("Est. return", "The injury report's estimated return date. Teams rarely confirm these, so they often move.", "n");
    const card = ab => {
      const list = rows.filter(p => p.team === ab), g = playing[ab];
      const body = list.map(p => `<tr>${td(`${pname(p.name, p.player_id)}${p.in_lineup ? ' <span class="pill ok">In lineup</span>' : ""}${p.note ? `<div class="tiny muted inj-note">${esc(p.note)}</div>` : ""}`)}${td(esc(p.position || "-"), "hm")}${td(injPill(p.status))}${td(esc(p.injury || "-"), "small")}${td(esc(shortDay(p.reported)), "n hm")}${td(esc(shortDay(p.return_est)), "n")}</tr>`).join("");
      return `<section class="card ${mine(ab) ? "edge" : ""}" data-team="${esc(ab)}"><h2 class="teamline" style="margin-top:0">${badge(ab, "", true)} ${esc(teamName(ab))} <span class="small muted">${list.length} listed${g ? `; today ${esc(g.away)} @ ${esc(g.home)}, ${esc(localTime(g.start_utc))}` : ""}</span></h2>${tbl(thead, body, "inj")}</section>`;
    };
    const listed = teams.filter(ab => rows.some(p => p.team === ab)), none = teams.filter(ab => !rows.some(p => p.team === ab));
    const names = { espn: "ESPN's injury report", hockey_reference: "Hockey-Reference's injury report", daily_faceoff: "Daily Faceoff's injured lists for today's teams" };
    const src = (I.sources || []).map(u => names[u] || u);
    return top + tiles + listed.map(card).join("") +
      (none.length ? `<p class="small muted">No injuries listed: ${none.map(esc).join(", ")}.</p>` : "") +
      `<p class="tiny muted">${src.length ? "From " + src.map(esc).join(" and ") + ". " : ""}Estimated returns are estimates and often move. IN LINEUP means Daily Faceoff has him in today's lineup, so he is expected to play.</p>`;
  }

  // ---------- standings ----------
  // The NHL's own order: divisionSequence, conferenceSequence, leagueSequence and
  // wildcardSequence come from the standings feed, so ties are already broken.
  const STAND_VIEWS = [["wildcard", "Wild Card"], ["division", "Division"], ["conference", "Conference"], ["league", "League"]];
  const CONF_ORDER = ["Eastern", "Western"];
  const DIV_ORDER = ["Atlantic", "Metropolitan", "Central", "Pacific"];
  function streakCell(code) {
    const m = /^(W|L|OT)(\d+)$/.exec(String(code || ""));
    if (!m) return esc(code || "-");
    const n = +m[2], what = m[1] === "W" ? (n === 1 ? "Won the last game" : `Won ${n} in a row`)
      : m[1] === "L" ? (n === 1 ? "Lost the last game in regulation" : `Lost ${n} in a row in regulation`)
      : (n === 1 ? "Lost the last game in overtime or a shootout" : `Lost ${n} in a row in overtime or a shootout`);
    return `<abbr class="tip" data-tip="${esc(what)}.">${esc(code)}</abbr>`;
  }
  function standRow(r, rank) {
    const cl = r.clinch ? ` <span class="pill clinch">${esc(r.clinch)}</span>` : "";
    return `<tr class="${mine(r.team) ? "lead" : ""}">${td(esc(rank ?? "-"), "n")}${td(`<span class="steam">${badge(r.team, "sm")}<span class="tname hm">${esc(teamName(r.team))}</span>${cl}</span>`)}
      ${td(esc(r.gp ?? "-"), "n")}${td(esc(r.w ?? "-"), "n")}${td(esc(r.l ?? "-"), "n")}${td(esc(r.otl ?? "-"), "n")}${td(`<b>${esc(r.pts ?? "-")}</b>`, "n")}
      ${td(typeof r.p_pct === "number" ? r.p_pct.toFixed(3).replace(/^0/, "") : "-", "n hm")}${td(esc(r.rw ?? "-"), "n hm")}${td(esc(r.row ?? "-"), "n hm")}
      ${td(esc(r.gf ?? "-"), "n hm")}${td(esc(r.ga ?? "-"), "n hm")}${td(typeof r.diff === "number" ? (r.diff > 0 ? "+" : "") + r.diff : "-", "n")}
      ${td(esc(r.l10 || "-"), "n hm")}${td(streakCell(r.streak), "n hm")}</tr>`;
  }
  const standHead = () => th("Rk", "n") + th("Team") + th("GP", "n") + th("W", "n") + th("L", "n") + th("OTL", "n") + th("PTS", "n") +
    th("P%", "n hm") + th("RW", "n hm") + th("ROW", "n hm") + th("GF", "n hm") + th("GA", "n hm") + th("DIFF", "n") + th("L10", "n hm") + th("STRK", "n hm");
  const groupRow = (label, cls = "") => `<tr class="grp ${cls}"><td colspan="15">${esc(label)}</td></tr>`;
  function vStandings() {
    const S = D.standings || {}, rows = S.teams || [];
    const seg = `<div class="seg stand" role="group" aria-label="Standings view">${STAND_VIEWS.map(([k, lab]) =>
      `<button data-act="stand" data-arg="${k}" aria-pressed="${st.stand === k}">${lab}</button>`).join("")}</div>`;
    const top = `${head("standings", "Standings")}<p class="sub">${S.as_of_utc ? `NHL standings as of ${esc(localStamp(S.as_of_utc))}.` : esc(longDate(D.date)) + "."} ${updated()}</p>
      <div class="row noprint" style="margin-bottom:10px">${seg}</div>`;
    if (!rows.length) return top + emptyCard("The standings are not in this data file yet.");
    const by = (k, v) => rows.filter(r => r[k] === v);
    const sortBy = (list, k) => list.slice().sort((a, b) => (a[k] ?? 99) - (b[k] ?? 99) || a.team.localeCompare(b.team));
    const card = (title, body, foot = "") => `<section class="card"><h2 style="margin-top:0">${esc(title)}</h2>${tbl(standHead(), body, "standings")}${foot}</section>`;
    let out = "";
    if (st.stand === "wildcard") {
      out = CONF_ORDER.map(conf => {
        const cr = by("conference", conf);
        if (!cr.length) return "";
        const divs = DIV_ORDER.filter(dv => cr.some(r => r.division === dv));
        let body = divs.map(dv => groupRow(dv) + sortBy(cr.filter(r => r.division === dv && (r.div_rank || 99) <= 3), "div_rank").map(r => standRow(r, r.div_rank)).join("")).join("");
        const wc = sortBy(cr.filter(r => (r.wc_rank || 0) >= 1), "wc_rank");
        body += groupRow("Wild Card") + wc.map((r, i) => standRow(r, r.wc_rank).replace("<tr class=\"", `<tr class="${i === 2 ? "cut " : ""}`)).join("");
        return card(`${conf} Conference`, body);
      }).join("") + `<p class="tiny muted">The top three in each division and the next two teams in each conference (the wild cards) make the playoffs. The line under the second wild card is the playoff line.</p>`;
    } else if (st.stand === "division") {
      out = DIV_ORDER.filter(dv => rows.some(r => r.division === dv)).map(dv => card(`${dv} Division`, sortBy(by("division", dv), "div_rank").map(r => standRow(r, r.div_rank)).join(""))).join("");
    } else if (st.stand === "conference") {
      out = CONF_ORDER.filter(c => rows.some(r => r.conference === c)).map(c => card(`${c} Conference`, sortBy(by("conference", c), "conf_rank").map(r => standRow(r, r.conf_rank)).join(""))).join("");
    } else {
      out = card("League", sortBy(rows, "league_rank").map(r => standRow(r, r.league_rank)).join(""));
    }
    return top + out + `<p class="tiny muted">From the NHL's standings feed, in the league's own order (points, then the tiebreakers). Your teams are shaded.</p>`;
  }

  // ---------- results ----------
  function vResults() {
    const R = D.results || {}, s = R.season || {};
    const last = (R.last_games || []).map(r => {
      const away = (r.matchup || "").split(" @ ")[0];
      const ot = r.last_period && r.last_period !== "REG" ? ` (${esc(r.last_period)})` : "";
      return `<tr>${td(esc(r.matchup))}${td(badge(r.projected, "sm"))}${td(pct(+r.win_prob), "n")}${td(tierPill(r.tier), "hm")}${td(`${esc(away)} ${esc(r.away_score)}, ${esc((r.matchup || "").split(" @ ")[1])} ${esc(r.home_score)}${ot}`, "small")}${td(r.correct === "1" ? '<span class="pill ok">Right</span>' : '<span class="pill bad">Wrong</span>')}</tr>`;
    }).join("");
    const tiers = s.by_tier ? Object.entries(s.by_tier).map(([t, v]) => `<tr>${td(tierPill(t))}${td(v.games, "n")}${td(v.correct, "n")}${td(v.games ? pct(v.correct / v.games) : "-", "n")}</tr>`).join("") : "";
    const m = s.market || {};
    return `${head("results", "Results")}${stamp()}
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
    return `${head("model", "Model")}${stamp()}
      <section class="card"><h2 style="margin-top:0">Learning</h2><p>${esc(L.games || 0)} settled games this season; next learning cycle at ${esc(L.next_at || "-")}. ${esc(L.cycles || 0)} cycles so far, ${esc(L.accepted_cycles || 0)} adopted.</p><p>${status}</p>
      ${L.last ? `<p class="small muted">Last cycle ${esc(L.last.cycle)}: ${esc(L.last.reason || "")}</p>` : ""}
      ${tbl(th("Setting") + th("In use", "n") + th("Baseline", "n"), set)}<p class="tiny muted">Baseline: the 2025-26 calibration on 1,312 games. A change is adopted only after it beats the settings in use on at least 40 games it was not fit on, by two standard errors of log loss.</p></section>
      <section class="card"><h2 style="margin-top:0">Calibration this season</h2><p class="small muted">How often the projected winner won in each probability band. A calibrated model's 60 percent picks win about 60 percent of the time; small bands swing a lot.</p>${cal ? tbl(th("Band") + th("Games", "n") + th("Average", "n") + th("Won", "n") + th(""), cal) : '<p class="muted">No games yet.</p>'}</section>`;
  }

  // ---------- DFS ----------
  function vDfs() {
    const sk = (D.dfs || {}).skaters || [];
    // goalies: the team's win probability, highest first; ties go to the higher DK projection
    const wpv = v => (typeof v === "number" && !isNaN(v) ? v : -1);
    const gk = ((D.dfs || {}).goalies || []).slice().sort((a, b) => wpv(b.win_prob) - wpv(a.win_prob) || wpv(b.dk_proj) - wpv(a.dk_proj) || String(a.name || "").localeCompare(String(b.name || "")));
    if (!sk.length) return `${head("dfs", "DFS projections")}${stamp()}${emptyCard("No games today.")}`;
    const sRows = sk.map((p, i) => `<tr class="${mine(p.team) ? "lead" : ""}">${td(i + 1, "n")}${td(pname(p.name, p.player_id))}${td(badge(p.team, "sm"))}${td(esc(p.opp || ""), "hm")}${td(esc(p.position))}${td(`<b>${num(p.dk_proj, 1)}</b>`, "n")}${td(num(p.proj_goals, 2), "n")}${td(num(p.proj_assists, 2), "n")}${td(num(p.proj_sog, 1), "n hm")}${td(num(p.avg_toi_min, 1), "n hm")}</tr>`).join("");
    const gRows = gk.map((p, i) => `<tr class="${mine(p.team) ? "lead" : ""}">${td(i + 1, "n")}${td(pname(p.name, p.player_id))}${td(badge(p.team, "sm"))}${td(esc(p.opp || ""), "hm")}${td(pct(p.win_prob), "n")}${td(sv(p.save_pct), "n")}${td(num(p.gsax, 1), "n hm")}${td(`<b>${num(p.dk_proj, 1)}</b>`, "n")}</tr>`).join("");
    return `${head("dfs", "DFS projections")}${stamp()}<p class="small muted">Projected DraftKings fantasy points for tonight, from each player's season rates, recent ice time and his team's win probability. For research; check confirmed lineups before you set yours.</p>
      <section class="card"><h2 style="margin-top:0">Skaters</h2>${tbl(th("#", "n") + th("Skater") + th("Team") + th("Opp", "hm") + th("Pos") + th("DK", "n") + th("G", "n") + th("A", "n") + th("SOG", "n hm") + th("TOI", "n hm"), sRows)}</section>
      <section class="card"><h2 style="margin-top:0">Goalies</h2><p class="small muted">Every probable starter, sorted by his team's win probability, highest first (ties: the higher DK projection).</p>${tbl(th("#", "n") + th("Goalie") + th("Team") + th("Opp", "hm") + th("Win %", "n") + th("SV%", "n") + th("GSAx", "n hm") + th("DK", "n"), gRows)}</section>`;
  }

  // ---------- settings ----------
  // Install (1.12.0). Android, Chrome and Edge hand the page an install prompt
  // (beforeinstallprompt), so one tap installs. Safari, iOS and Firefox have no such
  // prompt: the app shows the steps instead, in place, for the device in hand.
  let installEvt = null, installedNow = false;
  const SNOOZE_MS = 14 * 864e5;            // Not now hides the banner for 14 days
  const isStandalone = () => window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  const appleTouch = () => /iPad|iPhone|iPod/.test(navigator.userAgent || "") || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const deviceKind = () => appleTouch() ? "ios" : /Android/.test(navigator.userAgent || "") ? "android" : "computer";
  const canInstall = () => location.protocol.startsWith("http") && !isStandalone() && !installedNow;
  const uiIco = id => `<span class="ui-ico" aria-hidden="true"><svg focusable="false"><use href="#i-${id}"></use></svg></span>`;
  function snoozedUntil() {
    let v = null; try { v = localStorage.getItem(HINT); } catch (e) { }
    if (v === "hide") {                   // 1.11.1 and earlier hid the banner for good: now 14 days from today
      v = String(Date.now() + SNOOZE_MS);
      try { localStorage.setItem(HINT, v); } catch (e) { }
    }
    return Number(v) || 0;
  }
  function howTo(os) {
    if (os === "ios") return `<section class="howto" data-os="ios"><h3>iPhone or iPad</h3>
      <p class="small muted">Apple does not let a website install itself, so it takes three taps.</p>
      <ol class="steps-list"><li>In Safari, tap <b>Share</b> ${uiIco("share")}. If Share is not showing, tap <span class="kbd" aria-label="More">•••</span> beside the address bar first (iOS 26), then Share.</li>
      <li>Scroll down and tap <b>Add to Home Screen</b> ${uiIco("addhome")}.</li>
      <li>If you see <b>Open as Web App</b>, leave it on. Tap <b>Add</b>.</li></ol>
      <p class="small"><b>Chrome or another browser</b> (iOS 16.4 or later): tap <b>Share</b> ${uiIco("share")}, then <b>Add to Home Screen</b>.</p></section>`;
    if (os === "android") return `<section class="howto" data-os="android"><h3>Android</h3>
      <ol class="steps-list"><li><b>Chrome:</b> ${installEvt ? "tap <b>Install now</b> below, or " : ""}tap the menu <span class="kbd" aria-label="Menu">⋮</span>, then <b>Install app</b> or <b>Add to Home screen</b>.</li>
      <li><b>Other browsers:</b> open the browser menu and pick <b>Install</b> or <b>Add to Home screen</b>.</li></ol></section>`;
    return `<section class="howto" data-os="computer"><h3>Computer</h3>
      <ol class="steps-list"><li><b>Chrome or Edge:</b> click the install icon at the right end of the address bar${installEvt ? ", or <b>Install now</b> below" : ""}.</li>
      <li><b>Safari on a Mac</b> (macOS Sonoma or later): choose File, then <b>Add to Dock</b>.</li></ol></section>`;
  }
  const deviceFirst = () => { const os = deviceKind(); return [os].concat(["ios", "android", "computer"].filter(x => x !== os)); };
  const installNow = () => installEvt && canInstall() ? '<p><button class="btn" data-act="getApp">Install now</button></p>' : "";
  function installSteps() {
    const hosted = location.protocol.startsWith("http");
    const here = hosted ? location.href.split("#")[0].split("?")[0] : PUBLIC_URL;
    const onPublic = here.startsWith(PUBLIC_URL);
    return `${isStandalone() ? "<p><b>You are using the installed app.</b> Share the link or the code below to put it on another device.</p>" : ""}
      <p>Open <a href="${esc(here)}">${esc(here)}</a> on any phone, tablet or computer. Nothing to sign up for.</p>
      ${!hosted || onPublic ? `<img class="qr" src="qr-app.png" width="160" height="160" alt="QR code for ${esc(PUBLIC_URL)}">` : ""}
      ${deviceFirst().map(howTo).join("")}
      ${installNow()}`;
  }
  function openSheet() {
    const d = $("#getapp");
    if (!d) return;
    const [os, ...rest] = deviceFirst();
    d.innerHTML = `<div class="sheet-in"><div class="sheet-head"><h2 id="getapp-title">Install NHL Daily</h2><button class="btn small ghost" data-act="closeApp">Close</button></div>
      <p class="small muted">It opens full screen from its own icon, like any other app, and keeps working with the last data it saved when you are offline.</p>
      ${howTo(os)}${installNow()}
      <details class="other-dev"><summary>Other devices</summary>${rest.map(howTo).join("")}</details></div>`;
    decorate(d);
    if (typeof d.showModal === "function") { if (!d.open) d.showModal(); }
    else d.setAttribute("open", "");
  }
  function closeSheet() {
    const d = $("#getapp");
    if (!d || !d.open) return;
    if (typeof d.close === "function") d.close(); else d.removeAttribute("open");
  }
  function getApp() {
    if (installEvt && canInstall()) {
      const e = installEvt;
      installEvt = null;                   // a prompt can be shown only once
      closeSheet();
      try {
        const p = e.prompt();
        if (p && p.catch) p.catch(() => openSheet());
        Promise.resolve(e.userChoice).then(c => { if (c && c.outcome === "accepted") installed(); else showInstall(); }).catch(() => {});
      } catch (err) { openSheet(); }
      showInstall();
      return;
    }
    openSheet();
  }
  function installed() {
    installedNow = true;
    installEvt = null;
    closeSheet();
    showInstall();
    if (st.tab === "settings") render();
  }
  function showInstall() {
    const btn = $("#getapp-btn");
    if (btn) btn.hidden = !canInstall();
    const box = $("#install");
    const touch = appleTouch() || /Android/.test(navigator.userAgent || "");
    if (!canInstall() || snoozedUntil() > Date.now() || !(installEvt || touch)) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = `<span>Add NHL Daily to your home screen for one-tap access.</span><span class="row"><button class="btn small" data-act="install">${installEvt ? "Install" : "How"}</button><button class="btn small ghost" data-act="hideInstall">Not now</button></span>`;
    decorate(box);
  }
  function injSources(inj) {
    if (!inj || !inj.loaded) return "No injury report reached; missing players come from each team's last lineup only";
    const names = { espn: "ESPN's injury report", hockey_reference: "Hockey-Reference's injury report", daily_faceoff: "Daily Faceoff's injured lists for today's teams" };
    const used = Array.isArray(inj.sources) ? inj.sources : String(inj.source || "").split("+");
    return `${esc(inj.rows)} players from ${used.map(u => esc(names[u] || u)).join(" and ")}`;
  }
  function vSettings() {
    const S = D.sources || {}, gl = S.starting_goalies || {}, inj = S.injuries || {}, ln = S.lines || {};
    const lines = `${esc(ln.daily_faceoff || 0)} teams from Daily Faceoff, ${esc(ln.shift_chart || 0)} rebuilt from the last game's shift chart${(ln.missing || []).length ? ", missing: " + ln.missing.map(esc).join(", ") : ""}`;
    return `${head("settings", "Settings")}
      <section class="card"><h2 style="margin-top:0">My Teams</h2>${chooser()}<p class="row" style="margin-top:8px"><span class="small muted">Stats window:</span> ${winToggle()}</p></section>
      <section class="card"><h2 style="margin-top:0">Team logos</h2><p class="row"><span class="seg" role="group" aria-label="Team logos"><button data-act="logos" data-arg="on" aria-pressed="${st.logos}">Show logos</button><button data-act="logos" data-arg="off" aria-pressed="${!st.logos}">Badges only</button></span></p>
        <p class="small muted">Official team logos load from the NHL's own image server (assets.nhle.com). A team whose logo cannot load keeps its color badge. With Badges only, the app asks the NHL's server for nothing.</p></section>
      <section class="card"><h2 style="margin-top:0">Get the app</h2>${installSteps()}</section>
      <section class="card"><h2 style="margin-top:0">Today's data</h2><dl class="kv"><dt>Slate</dt><dd>${esc(longDate(D.date))}, ${esc(D.slate.game_count)} games</dd>
        <dt>Built</dt><dd>${esc(localStamp(D.generated_utc))}</dd>
        <dt>Schedule</dt><dd>Twice a day, about ${dailyRuns().map(esc).join(" and ")} your time. GitHub can start a run late.</dd>
        <dt>Next update</dt><dd>${updSpan()}</dd>
        <dt>Starting goalies</dt><dd>${gl.loaded ? `${esc(gl.teams)} teams listed by Daily Faceoff, ${esc(gl.confirmed)} confirmed` : "Daily Faceoff not reached; starters are the model's rotation guess"}</dd>
        <dt>Injuries</dt><dd>${injSources(inj)}</dd>
        <dt>Betting lines</dt><dd>${esc(S.market_games || 0)} games</dd><dt>Lines</dt><dd>${lines}</dd>
        <dt>Model</dt><dd>RAA model ${esc(D.model_version)}, app ${APP_VERSION}</dd></dl>
        ${(D.problems || []).length ? `<p class="small" style="color:var(--warn)">Notes from this build: ${D.problems.map(esc).join("; ")}</p>` : ""}</section>
      <section class="card"><h2 style="margin-top:0">Privacy</h2><p class="small">This app has no accounts and sends nothing about you anywhere. Your teams and settings are stored only in this browser. Team logos come from the NHL's image server, which sees each request as any website would; choose Badges only above to turn that off. <button class="btn small warn" data-act="reset">Clear my settings</button></p></section>`;
  }

  // ---------- help ----------
  function vHelp() {
    const gloss = (window.NDA_GLOSSARY || []).map(([t, d]) => `<dt>${esc(t)}</dt><dd>${esc(d)}</dd>`).join("");
    const tipNote = `<p class="small">Hover over, tap, or Tab to any colored tag, summary tile, column heading or dotted-underlined abbreviation to see what it means. Click or tap a player's name to open his official NHL.com page with his full stats in a separate window.</p>`;
    return `${head("help", "How it works")}
      <section class="card"><p>Twice a day (at 9:00 AM and 4:45 PM US Central time) a free GitHub workflow reads the NHL's public schedule, standings and game logs, MoneyPuck's season numbers, Daily Faceoff's starting goalies, line combinations and injured lists, and ESPN's public injury report. It scores every team on five pillars (form, missing players, playoff-race motivation, the starting goalie and rest), turns the gap between the two teams into a win probability, and publishes one data file that this app reads.</p>
      <p>The line under each page title tells you when the next update is due. GitHub starts its own scheduled runs late or now and then not at all, so a small timer outside GitHub starts each update, with GitHub's schedule as a backup; a backup run builds only when the update has not posted yet. While an update is due, an open copy of the app checks for the new file every ${CHECK_MIN.due} minutes and loads it on its own; the Check for newer data button checks right away.</p>
      <p>Each morning it also settles the previous night's games, keeps the season record, and every 10 settled games tests whether a refit of the model would predict better. A refit is adopted only after it proves itself on games it has not seen.</p></section>
      <section class="card"><h2 style="margin-top:0">Limits</h2><ul class="steps-list"><li>Hockey is close to a coin flip: last season the model picked 56 percent of winners on games it had not seen, and about 64 percent on picks that cleared the gate. Treat the numbers as estimates.</li>
      <li>Morning starting goalies are mostly projections; check the evening update or the team's own news before relying on one.</li>
      <li>Line combinations change at the morning skate. When Daily Faceoff cannot be reached, the app rebuilds lines from the last game's ice time.</li>
      <li>Not affiliated with the NHL, ESPN, MoneyPuck, Daily Faceoff or Hockey-Reference. Nothing here is betting advice.</li></ul></section>
      <section class="card"><h2 style="margin-top:0">Glossary</h2>${tipNote}<dl class="gloss">${gloss}</dl></section>`;
  }

  // ---------- data refresh ----------
  async function refresh(manual) {
    if (!location.protocol.startsWith("http")) { if (manual) toast("Open the live app to check for newer data"); return; }
    try {
      const r = await fetch("data/latest.json?ts=" + Date.now(), { cache: "no-cache" });
      if (!r.ok) throw new Error(r.status);
      const n = await r.json();
      if (n && n.format === "nhl-daily-analytics/1" && (!D || n.generated_utc > D.generated_utc)) { D = n; render(); toast("Updated to the latest data"); }
      else if (manual) { const s = updState(); toast(s.kind === "fresh" ? `You have the latest data. Next scheduled update about ${runAt(s.next)}.` : `No new data yet: the ${runAt(s.last)} update has not posted.`); }
    } catch (e) { if (manual) toast("Could not reach the data file; showing the saved copy"); }
  }
  // keep the "next update" lines current, and while an update is due, look for it
  const TICK_MS = 20000;
  let lastAuto = Date.now();
  function tick() {
    const s = updState(), txt = updText(s);
    document.querySelectorAll("[data-upd]").forEach(el => {
      if (el.dataset.txt === txt || (tipEl && el.contains(tipEl))) return;   // leave an open definition alone
      el.className = "upd " + s.kind;
      el.textContent = txt;
      el.dataset.txt = txt;
      wrapText(el);
    });
    const every = CHECK_MIN[s.kind];
    // half a tick of slack, so a check is not put off by a 20-second tick for a few milliseconds
    if (every && document.visibilityState === "visible" && Date.now() - lastAuto >= every * 60000 - TICK_MS / 2) { lastAuto = Date.now(); refresh(false); }
  }

  // ---------- start ----------
  $("#ver").textContent = APP_VERSION;
  window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); installEvt = e; showInstall(); if (st.tab === "settings") render(); });
  window.addEventListener("appinstalled", () => { installed(); toast("Installed. Open NHL Daily from your home screen or app list."); });
  { const d = $("#getapp"); if (d) d.addEventListener("click", e => { if (e.target === d) closeSheet(); }); }   // a click on the dimmed page closes the steps
  if ("serviceWorker" in navigator && location.protocol.startsWith("http")) navigator.serviceWorker.register("sw.js").catch(() => {});
  setTab(st.tab);
  decorate(document.querySelector(".brand"));
  decorate(document.querySelector(".foot-in"));
  showInstall();
  refresh(false);
  setInterval(tick, TICK_MS);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { lastAuto = Date.now(); refresh(false); tick(); } });
})();
