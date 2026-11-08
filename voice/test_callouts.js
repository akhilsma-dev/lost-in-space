// Scenario test for dashboard/static/voice.js (fake clock, fake audio). Run: node voice/test_callouts.js
const fs = require("fs");
const src = fs.readFileSync(process.argv[2] || require("path").join(__dirname, "../dashboard/static/voice.js"), "utf8");

function run(name, steps, expect) {
  let T = 0;
  const players = [];
  const ctx = {
    performance: { now: () => T },
    localStorage: { getItem: () => null, setItem() {} },
    document: { getElementById: () => null, addEventListener() {} },
    fetch: () => ({ then: () => ({ then: () => ({ catch() {} }) }) }),   // manifest loaded by hand below
    Audio: class {
      constructor(src) { this.src = src; this.ls = {}; }
      addEventListener(ev, f) { this.ls[ev] = f; }
      play() { this.endAt = T + 1500; players.push(this); return Promise.resolve(); }
      pause() { this.endAt = null; }
    },
  };
  const fn = new Function(...Object.keys(ctx), src + "\nreturn Voice;");
  const V = fn(...Object.values(ctx));
  // inject clips the way load() would
  const ids = ["online", "lock_first", "lock_again", "lost", "search", "flare", "unstable", "glare_on", "glare_off",
    "no_signal", "signal_back", ...[...Array(10)].map((_, i) => "relock_" + (i + 1)),
    ...[0, 5, 10, 15, 20].map((r) => "rate_" + r), ...[5, 10, 20, 30, 60].map((a) => "acc_" + a)];
  const clips = Object.fromEntries(ids.map((i) => [i, { file: i + ".mp3", text: i }]));
  V._test_load(clips, ctx.Audio);

  const snap = (state, extra = {}) => ({ state, truth: { rate_dps: extra.rate ?? 0, glare: false, flash: extra.flash ?? 0 },
    est: { status: state, stage: extra.stage ?? state }, err: { point: 8 }, stats: {} });
  for (const [dur, state, extra] of steps) {
    for (let t = 0; t < dur; t += 66) {
      V.update(snap(state, extra));
      for (const p of players) if (p.endAt != null && T >= p.endAt) { p.endAt = null; p.ls.ended({ target: p }); }
      T += 66;
    }
  }
  const said = V.history.map((h) => h[1]);
  if (expect.relocks) { const n = said.filter((x) => /^relock_/.test(x)).length; console.log(`${n >= expect.relocks ? "PASS" : "FAIL"}  ${name} (${n} relocks)\n      said: ${said.join(", ")}`); return n >= expect.relocks; }
  const ok = expect.every((e) => said.some((s) => (e instanceof RegExp ? e.test(s) : s === e)))
    && !(expect.not || []).some((e) => said.includes(e));
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}\n      said: ${said.join(", ")}`);
  return ok;
}

let all = true;
// 1. tuned solver: flare, lost for only 0.6 s (shorter than the LOST debounce), lock again
all &= run("short outage after flare", [
  [3000, "LOCK"], [600, "LOST", { flash: 255 }], [400, "LOCK", { flash: 255 }], [4000, "LOCK"]],
  ["flare", /^relock_1$/]);
// 2. lock flapping -> "unstable" mute, then a flare: relock still announced
const flap = [];
for (let i = 0; i < 6; i++) flap.push([1200, "LOST"], [900, "LOCK"]);
all &= run("flare right after lock unstable", [[3000, "LOCK"], ...flap,
  [1500, "LOST", { flash: 255 }], [500, "LOST"], [4000, "LOCK"]],
  ["unstable", "flare", /^relock_\d+$/]);
// 3. flare while the knob also moves (rate line queued): relock not dropped
all &= run("flare + tumble change", [[3000, "LOCK"], [1500, "LOST", { flash: 255, rate: 10 }],
  [800, "LOST", { rate: 10 }], [5000, "LOCK", { rate: 10 }]],
  ["flare", "rate_10", /^relock_\d+$/]);
// 4. several flares in a row: one relock per flare
all &= run("three flares", [[3000, "LOCK"],
  [1500, "LOST", { flash: 255 }], [3000, "LOCK"],
  [1500, "LOST", { flash: 255 }], [3000, "LOCK"],
  [1500, "LOST", { flash: 255 }], [3000, "LOCK"]], { relocks: 3, every: () => true });
// 5. recovery: detumble from 15 deg/s (stabilized), then a search slew (announced, rate burst not read out)
const ramp = [];
for (let r = 15; r > 0; r -= 1) ramp.push([250, "SEARCH", { rate: r, stage: "DETUMBLING " + r }]);
all &= run("detumble then search slew", [[3000, "LOCK", { rate: 15 }], [4000, "SEARCH", { rate: 15, stage: "RATE HIGH 15" }],
  ...ramp, [9000, "SEARCH", { stage: "MATCHING" }], [3000, "SEARCH", { rate: 6, stage: "SEARCH SLEW" }],
  [4000, "SEARCH", { stage: "MATCHING" }]],
  Object.assign(["rate_0", "search"], { not: ["rate_5", "rate_10"] }));
process.exitCode = all ? 0 : 1;
