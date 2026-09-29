// predict-core.js(JS 예지 코어)와 demo-api.js를 Node에서 돌려 결과를 JSON으로 낸다(test_parity.py가 호출).
//   node parity_harness.js <predict-core.js> <demo-api.js> <fixtures.json>
// 브라우저 전역(window·setInterval 등)은 최소한으로 흉내 낸다. 로드 순서는 화면과 같다(코어 → 데모).
const fs = require("fs"), vm = require("vm");
const [core, src, fx] = [fs.readFileSync(process.argv[2], "utf8"), fs.readFileSync(process.argv[3], "utf8"),
  JSON.parse(fs.readFileSync(process.argv[4], "utf8"))];
const win = { fetch: null, location: { href: "http://localhost/" } };
const ctx = { window: win, setInterval() {}, console: { log() {} }, Response: function () {},
  URL, URLSearchParams, Date, Math, JSON, Promise };
vm.createContext(ctx);
vm.runInContext(core, ctx);
win.JCCPredict = ctx.JCCPredict;
vm.runInContext(src, ctx);
const C = Object.assign({}, ctx.JCCPredict, { groupAlarms: win.JCC_DEMO._core.groupAlarms });
// 판넬 통합(입력 조립 → Predictor)을 한 틱씩 — 서버 storage.predict_scan·tuning_eval.replay와 같은 흐름
function panelRun(scn) {
  const P = new C.Predictor(C.defaultCfg());
  const data = scn.roles, roles = {};
  Object.keys(data).forEach(r => { roles[r] = ["sim", r]; });
  let now = 0;
  const series = (dev, key, n) => { const a = data[key]; let lo = 0, hi = a.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (a[m][0] <= now) lo = m + 1; else hi = m; }
    return a.slice(Math.max(0, lo - n), lo); };
  const out = [];
  for (let k = 0; ; k++) {
    const t = scn.t0 + k * scn.step; if (t > scn.t_end) break;
    now = t;
    const asm = C.buildPanelInputs(roles, series, t);
    if (!Object.keys(asm.inputs).length) { out.push(null); continue; }
    const r = P.assessPanel("sim", t, asm.inputs, asm.pending);
    out.push([r.fire.stage, r.fire.fri, r.fire.vent.open, r.contact.stage, r.contact.residual,
      r.dew.stage, r.dew.margin, r.dew.heater, r.dew.fan, r.contact.baseline.status]);
  }
  return out;
}
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
  baseline: fx.baseline.map(seq => { const b = new C.ContactBaseline(C.defaultCfg().contact);
    const mid = []; seq.forEach(([ts, I, T, Ta], i) => { b.observe(ts, I, T, Ta); if (i === 149) mid.push(b.progress()); });
    return { status: b.status, k: b.k == null ? null : Math.round(b.k * 1e6) / 1e6, learned_at: b.learned_at,
      mid: Math.round(mid[0] * 1e4) / 1e4 }; }),
  contact_ref: fx.contact_ref.map(([i, t, a, h, k]) => { const r = C.assessContact(i, t, a, h, null, k, null);
    return { stage: r.stage, residual: r.residual }; }),
  panel: fx.panel.map(panelRun),
  // 튜닝 성적표: 같은 시나리오·설정으로 사건·판정이 파이썬과 같은지(runs는 크기 때문에 뺀다)
  tuning: (() => { const t0 = Date.now(); const out = fx.tuning.params.map(p => {
    const sc = C.scorecard(fx.tuning.scenarios, p); delete sc.runs; return sc; });
    return { cards: out, ms_default: (() => { const a = Date.now(); C.scorecard(fx.tuning.scenarios, fx.tuning.params[0]); return Date.now() - a; })(),
      ms_total: Date.now() - t0 }; })(),
  incidents: fx.incidents.map(c => C.groupAlarms(c.alarms, c.sensors, c.panels)
    .map(x => [x.cause, x.primary, x.alarms.slice().sort((a, b) => a - b), x.severity, x.acked])),
};
process.stdout.write(JSON.stringify(out));
