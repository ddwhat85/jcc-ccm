// demo-api.js의 예지 코어를 Node에서 돌려 결과를 JSON으로 낸다(test_parity.py가 호출).
//   node parity_harness.js <demo-api.js> <fixtures.json>
// 브라우저 전역(window·setInterval 등)은 최소한으로 흉내 낸다.
const fs = require("fs"), vm = require("vm");
const [src, fx] = [fs.readFileSync(process.argv[2], "utf8"), JSON.parse(fs.readFileSync(process.argv[3], "utf8"))];
const win = { fetch: null, location: { href: "http://localhost/" } };
const ctx = { window: win, setInterval() {}, console: { log() {} }, Response: function () {},
  URL, URLSearchParams, Date, Math, JSON, Promise };
vm.createContext(ctx);
vm.runInContext(src, ctx);
const C = win.JCC_DEMO._core;
const r2 = x => (x == null ? null : Math.round(x * 100) / 100);
const out = {
  slope: fx.slope.map(s => r2(C.slopePerMin(s))),
  z: fx.z.map(([v, b]) => r2(C.robustZ(v, b))),
  fire: fx.fire.map(sig => { const r = C.assessFire(sig); return { fri: r.fri, stage: r.stage }; }),
  contact: fx.contact.map(([i, t, a, h]) => { const r = C.assessContact(i, t, a, h);
    return { stage: r.stage, residual: r.residual, slope: r.residual_slope }; }),
  dew: fx.dew.map(([t, rh, s, h]) => { const r = C.assessDew(t, rh, s, h);
    return { stage: r.stage, margin: r.margin, slope: r.margin_slope }; }),
  // 접점 기준: 같은 표본열을 먹여 확정 시각·k·진행률이 같은지(운영 기본 학습 조건으로)
  baseline: fx.baseline.map(seq => { const b = new C.ContactBaseline({ learnSamples: 300, learnSpan: 1800 });
    const mid = []; seq.forEach(([ts, I, T, Ta], i) => { b.observe(ts, I, T, Ta); if (i === 149) mid.push(b.progress()); });
    return { status: b.status, k: b.k == null ? null : Math.round(b.k * 1e6) / 1e6, learned_at: b.learned_at,
      mid: Math.round(mid[0] * 1e4) / 1e4 }; }),
  contact_ref: fx.contact_ref.map(([i, t, a, h, k]) => { const r = C.assessContact(i, t, a, h, k, null);
    return { stage: r.stage, residual: r.residual }; }),
  incidents: fx.incidents.map(c => C.groupAlarms(c.alarms, c.sensors, c.panels)
    .map(x => [x.cause, x.primary, x.alarms.slice().sort((a, b) => a - b), x.severity, x.acked])),
};
process.stdout.write(JSON.stringify(out));
