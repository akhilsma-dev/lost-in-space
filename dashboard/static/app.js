"use strict";
// Lost in Space mission control: receives /events (SSE, ~15 Hz) and draws everything on canvases.

const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const C = { lock: css("--lock"), lost: css("--lost"), search: css("--search"), con: css("--con"),
            cent: css("--cent"), truth: css("--truth"), dim: css("--dim"), edge: css("--edge"), nosig: css("--nosig") };

let S = null;          // latest snapshot
let lastMsg = 0;       // wall time we last heard from the server
const frame = { img: null, id: -1, loading: false };      // truth re-render of the box screen
const camFrame = { img: null, id: -1, loading: false };   // the frame the solver actually saw

// ---------------------------------------------------------------- data in
function onState(txt) {
  S = JSON.parse(txt); lastMsg = performance.now(); updateText(); maybeLoadFrame();
  if (typeof Voice !== "undefined") Voice.update(S);
}
function connect() {
  if (new URLSearchParams(location.search).has("poll")) {   // plain polling (headless screenshots, picky proxies)
    const tick = () => fetch("/state").then((r) => r.text()).then(onState).catch(() => {}).finally(() => setTimeout(tick, 66));
    return tick();
  }
  const es = new EventSource("/events");
  es.onmessage = (e) => onState(e.data);
  es.onerror = () => { /* EventSource retries by itself */ };
}
function loadImg(slot, url, id) {
  if (id === slot.id || slot.loading) return;
  slot.loading = true;
  const im = new Image();
  im.onload = () => { slot.img = im; slot.id = id; slot.loading = false; };
  im.onerror = () => { slot.loading = false; };
  im.src = url + "?i=" + id;
}
function maybeLoadFrame() {
  if (S.view.has_frame) loadImg(frame, "/frame.jpg", S.view.frame_id);
  if (S.cam) loadImg(camFrame, "/cam.jpg", S.cam.frame_id);
}

// ---------------------------------------------------------------- formatting
const f2 = (x, d = 2) => (x == null ? "—" : x.toFixed(d));
const wrap180 = (a) => ((a + 540) % 360) - 180;
function fmtAngle(arcsec) {
  if (arcsec == null) return "—";
  if (arcsec < 60) return arcsec.toFixed(1) + "″";
  if (arcsec < 3600) return (arcsec / 60).toFixed(1) + "′";
  return (arcsec / 3600).toFixed(2) + "°";
}
function fmtProb(p) { return p == null ? "—" : p.toExponential(0).replace("e", "e"); }
function fmtAgo(s) { return s == null ? "never" : s < 1 ? "now" : s < 60 ? s.toFixed(0) + " s ago" : (s / 60).toFixed(0) + " min ago"; }

// ---------------------------------------------------------------- text panels
function updateText() {
  const s = S;
  $("modeLabel").textContent = s.mode_label;
  const ft = s.feeds.truth, fe = s.feeds.est;
  $("fTruth").className = "feed" + (ft.ok ? " ok" : "");
  $("fTruth").querySelector("b").textContent = ft.error ? "PORT BUSY" : ft.ok ? ft.hz.toFixed(0) + " Hz" : "NO SIGNAL";
  $("fEst").className = "feed" + (fe.ok ? " ok" : "");
  $("fEst").querySelector("b").textContent = fe.error ? "PORT BUSY" : fe.ok ? fe.hz.toFixed(0) + " Hz" + (fe.mock ? " (mock)" : "") : "NO SIGNAL";
  $("clock").textContent = new Date(s.now * 1000).toLocaleTimeString([], { hour12: false });

  const b = $("lockBadge");
  const cls = { "LOCK": "lock", "LOST": "lost", "SEARCH": "search", "NO SIGNAL": "nosig", "CALIB": "calib" }[s.state];
  const stage = stageText(s);
  b.className = "badge " + cls + (stage ? " has-stage" : "");
  b.innerHTML = s.state === "NO SIGNAL" ? "NO<br>SIGNAL" : s.state === "CALIB" ? "CALIB-<br>RATING"
    : stage ? `<span>${s.state}</span><small>${stage}</small>` : s.state;

  const e = s.est, lock = e && e.status === "LOCK";
  const conf = lock ? e.confidence : 0;
  $("confBar").style.width = (conf * 100).toFixed(0) + "%";
  $("conf").textContent = e ? conf.toFixed(2) : "—";
  $("matches").textContent = e ? e.matches : "—";
  $("prob").textContent = lock ? fmtProb(e.prob) : "—";
  $("rmse").textContent = lock ? e.rmse.toFixed(0) + "″" : "—";
  $("solvems").textContent = e && e.solve_ms != null ? e.solve_ms.toFixed(0) + " ms" : "—";
  $("ncent").textContent = e ? e.n_centroids : "—";

  const t = s.paired || s.truth;
  $("tRa").textContent = t ? f2(t.ra, 3) + "°" : "no signal";
  $("tDec").textContent = t ? f2(t.dec, 3) + "°" : "";
  $("tRoll").textContent = t ? f2(t.roll, 2) + "°" : "";
  $("eRa").textContent = lock ? f2(e.ra, 3) + "°" : (e ? "—" : "no signal");
  $("eDec").textContent = lock ? f2(e.dec, 3) + "°" : "—";
  $("eRoll").textContent = lock ? f2(e.roll, 2) + "°" : "—";
  const d = lock && t;
  $("dRa").textContent = d ? fmtAngle(Math.abs(wrap180(e.ra - t.ra)) * 3600 * Math.cos(t.dec * Math.PI / 180)) : "—";
  $("dDec").textContent = d ? fmtAngle(Math.abs(e.dec - t.dec) * 3600) : "—";
  $("dRoll").textContent = d ? fmtAngle(Math.abs(wrap180(e.roll - t.roll)) * 3600) : "—";
  const err = s.err;
  $("errFull").textContent = err ? fmtAngle(err.full) : "—";
  $("errFull").style.color = !err ? C.dim : err.full < 120 ? C.lock : err.full < 3600 ? C.search : C.lost;
  $("errPoint").textContent = err ? fmtAngle(err.point) : "—";
  $("errRoll").textContent = err ? fmtAngle(Math.abs(err.roll) * 3600) : "—";

  const tr = s.truth;
  const rate = tr ? tr.rate_dps : null;
  $("rate").textContent = rate == null ? "—" : rate.toFixed(1) + " °/s";
  $("rateBar").style.width = rate == null ? "0" : Math.min(100, (rate / 30) * 100) + "%";
  $("glare").textContent = tr ? (tr.glare ? "ON" : "OFF") : "—";
  $("glare").className = "pill " + (tr && tr.glare ? "on" : "off");
  $("mode").textContent = tr ? tr.mode.toUpperCase() : "—";
  $("mode").className = "pill " + (tr && tr.mode !== "sky" ? "on" : "off");
  $("sinceLock").textContent = s.state === "LOCK" ? "locked" : fmtAgo(s.since_lock);

  const st = s.stats;
  $("lock30").textContent = st.lock30 == null ? "—" : (st.lock30 * 100).toFixed(0) + "%";
  $("med30").textContent = fmtAngle(st.med_err30);
  $("sess").textContent = st.session_n ? `${st.session_lock}/${st.session_n} locked` : "—";

  updateSolveRate();
  updateNavText();
}

function updateSolveRate() {
  const sr = S.solve_rate, st = S.stats;
  let rows, title, note;
  if (sr && Array.isArray(sr.rows)) {
    rows = sr.rows.map((r, i) => ({ label: r.label, rate: r.rate, n: r.n, col: i === sr.rows.length - 1 ? C.lock : C.search }));
    title = sr.title || "tetra3 alone vs tetra3 + CNN";
    note = sr.note || "";
  } else {
    rows = [
      { label: "LIVE (30 s)", rate: st.lock30, n: st.n30, col: C.con },
      { label: "tetra3 alone", rate: null, col: C.search },
      { label: "tetra3 + CNN", rate: null, col: C.lock },
    ];
    title = "tetra3 alone vs tetra3 + CNN";
    note = "Benchmark pending — drop results in dashboard/solve_rate.json";
  }
  $("srTitle").textContent = title;
  $("srNote").textContent = note;
  const html = rows.map((r) => {
    const pend = r.rate == null;
    return `<div class="srrow${pend ? " pending" : ""}"><span>${r.label}</span>` +
      `<div class="bar"><div style="width:${pend ? 0 : (r.rate * 100).toFixed(1)}%;background:${r.col}"></div></div>` +
      `<span class="num">${pend ? "pending" : (r.rate * 100).toFixed(0) + "%"}</span></div>`;
  }).join("");
  if ($("srRows").innerHTML !== html) $("srRows").innerHTML = html;
}

// ---------------------------------------------------------------- canvas helpers
function fit(canvas) {
  const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(r.width * dpr)), h = Math.max(1, Math.round(r.height * dpr));
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return [ctx, r.width, r.height];
}
function bigText(ctx, W, H, text, sub, col, px = 44) {
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillStyle = col; ctx.font = `800 ${px}px -apple-system, Helvetica, Arial`;
  ctx.fillText(text, W / 2, H / 2 - (sub ? 14 : 0));
  if (sub) { ctx.fillStyle = C.dim; ctx.font = "500 16px -apple-system, Helvetica, Arial"; ctx.fillText(sub, W / 2, H / 2 + 26); }
}

// ---------------------------------------------------------------- camera view
// Draw one sky picture (image + optional overlay) letterboxed into the box (bx, by, bw, bh).
function drawSky(ctx, bx, by, bw, bh, img, v, o, bore, alpha = 0.9, filter = "none") {
  const sc = Math.min(bw / v.w, bh / v.h), dw = v.w * sc, dh = v.h * sc, ox = bx + (bw - dw) / 2, oy = by + (bh - dh) / 2;
  const X = (x) => ox + x * sc, Y = (y) => oy + y * sc;
  ctx.save();
  ctx.beginPath(); ctx.rect(ox, oy, dw, dh); ctx.clip();
  ctx.fillStyle = "#000"; ctx.fillRect(ox, oy, dw, dh);
  if (img) { ctx.globalAlpha = alpha; ctx.filter = filter; ctx.drawImage(img, ox, oy, dw, dh); ctx.filter = "none"; ctx.globalAlpha = 1; }
  if (o) {
    ctx.strokeStyle = C.con; ctx.globalAlpha = 0.75; ctx.lineWidth = 1.6;
    ctx.beginPath();
    for (const [ax, ay, bx2, by2] of o.lines) { ctx.moveTo(X(ax), Y(ay)); ctx.lineTo(X(bx2), Y(by2)); }
    ctx.stroke(); ctx.globalAlpha = 1;
    ctx.font = "700 13px -apple-system, Helvetica, Arial"; ctx.textAlign = "center"; ctx.fillStyle = "rgba(120,165,255,.75)";
    for (const [x, y, n] of o.cons) ctx.fillText(n.toUpperCase(), X(x), Y(y));
  }
  if (v.cents) {   // detected centroids
    ctx.strokeStyle = C.cent; ctx.lineWidth = 1; ctx.globalAlpha = 0.85;
    for (const [x, y] of v.cents) { ctx.beginPath(); ctx.arc(X(x), Y(y), 5, 0, 2 * Math.PI); ctx.stroke(); }
    ctx.globalAlpha = 1;
  }
  if (o) {
    ctx.strokeStyle = C.lock; ctx.lineWidth = 2;
    for (const [x, y] of o.ident) { ctx.beginPath(); ctx.arc(X(x), Y(y), 10, 0, 2 * Math.PI); ctx.stroke(); }
    ctx.font = "600 14px -apple-system, Helvetica, Arial"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
    for (const [x, y, name] of o.labels) {
      ctx.fillStyle = "rgba(0,0,0,.55)";
      const tw = ctx.measureText(name).width;
      ctx.fillRect(X(x) + 13, Y(y) - 15, tw + 8, 18);
      ctx.fillStyle = "#d6ffe4"; ctx.fillText(name, X(x) + 17, Y(y) - 6);
    }
  }
  if (bore) {   // boresight reticle
    ctx.strokeStyle = "rgba(60,230,122,.55)"; ctx.lineWidth = 1;
    const cx = X(bore[0]), cy = Y(bore[1]);
    ctx.beginPath(); ctx.moveTo(cx - 18, cy); ctx.lineTo(cx - 6, cy); ctx.moveTo(cx + 6, cy); ctx.lineTo(cx + 18, cy);
    ctx.moveTo(cx, cy - 18); ctx.lineTo(cx, cy - 6); ctx.moveTo(cx, cy + 6); ctx.lineTo(cx, cy + 18); ctx.stroke();
  }
  ctx.restore();
  ctx.strokeStyle = C.edge; ctx.lineWidth = 1; ctx.strokeRect(ox + 0.5, oy + 0.5, dw - 1, dh - 1);
  return { ox, oy, dw, dh };
}

function drawView() {
  const [ctx, W, H] = fit($("view"));
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, H);
  if (!S) { bigText(ctx, W, H, "CONNECTING…", "dashboard server not reachable", C.nosig); return; }
  const v = S.view, cam = S.cam && camFrame.img ? S.cam : null;
  let r;
  if (cam) {
    // main: the frame the solver saw, overlay from the estimate solved on that same frame
    $("viewTitle").textContent = "CAMERA VIEW";
    $("viewNote").textContent = "what the solver sees · overlay from the ESTIMATED attitude";
    const o = cam.overlay;
    r = drawSky(ctx, 0, 0, W, H, camFrame.img, cam, o, o && o.bore, 1);
    // inset: the real sky on the box screen (hidden truth), no overlay
    if (v.has_frame && frame.img) {
      const iw = Math.max(170, r.dw * 0.32), ih = iw * v.h / v.w, ix = r.ox + r.dw - iw - 10, iy = r.oy + 10;
      ctx.fillStyle = "rgba(0,0,0,.6)"; ctx.fillRect(ix - 3, iy - 3, iw + 6, ih + 24);
      // small inset: stars are 1-2 px after downscaling, so brighten them to stay visible
      drawSky(ctx, ix, iy, iw, ih, frame.img, { w: v.w, h: v.h }, null, null, 1, "brightness(2.6) contrast(1.2)");
      ctx.strokeStyle = C.truth; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]); ctx.strokeRect(ix - 0.5, iy - 0.5, iw + 1, ih + 1); ctx.setLineDash([]);
      ctx.font = "700 11px -apple-system, Helvetica, Arial"; ctx.textAlign = "left"; ctx.textBaseline = "top";
      ctx.fillStyle = C.truth; ctx.fillText("REAL SKY · hidden truth", ix + 2, iy + ih + 5);
    }
  } else {
    // no camera frames (mock, or an older solver): the truth re-render with the overlay, as before
    $("viewTitle").textContent = "SKY VIEW";
    $("viewNote").textContent = "screen in the box, re-rendered from truth · overlay from the ESTIMATED attitude";
    r = drawSky(ctx, 0, 0, W, H, v.has_frame ? frame.img : null, v, v.overlay, v.overlay && [v.w / 2, v.h / 2]);
  }
  const { ox, oy } = r;

  // status overlays
  const stale = performance.now() - lastMsg > 2500;
  if (stale) bigText(ctx, W, H, "DASHBOARD SERVER OFFLINE", "reconnecting…", C.lost);
  else if (!S.feeds.truth.ok && !S.feeds.est.ok) bigText(ctx, W, H, "NO SIGNAL", "waiting for truth (UDP 5005) and estimates (UDP 5008)", C.nosig);
  else if (!cam && !v.has_frame) bigText(ctx, W, H, "NO SKY FEED", "truth (UDP 5005) missing — showing detections only", C.nosig);
  else if (S.state === "NO SIGNAL") bigText(ctx, W, H, "NO ESTIMATE SIGNAL", "camera solve (UDP 5008) not running", C.nosig);
  else if (S.state === "SEARCH" || S.state === "LOST") {
    ctx.textAlign = "left"; ctx.textBaseline = "top"; ctx.font = "800 22px -apple-system, Helvetica, Arial";
    ctx.fillStyle = S.state === "LOST" ? C.lost : C.search;
    ctx.fillText(S.state === "LOST" ? "LOCK LOST" : "SEARCHING THE SKY…", ox + 16, oy + 14);
    const stage = stageText(S);
    if (stage) { ctx.font = "700 15px -apple-system, Helvetica, Arial"; ctx.fillText(stage, ox + 16, oy + 42); }
  }
}

// acquisition stage from camera.solve_live ("VERIFYING 3/6", "DETUMBLING 12.0", ...) as a short label;
// null when locked or when the solver does not send stages
function stageText(S) {
  const st = S && S.est && S.est.stage;
  if (!st || S.state === "LOCK" || S.state === "NO SIGNAL" || S.state === "CALIB" || st === S.state) return null;
  const [w, x] = st.split(" ", 2), rest = st.slice(w.length + 1);
  if (w === "DETECTING") return `DETECTING STARS · ${rest}`;
  if (w === "MATCHING") return "MATCHING PATTERN";
  if (w === "VERIFYING") return `VERIFYING ${rest}`;
  if (w === "RATE") return `RATE TOO HIGH · ${(+st.split(" ")[2]).toFixed(1)}°/s`;
  if (w === "DETUMBLING") return `DETUMBLING · ${(+x).toFixed(1)}°/s`;
  if (st === "SEARCH SLEW") return "SEARCH SLEW";
  return st;
}

// ---------------------------------------------------------------- 3D spacecraft
const BODY = (() => {
  // body frame: x right, y down, z boresight (camera)
  const e = [];
  const box = (x0, x1, y0, y1, z0, z1, kind) => {
    const p = [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]];
    for (const [a, b] of [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]]) e.push([p[a], p[b], kind]);
  };
  box(-0.28, 0.28, -0.28, 0.28, -0.35, 0.35, "bus");
  box(0.32, 1.15, -0.02, 0.02, -0.22, 0.22, "panel");
  box(-1.15, -0.32, -0.02, 0.02, -0.22, 0.22, "panel");
  for (const x of [0.6, 0.88]) { e.push([[x, 0, -0.22], [x, 0, 0.22], "panel"]); e.push([[-x, 0, -0.22], [-x, 0, 0.22], "panel"]); }
  e.push([[0, 0, 0.35], [0, 0, 1.25], "bore"]);
  e.push([[0, 0, 0], [0, -0.75, 0], "up"]);
  return e;
})();

function drawAtt3d(t) {
  const [ctx, W, H] = fit($("att3d"));
  ctx.clearRect(0, 0, W, H);
  const R = Math.min(W, H) * 0.42, cx = W / 2, cy = H / 2 + 4;
  const az = t * 0.00008, el = 0.38;   // slow orbit of the viewpoint
  const ca = Math.cos(az), sa = Math.sin(az), ce = Math.cos(el), se = Math.sin(el);
  const P = (v) => {   // inertial -> screen (orthographic), returns [x, y, depth]
    const x = ca * v[0] + sa * v[1], y = -sa * v[0] + ca * v[1], z = v[2];
    const yy = ce * y + se * z, zz = -se * y + ce * z;
    return [cx + R * x, cy - R * zz, yy];
  };
  // celestial sphere
  ctx.strokeStyle = "rgba(111,124,150,.35)"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.arc(cx, cy, R, 0, 2 * Math.PI); ctx.stroke();
  const ring = (fn, n = 72) => {
    for (let i = 0; i < n; i++) {
      const a = P(fn(i / n * 2 * Math.PI)), b = P(fn((i + 1) / n * 2 * Math.PI));
      ctx.globalAlpha = a[2] > 0 ? 0.18 : 0.5;
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
    }
    ctx.globalAlpha = 1;
  };
  ctx.strokeStyle = "#6f7c96";
  ring((a) => [Math.cos(a), Math.sin(a), 0]);
  ring((a) => [Math.cos(a), 0, Math.sin(a)]);
  ring((a) => [0, Math.cos(a), Math.sin(a)]);
  ctx.fillStyle = C.dim; ctx.font = "600 11px -apple-system, Helvetica, Arial"; ctx.textAlign = "center";
  const np = P([0, 0, 1.08]); ctx.fillText("NCP", np[0], np[1]);
  const vx = P([1.1, 0, 0]); ctx.fillText("♈︎ RA 0°", vx[0], vx[1]);

  const drawCraft = (M, style) => {
    const S3 = 0.62;
    const tf = (p) => P([S3 * (M[0][0] * p[0] + M[0][1] * p[1] + M[0][2] * p[2]),
                         S3 * (M[1][0] * p[0] + M[1][1] * p[1] + M[1][2] * p[2]),
                         S3 * (M[2][0] * p[0] + M[2][1] * p[1] + M[2][2] * p[2])]);
    for (const [a, b, kind] of BODY) {
      const A = tf(a), B = tf(b);
      ctx.strokeStyle = kind === "panel" ? style.panel : kind === "bore" ? style.bore : kind === "up" ? style.up : style.bus;
      ctx.lineWidth = kind === "bore" ? 2.5 : style.lw;
      ctx.setLineDash(style.dash);
      ctx.globalAlpha = style.alpha * (A[2] + B[2] > 0 ? 0.65 : 1);
      ctx.beginPath(); ctx.moveTo(A[0], A[1]); ctx.lineTo(B[0], B[1]); ctx.stroke();
    }
    ctx.setLineDash([]); ctx.globalAlpha = 1;
    // where the camera points on the sky
    const bz = [M[0][2], M[1][2], M[2][2]], q = P(bz);
    ctx.fillStyle = style.bore; ctx.beginPath(); ctx.arc(q[0], q[1], style.dot, 0, 2 * Math.PI);
    if (style.fillDot) ctx.fill(); else { ctx.strokeStyle = style.bore; ctx.lineWidth = 2; ctx.stroke(); }
  };
  if (!S) return;
  const a = S.att;
  if (a.truth) drawCraft(a.truth, { bus: C.truth, panel: C.truth, bore: C.truth, up: C.truth, lw: 1, dash: [4, 4], alpha: 0.55, dot: 9, fillDot: false });
  if (a.est) drawCraft(a.est, { bus: C.lock, panel: "#5aa8ff", bore: C.lock, up: "rgba(60,230,122,.5)", lw: 1.6, dash: [], alpha: 1, dot: 4.5, fillDot: true });
  if (!a.truth && !a.est) bigText(ctx, W, H, "NO ATTITUDE", "", C.nosig, 22);
  else if (!a.est) { ctx.fillStyle = S.state === "LOST" ? C.lost : C.search; ctx.font = "700 13px -apple-system, Helvetica, Arial";
    ctx.textAlign = "left"; ctx.fillText("no estimate — " + S.state.toLowerCase(), 8, H - 10); }
}

// ---------------------------------------------------------------- history chart
function drawChart() {
  const [ctx, W, H] = fit($("chart"));
  ctx.clearRect(0, 0, W, H);
  const L = 52, Rr = 44, T = 18, B = 20, pw = W - L - Rr, ph = H - T - B;
  const span = 90, X = (dt) => L + pw * (1 + dt / span);
  const lmin = 0, lmax = 5.2;   // log10 arcsec: 1" .. ~44°
  const Y = (a) => T + ph * (1 - (Math.log10(Math.max(a, 1)) - lmin) / (lmax - lmin));
  // grid
  ctx.font = "500 11px " + "SF Mono, Menlo, monospace"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
  for (const [v, lab] of [[1, "1″"], [10, "10″"], [60, "1′"], [600, "10′"], [3600, "1°"], [36000, "10°"]]) {
    const y = Y(v); ctx.strokeStyle = "rgba(111,124,150,.15)"; ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(L + pw, y); ctx.stroke();
    ctx.fillStyle = C.dim; ctx.fillText(lab, L - 6, y);
  }
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (let s = 0; s <= span; s += 15) { ctx.fillStyle = C.dim; ctx.fillText(s ? `-${s}s` : "now", X(-s), T + ph + 5); }
  ctx.save(); ctx.translate(12, T + ph / 2); ctx.rotate(-Math.PI / 2); ctx.textAlign = "center"; ctx.fillText("attitude error", 0, 0); ctx.restore();
  if (!S) return;
  const h = S.hist;
  // lock strip
  for (let i = 0; i < h.length; i++) {
    const x0 = X(h[i][0]), x1 = i + 1 < h.length ? X(h[i + 1][0]) : X(0);
    ctx.fillStyle = h[i][1] ? "rgba(60,230,122,.85)" : "rgba(255,90,78,.85)";
    ctx.fillRect(x0, T - 12, Math.max(1, x1 - x0), 6);
    if (!h[i][1]) { ctx.fillStyle = "rgba(255,90,78,.08)"; ctx.fillRect(x0, T, Math.max(1, x1 - x0), ph); }
  }
  // tumble rate (right axis, 0..30 deg/s)
  const YR = (r) => T + ph * (1 - Math.min(r, 30) / 30);
  ctx.beginPath(); let started = false;
  for (const p of h) { if (p[3] == null) continue; const x = X(p[0]), y = YR(p[3]); started ? ctx.lineTo(x, y) : ctx.moveTo(x, y); started = true; }
  ctx.strokeStyle = "rgba(255,184,60,.55)"; ctx.lineWidth = 1.5; ctx.setLineDash([3, 3]); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle = "rgba(255,184,60,.75)"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
  for (const r of [0, 10, 20, 30]) ctx.fillText(r + "°/s", L + pw + 6, YR(r));
  // error
  ctx.strokeStyle = C.lock; ctx.lineWidth = 2; ctx.beginPath(); started = false;
  for (const p of h) {
    if (p[2] == null) { started = false; continue; }
    const x = X(p[0]), y = Y(p[2]);
    started ? ctx.lineTo(x, y) : ctx.moveTo(x, y); started = true;
  }
  ctx.stroke();
  ctx.fillStyle = C.lock;
  for (const p of h) if (p[2] != null) { ctx.fillRect(X(p[0]) - 1, Y(p[2]) - 1, 2, 2); }
  ctx.textAlign = "left"; ctx.textBaseline = "top"; ctx.font = "600 11px -apple-system, Helvetica, Arial";
  ctx.fillStyle = C.lock; ctx.fillText("— error", L + 6, T + 2);
  ctx.fillStyle = "rgba(255,184,60,.85)"; ctx.fillText("- - tumble", L + 66, T + 2);
  ctx.fillStyle = C.lost; ctx.fillText("■ lost", L + 140, T + 2);
}

// ---------------------------------------------------------------- position from planets
const PLANET_COL = { Mercury: "#b9b4ab", Venus: "#f3d9a4", Earth: "#5aa8ff", Moon: "#c9ced8", Mars: "#ff7a50",
                     Jupiter: "#e9c79a", Saturn: "#e8d59a" };
function fmtKm(km) {
  if (km == null) return "—";
  if (km >= 1e6) return (km / 1e6).toFixed(2) + " Mkm";
  return Math.round(km).toLocaleString("en-US") + " km";
}
function updateNavText() {
  const n = S && S.nav, panel = $("navPanel");
  const show = !!n;
  if (panel.hidden === show) { panel.hidden = !show; document.body.classList.toggle("with-nav", show); }
  if (!n) return;
  const fix = n.est_au != null;
  // A flare turns the spacecraft, it doesn't move it: without a lock we keep the last fix and say so.
  const hold = fix && S.state !== "LOCK";
  panel.classList.toggle("hold", hold);
  $("navHold").textContent = hold ? `· HOLD (no lock) · last fix ${n.age_s != null ? n.age_s.toFixed(0) + " s ago" : ""}` : "";
  $("navR").textContent = fix ? n.r_au.toFixed(4) + " AU" : "—";
  $("navEarth").textContent = fix ? (n.earth_km / 1e6).toFixed(3) + " Mkm" : "—";
  $("navEarthSig").textContent = fix ? "± " + fmtKm(n.earth_sigma_km) + " (1σ)" : "";
  $("navErr").textContent = fix && n.err_km != null ? fmtKm(n.err_km) : "—";
  $("navErr").style.color = hold || !fix || n.err_km == null ? C.dim : n.axes_km && n.err_km < 3 * n.axes_km[0] ? C.lock : C.search;
  $("navSig").textContent = fix && n.axes_km ? "1σ ≤ " + fmtKm(n.axes_km[0]) : "";
  // the distance-to-Earth working: position from the sightings, Earth from the ephemeris, subtract
  const ec = fix && n.earth_calc;
  let calc = "";
  if (ec) {
    const v3 = (v) => `(${v.map((x) => (x >= 0 ? "+" : "") + x.toFixed(4)).join(", ")}) AU`;
    const d = ec.us_au.map((u, i) => ec.earth_au[i] - u);
    calc =
      `<div><span class="k">US</span><span class="v">${v3(ec.us_au)}</span> photos</div>` +
      `<div><span class="k">EARTH</span><span class="earth">${v3(ec.earth_au)}</span> ephemeris</div>` +
      `<div><span class="k">DISTANCE</span>√(${d.map((x) => (x < 0 ? `(${x.toFixed(3)})` : x.toFixed(3)) + "²").join("+")}) AU ` +
      `= <span class="v">${(ec.km / 1e6).toFixed(3)} Mkm</span></div>` +
      `<div><span class="k">LIGHT</span><span class="v">${ec.light_min.toFixed(1)} min</span>` +
      (ec.truth_km != null
        ? ` · truth ${(ec.truth_km / 1e6).toFixed(3)} Mkm, ` +
          `<span class="${Math.abs(ec.km - ec.truth_km) < 3 * (ec.sigma_km || 1e9) ? "ok" : "warn"}">off ${fmtKm(Math.abs(ec.km - ec.truth_km))}</span>`
        : "") + `</div>` +
      (ec.seen_as
        ? `<div><span class="k">CHECK</span>${ec.seen_as} seen where predicted (<span class="ok">${ec.resid_arcsec.toFixed(1)}″</span>)</div>`
        : `<div><span class="k">CHECK</span>Earth not in the photos</div>`);
  }
  if ($("navCalc").innerHTML !== calc) $("navCalc").innerHTML = calc;
  const sc = n.scan;   // solve_live --nav-scan: ecliptic sweep progress (button mode: WHERE AM I?)
  $("navNote").textContent = sc && sc.active && sc.mode === "button"
    ? (sc.wait === "lock" ? `scan waiting for lock · step ${sc.step}/${sc.n}`
      : sc.paused ? `scan on hold: spinning · step ${sc.step}/${sc.n}` : `scanning the ecliptic · step ${sc.step}/${sc.n}`)
      + (fix ? ` · ${n.bodies.length} bodies so far` : ` · ${n.n_cand || 0} candidates`)
    : sc && sc.paused ? "scan on hold: spinning"
    : !fix && sc && sc.mode === "button" && !sc.step ? "press WHERE AM I? on the console"
    : fix
    ? `${n.bodies.length} bodies: ${n.bodies.join(", ")}`
    : n.status === "no fix" ? `${n.n_cand || 0} candidates · no consistent fix yet`
    : `collecting planets · ${n.n_cand || 0} candidates`;
}
function drawNav() {
  const n = S && S.nav;
  if (!n) return;
  const [ctx, W, H] = fit($("navmap"));
  ctx.clearRect(0, 0, W, H);
  const pl = n.planets || {}, est = n.est_au, tru = n.truth_au;
  const norm = (v) => (v ? Math.hypot(v[0], v[1]) : 0);
  const Rau = Math.max(1.75, 1.15 * Math.max(norm(est), norm(tru), norm(pl.Mars)));
  const cx = W * 0.36, cy = H / 2 + 6, sc = Math.min(W * 0.34, H / 2 - 14) / Rau;
  const X = (v) => cx + v[0] * sc, Y = (v) => cy - v[1] * sc;
  ctx.font = "600 11px -apple-system, Helvetica, Arial"; ctx.textBaseline = "middle";
  // orbits (circles through today's distance) and planets
  for (const b of ["Mercury", "Venus", "Earth", "Mars"]) {
    if (!pl[b]) continue;
    ctx.strokeStyle = "rgba(111,124,150,.22)"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(cx, cy, norm(pl[b]) * sc, 0, 2 * Math.PI); ctx.stroke();
  }
  const used = new Set((n.bodies || []).flatMap((b) => b.split("+")));
  // sight lines from the estimate
  if (est) {
    for (const b of used) {
      if (!pl[b] && b !== "Sun") continue;
      const q = b === "Sun" ? [0, 0] : pl[b];
      let dx = q[0] - est[0], dy = q[1] - est[1];
      const d = Math.hypot(dx, dy), far = Rau * 3;
      const end = d * sc > W ? [est[0] + dx / d * far, est[1] + dy / d * far] : q;
      ctx.strokeStyle = "rgba(60,230,122,.22)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(X(est), Y(est)); ctx.lineTo(X(end), Y(end)); ctx.stroke();
    }
  }
  ctx.fillStyle = "#ffd75e"; ctx.beginPath(); ctx.arc(cx, cy, 5, 0, 2 * Math.PI); ctx.fill();
  ctx.fillStyle = C.dim; ctx.textAlign = "left"; ctx.fillText("Sun", cx + 7, cy - 8);
  for (const [b, v] of Object.entries(pl)) {
    if (b === "Moon") continue;
    const r = norm(v);
    let px = X(v), py = Y(v), lab = b;
    const inside = r * sc < Math.min(W * 0.34, H / 2 - 14) * 0.98;
    if (!inside) {   // Jupiter / Saturn: pin to the edge in their direction
      const k = Math.min(W * 0.34, H / 2 - 14) * 0.95 / (r * sc);
      px = cx + v[0] * sc * k; py = cy - v[1] * sc * k; lab = `${b} →`;
    }
    ctx.fillStyle = PLANET_COL[b] || C.dim; ctx.globalAlpha = used.has(b) ? 1 : 0.45;
    ctx.beginPath(); ctx.arc(px, py, inside ? 3.5 : 3, 0, 2 * Math.PI); ctx.fill();
    ctx.fillText(lab, px + 6, py - 7); ctx.globalAlpha = 1;
  }
  // the distance being reported: estimate -> Earth, labelled
  if (est && pl.Earth && n.earth_calc) {
    const E = pl.Earth;
    ctx.strokeStyle = "#5aa8ff"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(X(est), Y(est)); ctx.lineTo(X(E), Y(E)); ctx.stroke();
    const mx = (X(est) + X(E)) / 2, my = (Y(est) + Y(E)) / 2, lab = (n.earth_calc.km / 1e6).toFixed(1) + " Mkm";
    ctx.font = "700 12px -apple-system, Helvetica, Arial"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    const tw = ctx.measureText(lab).width;
    ctx.fillStyle = "rgba(7,9,15,.85)"; ctx.fillRect(mx - tw / 2 - 4, my - 9, tw + 8, 18);
    ctx.fillStyle = "#5aa8ff"; ctx.fillText(lab, mx, my);
    ctx.font = "600 11px -apple-system, Helvetica, Arial";
  }
  if (tru) {
    ctx.strokeStyle = C.truth; ctx.lineWidth = 1.5; ctx.setLineDash([3, 2]);
    ctx.beginPath(); ctx.arc(X(tru), Y(tru), 8, 0, 2 * Math.PI); ctx.stroke(); ctx.setLineDash([]);
  }
  if (est) {
    ctx.fillStyle = S.state === "LOCK" ? C.lock : C.search;
    ctx.beginPath(); ctx.arc(X(est), Y(est), 4, 0, 2 * Math.PI); ctx.fill();
  }
  // legend + 1 AU scale bar
  ctx.textAlign = "left"; ctx.textBaseline = "middle"; ctx.fillStyle = C.dim;
  ctx.strokeStyle = C.dim; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(6, H - 8); ctx.lineTo(6 + sc, H - 8); ctx.stroke();
  ctx.fillText("1 AU", 10 + sc, H - 8);
  ctx.fillStyle = C.lock; ctx.beginPath(); ctx.arc(10, 10, 4, 0, 2 * Math.PI); ctx.fill();
  ctx.fillStyle = C.dim; ctx.fillText("estimate", 18, 10);
  ctx.strokeStyle = C.truth; ctx.setLineDash([3, 2]); ctx.beginPath(); ctx.arc(80, 10, 5, 0, 2 * Math.PI); ctx.stroke();
  ctx.setLineDash([]); ctx.fillText("truth (hidden)", 90, 10);
  // zoom inset: estimate at the centre, 1-sigma / 3-sigma ellipse (ecliptic plane), truth offset
  if (est && n.ellipse_km) {
    const iw = Math.min(W * 0.34, H * 0.8, 190), ih = iw, ix = W - iw - 4, iy = H - ih - 4;
    ctx.fillStyle = "rgba(7,9,15,.85)"; ctx.fillRect(ix, iy, iw, ih);
    ctx.strokeStyle = C.edge; ctx.strokeRect(ix + 0.5, iy + 0.5, iw - 1, ih - 1);
    const [a, b, th] = n.ellipse_km, e = n.err_ecl_km;
    const span = Math.max(3.3 * a, e ? 1.25 * Math.hypot(e[0], e[1]) : 0);
    const k = Math.min(iw, ih) / 2 / span, icx = ix + iw / 2, icy = iy + ih / 2;
    for (const [m, al] of [[3, 0.35], [1, 0.9]]) {
      ctx.strokeStyle = C.lock; ctx.globalAlpha = al; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.ellipse(icx, icy, Math.max(1, m * a * k), Math.max(1, m * b * k), -th, 0, 2 * Math.PI); ctx.stroke();
    }
    ctx.globalAlpha = 1;
    ctx.fillStyle = C.lock; ctx.beginPath(); ctx.arc(icx, icy, 3, 0, 2 * Math.PI); ctx.fill();
    if (e) {
      ctx.strokeStyle = C.truth; ctx.setLineDash([3, 2]); ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(icx + e[0] * k, icy - e[1] * k, 6, 0, 2 * Math.PI); ctx.stroke(); ctx.setLineDash([]);
    }
    // scale bar: a round number of km
    const target = span * 0.6, p10 = Math.pow(10, Math.floor(Math.log10(target)));
    const bar = [1, 2, 5].map((m) => m * p10).filter((v) => v <= target).pop() || p10;
    ctx.strokeStyle = C.text || "#dbe3f4"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(ix + 8, iy + ih - 10); ctx.lineTo(ix + 8 + bar * k, iy + ih - 10); ctx.stroke();
    ctx.fillStyle = C.dim; ctx.textAlign = "left"; ctx.textBaseline = "bottom";
    ctx.fillText(fmtKm(bar), ix + 8, iy + ih - 13);
    ctx.textBaseline = "top"; ctx.fillText("ZOOM · 1σ / 3σ", ix + 6, iy + 5);
  } else if (!est) {
    ctx.fillStyle = C.search; ctx.textAlign = "right"; ctx.textBaseline = "top";
    ctx.fillText(n.status === "no fix" ? "NO FIX YET" : "COLLECTING PLANETS…", W - 6, 6);
  }
}

// ---------------------------------------------------------------- loop
function frameLoop(t) {
  drawView(); drawAtt3d(t); drawChart(); drawNav();
  if (S && performance.now() - lastMsg > 2500) { $("lockBadge").className = "badge nosig"; $("lockBadge").innerHTML = "NO<br>SIGNAL"; }
  requestAnimationFrame(frameLoop);
}
document.addEventListener("keydown", (e) => {
  if ((e.key === "f" || e.key === "F") && !e.metaKey && !e.ctrlKey) {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {});
    else document.exitFullscreen();
  }
});
document.addEventListener("fullscreenchange", () => document.body.classList.toggle("fs", !!document.fullscreenElement));
function loadConfig() {
  fetch("/config").then((r) => r.json()).then((c) => {
    if (!c.console_url) return;
    const u = new URL(c.console_url, location.href);
    u.searchParams.set("embed", "1");
    $("consolePanel").hidden = false;
    document.body.classList.add("with-console");
    // the console (bridge) can start after the dashboard: load the frame only once it answers
    const tryLoad = () => fetch(u.href, { mode: "no-cors", cache: "no-store" })
      .then(() => { $("consoleFrame").src = u.href; })
      .catch(() => setTimeout(tryLoad, 1000));
    tryLoad();
  }).catch(() => {});
}
loadConfig();
connect();
requestAnimationFrame(frameLoop);
