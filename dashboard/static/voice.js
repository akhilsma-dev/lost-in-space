"use strict";
// Mission-control voice: turns dashboard snapshots into spoken callouts.
// Clips come from /voice/manifest.json (made once by `python -m voice.generate`), so no network.
// app.js calls Voice.update(snapshot) on every state message. V toggles the voice.

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
  const audio = {};                      // id -> HTMLAudioElement (preloaded)
  let queue = [], playing = null;
  const history = [];                    // [time s, id] of every line played (debugging / tests)
  const v = { init: false, state: null, everLock: false, outageAt: null, flareAt: -1e9, prevFlash: 0,
              relockFrom: null, sawUnlock: false,   // flare -> next lock: always announced
              flaps: [], muteLockUntil: 0, lastUnstable: -1e9, cand: null, candAt: 0, lastSearch: -1e9, accDue: null, lastAcc: -1e9,
              rateBucket: null, rateSeen: null, rateAt: 0, glare: null,
              slewing: false, lastSlew: -1e9, rateQuietUntil: 0 };

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
    if (playing || blocked) return;
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
    const now = performance.now(), st = S.state, tr = S.truth;
    if (!v.init) {   // first snapshot: learn the situation silently
      v.init = true; v.state = v.cand = st; v.everLock = st === "LOCK";
      if (st !== "LOCK") v.outageAt = now;
      if (tr) { v.rateBucket = bucket(RATE_BUCKETS, tr.rate_dps || 0); v.rateSeen = tr.rate_dps; v.glare = !!tr.glare; v.prevFlash = tr.flash || 0; }
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

  return { update, say, toggle, history, _test_load: useClips };
})();
