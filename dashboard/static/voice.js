"use strict";
// Mission-control voice: turns dashboard snapshots into spoken callouts.
// Clips come from /voice/manifest.json (made once by `python -m voice.generate`), so no network.
// app.js calls Voice.update(snapshot) on every state message. V toggles the voice.
//
// [added] Voice COMMANDS: a small panel (button bottom-right, or the T key) to talk or type to the probe.
// It needs the helper `python -m voice.server`. Spoken callouts pause while the microphone is open, so the
// dashboard does not talk over you (or into the microphone). Your commands then trigger the usual callouts
// by themselves (rate_N, glare_on, flare, ...), because those rules watch the sim, not the panel.

const Voice = (() => {
  const RATE_BUCKETS = [0, 5, 10, 15, 20], ACC_BUCKETS = [5, 10, 20, 30, 60];
  const PRIO = { flare: 3, no_signal: 3, unstable: 2, lost: 2, signal_back: 2, online: 2 };   // default 1
  const STALE_MS = 4000;            // drop queued callouts older than this...
  const STALE_RELOCK_MS = 10000;    // ...except "lock reacquired", which is the point of a flare
  const RATE_SETTLE_MS = 1200;      // knob must rest this long before the rate is read out
  const FLARE_QUIET_MS = 4000;      // after a flare, "lost" is implied: don't say it
  const HOLD_MS = { LOCK: 700, LOST: 1000 };   // a new state must last this long to be announced
  const FLAP_WINDOW_MS = 15000, FLAP_N = 4, FLAP_MUTE_MS = 12000, UNSTABLE_COOLDOWN_MS = 30000;
  const ACC_DELAY_MS = 3000, ACC_COOLDOWN_MS = 30000, SEARCH_COOLDOWN_MS = 15000;

  let clips = null, base = "/voice/";
  let enabled = true, blocked = false;   // blocked = browser refused autoplay until a gesture
  let micHold = false;                   // [added] true while the command microphone is open: callouts wait
  const audio = {};                      // id -> HTMLAudioElement (preloaded)
  let queue = [], playing = null;
  const history = [];                    // [time s, id] of every line played (debugging / tests)
  const v = { init: false, state: null, everLock: false, outageAt: null, flareAt: -1e9, prevFlash: 0,
              relockFrom: null, sawUnlock: false,   // flare -> next lock: always announced
              flaps: [], muteLockUntil: 0, lastUnstable: -1e9, cand: null, candAt: 0, lastSearch: -1e9, accDue: null, lastAcc: -1e9,
              rateBucket: null, rateSeen: null, rateAt: 0, glare: null,
              slewing: false, lastSlew: -1e9, rateQuietUntil: 0,
              saidVerify: false, stagePrev: "", lastDetumble: -1e9,
              navActive: false, navHeld: false, navFix: false };

  try { enabled = localStorage.getItem("voice.enabled") !== "0"; } catch (e) {}

  // ---------------------------------------------------------------- playback
  function caption(text) {
    const c = document.getElementById("caption");
    if (!c) return;
    c.textContent = text;
    c.classList.add("show");
    clearTimeout(caption.t);
    caption.t = setTimeout(() => c.classList.remove("show"), 3200);
  }
  function say(id) {
    if (!clips || !clips[id] || !enabled) return;
    const now = performance.now(), prio = PRIO[id] || (isRelock(id) ? 2 : 1);
    queue = queue.filter((q) => q.id !== id);
    if (playing && prio > playing.prio && playing.prio < 3) {   // urgent: cut the current line short
      playing.el.pause(); playing = null;
    }
    queue.push({ id, prio, t: now });
    queue.sort((a, b) => b.prio - a.prio || a.t - b.t);
    if (queue.length > 3) queue.length = 3;
    pump();
  }
  const isRelock = (id) => id.startsWith("relock_") || id === "lock_again";
  function pump() {
    if (playing || blocked || micHold) return;
    const now = performance.now();
    queue = queue.filter((q) => now - q.t < (isRelock(q.id) ? STALE_RELOCK_MS : STALE_MS));
    const next = queue.shift();
    if (!next) return;
    const el = audio[next.id];
    el.currentTime = 0;
    playing = { ...next, el };
    caption(clips[next.id].text);
    history.push([+(performance.now() / 1000).toFixed(1), next.id]);
    if (history.length > 200) history.shift();
    el.play().catch((err) => {
      playing = null;
      if (err && err.name === "NotAllowedError") { blocked = true; queue.unshift(next); updChip(); }
      else pump();
    });
  }
  function ended(e) { if (playing && playing.el === e.target) { playing = null; pump(); } }

  // [added] stop talking while the command microphone is open, carry on afterwards
  function holdForMic(on) {
    if (micHold === on) return;
    micHold = on;
    if (on && playing) { playing.el.pause(); playing = null; }
    if (!on) pump();
  }

  // ---------------------------------------------------------------- UI
  function updChip() {
    const c = document.getElementById("voiceChip");
    if (!c) return;
    if (!clips) { c.textContent = "VOICE: NO CLIPS"; c.className = "chip voice off"; c.title = "run: python -m voice.generate"; return; }
    if (!enabled) { c.textContent = "🔇 VOICE OFF"; c.className = "chip voice off"; }
    else if (blocked) { c.textContent = "🔈 CLICK TO ENABLE VOICE"; c.className = "chip voice blocked"; }
    else { c.textContent = "🔊 VOICE"; c.className = "chip voice on"; }
    c.title = (clips && Object.values(clips)[0].engine === "say" ? "placeholder clips (macOS say) · " : "") + "V to toggle";
  }
  function toggle() {
    enabled = !enabled;
    try { localStorage.setItem("voice.enabled", enabled ? "1" : "0"); } catch (e) {}
    if (!enabled) { queue = []; if (playing) { playing.el.pause(); playing = null; } }
    else { unblock(); say("online"); }
    updChip();
  }
  let unblockedAt = -1e9;
  function unblock() { if (blocked) { blocked = false; unblockedAt = performance.now(); updChip(); pump(); } }

  // ---------------------------------------------------------------- rules
  function bucket(list, x) { return list.reduce((b, c) => (Math.abs(c - x) < Math.abs(b - x) ? c : b)); }

  function update(S) {
    if (!S) return;
    Cmd.update(S);   // [added] lets the command panel confirm that a command took effect
    const now = performance.now(), st = S.state, tr = S.truth;
    if (!v.init) {   // first snapshot: learn the situation silently
      v.init = true; v.state = v.cand = st; v.everLock = st === "LOCK";
      if (st !== "LOCK") v.outageAt = now;
      if (tr) { v.rateBucket = bucket(RATE_BUCKETS, tr.rate_dps || 0); v.rateSeen = tr.rate_dps; v.glare = !!tr.glare; v.prevFlash = tr.flash || 0; }
      v.stagePrev = (S.est && S.est.stage) || ""; v.saidVerify = v.stagePrev.startsWith("VERIFYING");
      v.navFix = !!(S.nav && S.nav.est_au); v.navActive = !!(S.nav && S.nav.scan && S.nav.scan.active && S.nav.scan.mode === "button");
      return;
    }

    // solar flare: the sensor whiteout starts
    const flash = tr ? tr.flash || 0 : 0;
    if (flash > 0 && v.prevFlash === 0) {
      v.flareAt = now; v.flaps = []; v.relockFrom = now; v.sawUnlock = st !== "LOCK"; say("flare");
    }
    v.prevFlash = flash;

    // lock state changes, debounced: a state counts once it has held for HOLD_MS, so one-frame
    // drop-outs at high tumble stay quiet; times are taken from when the new state started
    if (st !== v.cand) { v.cand = st; v.candAt = now; }
    if (v.cand !== v.state && now - v.candAt >= (HOLD_MS[v.cand] || 0)) {
      const prev = v.state, at = v.candAt;
      v.state = v.cand;
      if (prev === "NO SIGNAL") say("signal_back");
      if ((v.state === "LOCK" || prev === "LOCK") && now - v.flareAt > FLARE_QUIET_MS) {
        v.flaps.push(at);
        v.flaps = v.flaps.filter((t) => now - t < FLAP_WINDOW_MS);
        if (v.flaps.length >= FLAP_N && now - v.lastUnstable > UNSTABLE_COOLDOWN_MS) {
          say("unstable"); v.lastUnstable = now; v.muteLockUntil = now + FLAP_MUTE_MS; v.flaps = [];
        }
      }
      const muted = now < v.muteLockUntil;
      if (v.state === "LOCK") {
        if (!muted && v.relockFrom == null) {   // after a flare, the block below announces it
          const dt = v.outageAt == null ? null : (at - v.outageAt) / 1000;
          if (!v.everLock) say("lock_first");
          else if (dt != null && dt <= 10.5) say("relock_" + Math.max(1, Math.round(dt)));
          else say("lock_again");
        }
        v.everLock = true; v.outageAt = null; v.accDue = now + ACC_DELAY_MS;
      } else {
        if (prev === "LOCK") v.outageAt = at;
        v.accDue = null;
        if (v.state === "LOST" && prev === "LOCK" && !muted && now - v.flareAt > FLARE_QUIET_MS) say("lost");
        else if (v.state === "SEARCH" && now - v.lastSearch > SEARCH_COOLDOWN_MS && now - v.flareAt > FLARE_QUIET_MS) { say("search"); v.lastSearch = now; }
        else if (v.state === "NO SIGNAL") say("no_signal");
      }
    }

    // after a flare: announce the next solid lock, timed from the flare, even if the outage was
    // shorter than the LOST debounce or lock lines are muted
    if (v.relockFrom != null) {
      if (st !== "LOCK") v.sawUnlock = true;
      else if (v.sawUnlock && v.cand === "LOCK" && now - v.candAt >= HOLD_MS.LOCK) {
        const dt = (v.candAt - v.relockFrom) / 1000;
        say(dt <= 10.5 ? "relock_" + Math.max(1, Math.round(dt)) : "lock_again");
        v.relockFrom = null; v.everLock = true; v.outageAt = null; v.accDue = now + ACC_DELAY_MS;
      }
      if (v.relockFrom != null && now - v.relockFrom > 30000) v.relockFrom = null;   // never re-locked
    }

    // recovery maneuver from camera.solve_live: a search slew is announced ("Searching the sky.");
    // its rate burst is not read out as tumble. A detumble ends in "Spacecraft stabilized." via the rate rule.
    const stage = (S.est && S.est.stage) || "";
    const slewing = stage === "SEARCH SLEW";
    if (slewing && !v.slewing && now - v.lastSlew > SEARCH_COOLDOWN_MS && now - v.flareAt > FLARE_QUIET_MS) {
      say("search"); v.lastSlew = now; v.lastSearch = now;
    }
    v.slewing = slewing;
    if (slewing) v.rateQuietUntil = now + RATE_SETTLE_MS + 500;

    // acquisition stages: the first candidate of each outage ("Star pattern found. Verifying.") and the
    // start of a detumble maneuver
    if (st === "LOCK") v.saidVerify = false;
    else if (stage.startsWith("VERIFYING") && !v.saidVerify) { say("verifying"); v.saidVerify = true; }
    if (stage.startsWith("DETUMBLING") && !v.stagePrev.startsWith("DETUMBLING") && now - v.lastDetumble > SEARCH_COOLDOWN_MS) {
      say("detumble"); v.lastDetumble = now;
    }
    v.stagePrev = stage;

    // WHERE AM I? (solve_live --nav-scan-mode button): sweep starts, or waits for the spin to stop; first fix
    const nav = S.nav, sc = nav && nav.scan;
    const navActive = !!(sc && sc.active && sc.mode === "button"), held = !!(sc && sc.paused);
    if (navActive && !v.navActive) { say(held ? "nav_hold" : "nav_scan"); v.navHeld = held; }
    else if (navActive && v.navHeld && !held) { say("nav_scan"); v.navHeld = false; }
    v.navActive = navActive;
    const navFix = !!(nav && nav.est_au);
    if (navFix && !v.navFix) say("nav_fix");
    v.navFix = navFix;

    // how good the lock is, once it has settled
    if (v.accDue && now >= v.accDue && st === "LOCK") {
      v.accDue = null;
      const e = S.err && S.err.point;
      const b = e == null ? null : ACC_BUCKETS.find((x) => e < x);
      if (b && now - v.lastAcc > ACC_COOLDOWN_MS && !queue.length && !playing) { say("acc_" + b); v.lastAcc = now; }
    }

    if (tr) {
      // tumble rate: read out once the knob settles in a new 5 deg/s band
      const r = tr.rate_dps || 0;
      if (r !== v.rateSeen) { v.rateSeen = r; v.rateAt = now; }
      const b = bucket(RATE_BUCKETS, r);
      if (b !== v.rateBucket && now - v.rateAt > RATE_SETTLE_MS && now >= v.rateQuietUntil) { v.rateBucket = b; say("rate_" + b); }
      if (!!tr.glare !== v.glare) { v.glare = !!tr.glare; say(v.glare ? "glare_on" : "glare_off"); }
    }
  }

  // ================================================================ voice COMMANDS (input) [added]
  // Talk or type to the probe. Speech -> text happens in the helper (python -m voice.server, which keeps the
  // ElevenLabs key); only a short list of whitelisted sim commands can come out of it. The panel injects
  // its own button, CSS and DOM, and uses the live snapshots to CONFIRM each command really changed the sim.
  const Cmd = (() => {
    const API = window.VOICE_API || `${location.protocol}//${location.hostname}:5010`;
    const REC_MS = 6000, SERVER_REC_S = 4, CONFIRM_MS = 4000, MAX_LOG = 3;
    const EXAMPLES = ["stop tumbling", "faster", "slow down", "tumble about the roll axis", "solar flare", "glare on"];
    const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
    const truthOf = (S) => (S && S.truth) || null;

    // Returns a function(S) -> true once a snapshot shows the command took effect, or null when the effect
    // cannot be seen from the dashboard (axis, flare, boost, mag) or is ambiguous right now.
    function expectFor(cmd, S) {
      const [name, ...a] = cmd.trim().split(/\s+/);
      const n = parseFloat(a[0]), tr = truthOf(S);
      if (!tr) return null;
      const base = tr.rate_dps != null ? tr.rate_dps : 0;
      const t = (f) => (s) => { const x = truthOf(s); return !!x && f(x); };
      switch (name) {
        case "rate": {
          if (tr.mode === "paused" || isNaN(n)) return null;       // a paused sim reports 0 deg/s
          const want = clamp(n, 0, 60); return t((x) => Math.abs(x.rate_dps - want) < 0.6);
        }
        case "rate_add": {
          if (tr.mode === "paused" || isNaN(n)) return null;
          const want = clamp(base + n, 0, 60); return t((x) => Math.abs(x.rate_dps - want) < 0.6);
        }
        case "glare": { const want = a.length ? a[0] === "1" : !tr.glare; return t((x) => !!x.glare === want); }
        case "calib": { const want = a.length ? a[0] === "1" : tr.mode !== "calib"; return t((x) => (x.mode === "calib") === want); }
        case "pause": {
          if (tr.mode === "calib") return null;                     // calibration hides the paused state
          const want = a.length ? a[0] === "1" : tr.mode !== "paused"; return t((x) => (x.mode === "paused") === want);
        }
        default: return null;
      }
    }

    let lastS = null, status = null, micMode = "none", state = "idle";   // state: idle | listening | thinking
    let log = [], rec = null, recTimer = null, tick = null;
    const ui = {};

    function update(S) {
      lastS = S;
      const now = performance.now();
      let dirty = false;
      for (const e of log) for (const c of e.checks) {
        if (c.status !== "pending") continue;
        if (c.fn(S)) { c.status = "ok"; dirty = true; }
        else if (now - e.t0 > CONFIRM_MS) { c.status = "warn"; dirty = true; }
      }
      if (dirty) renderLog();
      renderLive(S);
    }

    const MIC = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';
    const CSS = `
    #voicePanel{position:fixed;right:14px;bottom:14px;z-index:60;display:flex;flex-direction:column;align-items:flex-end;gap:8px;
      font:500 13px -apple-system,Helvetica,Arial,sans-serif;color:var(--text,#dbe3f4)}
    #voicePanel button{font:inherit;color:inherit;cursor:pointer}
    #vpFab{display:flex;align-items:center;gap:8px;padding:8px 14px;border-radius:22px;border:1px solid var(--edge,#27304a);
      background:rgba(10,14,24,.92);font-weight:700;font-size:12px;letter-spacing:.1em}
    #vpFab.on{border-color:var(--lock,#3ce67a)}
    #vpBox{width:330px;max-height:80vh;overflow:auto;padding:12px;border-radius:12px;border:1px solid var(--edge,#27304a);
      background:rgba(10,14,24,.96);box-shadow:0 8px 30px rgba(0,0,0,.5)}
    #vpBox[hidden]{display:none}
    .vp-head{display:flex;align-items:center;gap:8px;margin-bottom:8px}
    .vp-head b{letter-spacing:.1em;font-size:12px}
    .vp-head .sp{flex:1}
    .vp-dot{width:9px;height:9px;border-radius:50%;background:var(--dim,#6f7c96)}
    .vp-dot.ok{background:var(--lock,#3ce67a)} .vp-dot.bad{background:var(--lost,#ff5a4e)}
    .vp-x{border:0;background:none;font-size:20px;line-height:1;color:var(--dim,#6f7c96)}
    .vp-live{padding:6px 8px;border-radius:8px;background:rgba(255,255,255,.04);font:600 12px SF Mono,Menlo,monospace;color:var(--dim,#6f7c96);margin-bottom:10px}
    .vp-row{display:flex;align-items:center;gap:12px;margin-bottom:10px}
    .vp-mic{flex:none;width:54px;height:54px;border-radius:50%;border:2px solid var(--lock,#3ce67a);background:rgba(60,230,122,.08);
      display:flex;align-items:center;justify-content:center;color:var(--lock,#3ce67a)}
    .vp-mic:disabled{opacity:.35;cursor:not-allowed}
    .vp-mic.listening{border-color:var(--lost,#ff5a4e);color:var(--lost,#ff5a4e);background:rgba(255,90,78,.12);animation:vpPulse 1s infinite}
    .vp-mic.thinking{border-color:var(--search,#ffb83c);color:var(--search,#ffb83c);animation:vpSpin 1.2s linear infinite;border-top-color:transparent}
    @keyframes vpPulse{0%{box-shadow:0 0 0 0 rgba(255,90,78,.5)}100%{box-shadow:0 0 0 14px rgba(255,90,78,0)}}
    @keyframes vpSpin{to{transform:rotate(360deg)}}
    .vp-hint{flex:1;line-height:1.35;color:var(--dim,#6f7c96)}
    .vp-hint.err{color:var(--lost,#ff5a4e)}
    .vp-type{display:flex;gap:6px;margin-bottom:10px}
    .vp-type input{flex:1;min-width:0;padding:7px 9px;border-radius:8px;border:1px solid var(--edge,#27304a);background:rgba(0,0,0,.35);color:inherit;font:inherit}
    .vp-type button{padding:7px 11px;border-radius:8px;border:1px solid var(--edge,#27304a);background:rgba(255,255,255,.06);font-weight:700;font-size:11px;letter-spacing:.06em}
    .vp-entry{padding:8px 0;border-top:1px solid var(--edge,#27304a)}
    .vp-heard{font-weight:600;margin-bottom:5px}
    .vp-chips{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:4px}
    .vp-chip{padding:2px 7px;border-radius:6px;background:rgba(255,255,255,.07);font:600 11px SF Mono,Menlo,monospace}
    .vp-chip.ok{background:rgba(60,230,122,.16);color:var(--lock,#3ce67a)}
    .vp-chip.warn{background:rgba(255,184,60,.16);color:var(--search,#ffb83c)}
    .vp-chip.pending{color:var(--dim,#6f7c96)}
    .vp-reply{color:var(--dim,#6f7c96);font-size:12px}
    .vp-entry.bad .vp-heard{color:var(--lost,#ff5a4e)}
    .vp-ex{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
    .vp-ex button{white-space:nowrap;padding:3px 8px;border-radius:12px;border:1px solid var(--edge,#27304a);background:none;font-size:11px;color:var(--dim,#6f7c96)}
    .vp-ex button:hover{color:var(--text,#dbe3f4);border-color:var(--dim,#6f7c96)}
    .vp-foot{margin-top:10px;font-size:11px;color:var(--dim,#6f7c96);display:flex;gap:10px;flex-wrap:wrap;align-items:center}
    .vp-foot a{color:var(--con,#78a5ff);cursor:pointer;text-decoration:underline}`;

    const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const browserMicOk = () => !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);

    function build() {
      const st = document.createElement("style"); st.textContent = CSS; document.head.appendChild(st);
      const el = document.createElement("div"); el.id = "voicePanel";
      el.innerHTML = `
        <div id="vpBox" hidden>
          <div class="vp-head"><b>VOICE COMMAND</b><span class="vp-dot" id="vpDot"></span><span id="vpState" style="font-size:11px;color:var(--dim,#6f7c96)">checking…</span><span class="sp"></span><button class="vp-x" id="vpClose" title="Close (Esc)">×</button></div>
          <div class="vp-live" id="vpLive">sim: waiting for data</div>
          <div class="vp-row"><button class="vp-mic" id="vpMic" title="Click to talk (T)">${MIC}</button><div class="vp-hint" id="vpHint">Click the microphone and speak a command.</div></div>
          <div class="vp-type"><input id="vpText" placeholder="or type: stop tumbling" autocomplete="off" spellcheck="false"><button id="vpSend">SEND</button></div>
          <div id="vpLog"></div>
          <div class="vp-ex" id="vpEx"></div>
          <div class="vp-foot" id="vpFoot"></div>
        </div>
        <button id="vpFab" title="Talk to the probe (T)">${MIC.replace('width="22" height="22"', 'width="16" height="16"')}<span>COMMAND</span></button>`;
      document.body.appendChild(el);
      for (const id of ["vpBox", "vpDot", "vpState", "vpClose", "vpLive", "vpMic", "vpHint", "vpText", "vpSend", "vpLog", "vpEx", "vpFoot", "vpFab"])
        ui[id] = document.getElementById(id);
      ui.vpFab.onclick = () => toggle();
      ui.vpClose.onclick = () => toggle(false);
      ui.vpMic.onclick = micClick;
      ui.vpSend.onclick = sendTyped;
      // typing must never reach the page's own shortcuts (V would toggle the spoken callouts)
      ui.vpText.addEventListener("keydown", (e) => { if (e.key === "Enter") sendTyped(); e.stopPropagation(); });
      ui.vpEx.innerHTML = EXAMPLES.map((x) => `<button>${esc(x)}</button>`).join("");
      ui.vpEx.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => sendText(b.textContent)));
      document.addEventListener("keydown", (e) => {
        const tag = (e.target && e.target.tagName) || "";
        if (/INPUT|TEXTAREA|SELECT/.test(tag) || e.metaKey || e.ctrlKey || e.altKey) return;
        if (e.key === "t" || e.key === "T") { toggle(true); micClick(); }
        else if (e.key === "Escape" && !ui.vpBox.hidden) toggle(false);
      });
      renderFoot(); setState("idle");
      setTimeout(checkStatus, 300);
    }

    function toggle(open) {
      const show = open === undefined ? ui.vpBox.hidden : open;
      ui.vpBox.hidden = !show; ui.vpFab.classList.toggle("on", show);
      if (show) { checkStatus(); renderLog(); }
    }
    function hint(msg, bad) { ui.vpHint.textContent = msg; ui.vpHint.className = "vp-hint" + (bad ? " err" : ""); }

    function setState(s, msg) {
      state = s;
      holdForMic(s === "listening");   // the dashboard stops talking while the microphone is open
      ui.vpMic.className = "vp-mic " + (s === "idle" ? "" : s);
      ui.vpMic.disabled = micMode === "none" && s === "idle";
      if (msg) hint(msg);
      else if (s === "idle") hint(micMode === "none" ? "No microphone available here: type a command instead."
        : "Click the microphone and speak a command.");
    }

    function renderLive(S) {
      if (!ui.vpLive || ui.vpBox.hidden) return;
      const tr = S && S.truth;
      const txt = tr ? `sim: tumble ${tr.rate_dps != null ? tr.rate_dps.toFixed(1) : "—"} °/s · glare ${tr.glare ? "ON" : "off"} · ${String(tr.mode || "").toUpperCase()}`
                     : "sim: no truth signal";
      if (ui.vpLive.textContent !== txt) ui.vpLive.textContent = txt;
    }

    function renderFoot() {
      const bits = [];
      if (status) {
        bits.push(`speech: ${status.stt ? "ElevenLabs ✓" : "key missing on server"}`);
        if (status.dry_run) bits.push("DRY RUN");
      }
      ui.vpFoot.innerHTML = bits.map(esc).join(" · ") +
        (browserMicOk() && status && status.server_mic ? ` · <a id="vpSwap">mic: ${micMode === "browser" ? "this device" : "probe computer"}</a>` : "");
      const sw = document.getElementById("vpSwap");
      if (sw) sw.onclick = () => { micMode = micMode === "browser" ? "server" : "browser"; renderFoot(); setState(state); };
    }

    function renderLog() {
      if (!ui.vpLog) return;
      ui.vpLog.innerHTML = log.slice(0, MAX_LOG).map((e) => {
        const chips = e.checks.map((c) => {
          const mark = c.status === "ok" ? " ✓" : c.status === "warn" ? " ?" : c.status === "pending" ? " …" : "";
          const tip = c.status === "warn" ? "sent, but no change seen on the dashboard yet" : c.status === "ok" ? "confirmed by the sim"
            : c.status === "sent" ? "sent (this one cannot be seen from the dashboard)" : "waiting for the sim";
          return `<span class="vp-chip ${c.status === "sent" ? "" : c.status}" title="${tip}">${esc(c.cmd)}${mark}</span>`;
        }).join("");
        return `<div class="vp-entry ${e.ok ? "" : "bad"}"><div class="vp-heard">${e.heard ? "“" + esc(e.heard) + "”" : "—"}</div>` +
          (chips ? `<div class="vp-chips">${chips}</div>` : "") +
          `<div class="vp-reply">${esc(e.ok ? e.reply : e.error || "failed")}</div></div>`;
      }).join("");
    }

    // ---------------------------------------------------------------- talking to the helper
    async function fetchJSON(path, opts = {}, ms = 25000) {
      const ctl = new AbortController(), to = setTimeout(() => ctl.abort(), ms);
      try {
        const r = await fetch(API + path, { ...opts, signal: ctl.signal });
        return await r.json();
      } catch (e) {
        return { ok: false, heard: "", commands: [], reply: "", error: "Voice helper not reachable. Start it with: python -m voice.server" };
      } finally { clearTimeout(to); }
    }

    async function checkStatus() {
      const r = await fetch(API + "/voice/status").then((x) => x.json()).catch(() => null);
      status = r;
      ui.vpDot.className = "vp-dot " + (r ? "ok" : "bad");
      ui.vpState.textContent = r ? "helper connected" : "helper offline";
      if (r) micMode = browserMicOk() ? "browser" : r.server_mic ? "server" : "none";
      else micMode = browserMicOk() ? "browser" : "none";
      renderFoot();
      if (state === "idle") setState("idle");
      if (!r) hint("Voice helper not reachable. Start it with: python -m voice.server", true);
    }

    function handleResult(res) {
      const e = { t0: performance.now(), heard: res.heard || "", ok: !!res.ok, reply: res.reply || "", error: res.error,
                  checks: (res.commands || []).map((cmd) => {
                    const fn = res.sent ? expectFor(cmd, lastS) : null;
                    return { cmd, fn, status: fn ? "pending" : "sent" };
                  }) };
      log.unshift(e); log = log.slice(0, 12);
      renderLog();
      setState("idle");
      if (!res.ok) hint(res.error || "That did not work.", true);
      caption(res.ok ? res.reply : res.error || "");   // the dashboard's own caption line
    }

    async function sendText(text) {
      text = (text || "").trim();
      if (!text) return;
      setState("thinking", "Sending…");
      handleResult(await fetchJSON("/voice/text", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) }));
    }
    function sendTyped() { const x = ui.vpText.value; ui.vpText.value = ""; sendText(x); }

    // ---------------------------------------------------------------- microphone
    function micClick() {
      if (state === "listening") return stopRec();
      if (state === "thinking") return;
      if (micMode === "browser") return startBrowserRec();
      if (micMode === "server") return serverTalk();
      setState("idle");
    }

    async function serverTalk() {
      let left = SERVER_REC_S;
      setState("listening", `Listening on the probe computer… ${left}s`);
      tick = setInterval(() => { left -= 1; if (left > 0) hint(`Listening on the probe computer… ${left}s`); else { clearInterval(tick); setState("thinking", "Transcribing…"); } }, 1000);
      const res = await fetchJSON("/voice/talk", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ seconds: SERVER_REC_S }) }, 40000);
      clearInterval(tick);
      handleResult(res);
    }

    async function startBrowserRec() {
      let stream;
      try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
      catch (e) {
        if (status && status.server_mic) { micMode = "server"; renderFoot(); return serverTalk(); }
        return hint("Microphone blocked: allow it in the browser, or type a command.", true);
      }
      const chunks = [];
      rec = new MediaRecorder(stream);
      rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      rec.onstop = async () => {
        clearTimeout(recTimer); stream.getTracks().forEach((t) => t.stop());
        setState("thinking", "Transcribing…");
        handleResult(await sendAudio(new Blob(chunks, { type: rec.mimeType || "audio/webm" })));
      };
      rec.start();
      setState("listening", "Listening… click again when you are done.");
      recTimer = setTimeout(stopRec, REC_MS);
    }
    function stopRec() { if (rec && rec.state !== "inactive") rec.stop(); }

    // 16 kHz mono WAV is what the speech model handles best, and is the same format the probe-side mic uses
    async function toWav16k(blob) {
      const ac = new (window.AudioContext || window.webkitAudioContext)();
      const buf = await ac.decodeAudioData(await blob.arrayBuffer()); ac.close();
      const off = new OfflineAudioContext(1, Math.max(1, Math.ceil(buf.duration * 16000)), 16000);
      const src = off.createBufferSource(); src.buffer = buf; src.connect(off.destination); src.start();
      const pcm = (await off.startRendering()).getChannelData(0);
      const out = new DataView(new ArrayBuffer(44 + pcm.length * 2));
      const w = (o, s) => { for (let i = 0; i < s.length; i++) out.setUint8(o + i, s.charCodeAt(i)); };
      w(0, "RIFF"); out.setUint32(4, 36 + pcm.length * 2, true); w(8, "WAVEfmt "); out.setUint32(16, 16, true);
      out.setUint16(20, 1, true); out.setUint16(22, 1, true); out.setUint32(24, 16000, true); out.setUint32(28, 32000, true);
      out.setUint16(32, 2, true); out.setUint16(34, 16, true); w(36, "data"); out.setUint32(40, pcm.length * 2, true);
      for (let i = 0; i < pcm.length; i++) out.setInt16(44 + i * 2, clamp(pcm[i], -1, 1) * 0x7fff, true);
      return new Blob([out], { type: "audio/wav" });
    }
    async function sendAudio(blob) {
      let body = blob;
      try { body = await toWav16k(blob); } catch (e) { /* send the recording as it is */ }
      return fetchJSON("/voice/audio", { method: "POST", headers: { "Content-Type": body.type || "application/octet-stream" }, body }, 40000);
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build);
    else build();

    return { update, toggle, send: sendText, _expectFor: expectFor };
  })();

  // ---------------------------------------------------------------- setup
  function load() {
    fetch(base + "manifest.json").then((r) => (r.ok ? r.json() : null)).then((m) => {
      if (!m || !m.clips) { updChip(); return; }
      useClips(m.clips);
    }).catch(() => updChip());
  }
  function useClips(c) {
    clips = c;
    for (const [id, x] of Object.entries(clips)) {
      const el = new Audio(base + x.file);
      el.preload = "auto";
      el.addEventListener("ended", ended);
      el.addEventListener("error", ended);
      audio[id] = el;
    }
    updChip();
    if (enabled) say("online");
  }
  document.addEventListener("keydown", (e) => {
    const tag = (e.target && e.target.tagName) || "";   // [added] typing in the command box must not toggle the voice
    if (/INPUT|TEXTAREA|SELECT/.test(tag)) return;
    if ((e.key === "v" || e.key === "V") && !e.metaKey && !e.ctrlKey) toggle();
  });
  // browsers only allow sound after a gesture: any click or key on the page unlocks it
  for (const ev of ["pointerdown", "keydown"]) document.addEventListener(ev, unblock, { capture: true });
  document.addEventListener("DOMContentLoaded", () => {
    const c = document.getElementById("voiceChip");
    if (c) c.addEventListener("click", () => { if (performance.now() - unblockedAt > 500) toggle(); });
    updChip();
  });
  load();

  return { update, say, toggle, history, _test_load: useClips, cmd: Cmd };
})();