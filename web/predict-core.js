/* JCC 예지 코어 (JS 한 벌) — server/jcc_server/{fire_risk,contact_heat,dewpoint,inputs,predict,tuning_eval}.py 미러.
 *
 * 누가 쓰나: 대시보드 튜닝 콘솔(실서버·데모 공통), 데모 인-브라우저 백엔드(demo-api.js),
 *            패리티 테스트(server/tests/parity_harness.js). 코어가 한 벌이라 셋이 갈라지지 않는다.
 * 설정 키는 파이썬 필드명 그대로(fire.h2.warn, contact.res_alarm …) — params.py 레지스트리와 같은 이름.
 * ⚠ 로직을 바꾸면 파이썬 정본과 함께 바꾸고 test_parity를 돌릴 것.
 */
(function (root) {
  "use strict";

  // ── 공통 ────────────────────────────────────────────────
  function ramp(x, lo, hi) { if (hi <= lo) return x >= hi ? 1 : 0; if (x <= lo) return 0; if (x >= hi) return 1; return (x - lo) / (hi - lo); }
  function clamp01(x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }
  // 최소제곱 기울기(분당). 시각을 평균 중심으로 옮겨 계산 — 에포크 초를 그대로 제곱합하면 상쇄로 0이 된다.
  function slopePerMin(series) {
    const pts = series.filter(p => p[1] != null); const n = pts.length;
    if (n < 2) return 0;
    const t0 = pts[0][0], xs = pts.map(p => p[0] - t0), ys = pts.map(p => p[1]);
    const mx = xs.reduce((a, c) => a + c, 0) / n, my = ys.reduce((a, c) => a + c, 0) / n;
    let num = 0, den = 0;
    for (let i = 0; i < n; i++) { num += (xs[i] - mx) * (ys[i] - my); den += (xs[i] - mx) * (xs[i] - mx); }
    if (den <= 1e-9) return 0;
    return (num / den) * 60;
  }
  const MIN_BASELINE = 30;   // fire_risk.MIN_BASELINE — 평소값이 이보다 적으면 z를 계산하지 않는다
  function robustZ(value, baseline) {
    const vals = baseline.filter(v => v != null); if (vals.length < MIN_BASELINE || value == null) return 0;
    const median = a => { const s = a.slice().sort((p, q) => p - q), m = s.length;
      return m % 2 ? s[(m - 1) / 2] : (s[m / 2 - 1] + s[m / 2]) / 2; };
    const med = median(vals), mad = median(vals.map(v => Math.abs(v - med)));
    let sigma = mad > 1e-9 ? 1.4826 * mad : 0;
    if (!sigma) { const m = vals.reduce((a, c) => a + c, 0) / vals.length; sigma = Math.sqrt(vals.reduce((a, c) => a + (c - m) * (c - m), 0) / vals.length); }
    if (sigma <= 1e-9) return 0; return (value - med) / sigma;
  }
  const r1 = x => Math.round(x * 10) / 10, r2 = x => Math.round(x * 100) / 100;

  // ── 설정 (FireConfig·ContactCfg·DewCfg 기본값) ──────────
  function defaultCfg() {
    return {
      fire: { h2: { warn: 10, alarm: 25, rise_warn: 3, rise_alarm: 10 },
              voc: { warn: 200, alarm: 1000, rise_warn: 100, rise_alarm: 400 },
              co: { warn: 50, alarm: 200, rise_warn: 20, rise_alarm: 80 },
              temp_rise_warn: 1, temp_rise_alarm: 5, w_h2: 0.38, w_voc: 0.38, w_co: 0.32, w_temp: 0.10,
              pair_boost: 0.20, conf_boost: 0.15, z_lo: 3, z_hi: 6,
              open_fri: 55, close_fri: 20, crit_fri: 80, watch_fri: 30, over_hold: 60 },
      contact: { i_min: 2, res_warn: 5, res_alarm: 11, t_abs_alarm: 60, rise_warn: 0.3, k_default: 0.03,
                 k_min: 0, k_max: 1, learn_samples: 300, learn_span: 1800, tau_days: 7 },
      dew: { margin_warn: 3, margin_alarm: 1, rh_high: 80, fall_warn: 0.4, horizon_min: 10, trend_cap: 6 },
    };
  }
  // 레지스트리 키(fire.h2.warn) 값을 cfg에 제자리로 대입 — params.apply_to와 같다
  function applyParams(cfg, params) {
    for (const key in params) {
      const parts = key.split("."); let o = cfg;
      for (let i = 0; i < parts.length - 1; i++) o = o[parts[i]];
      o[parts[parts.length - 1]] = Number(params[key]);
    }
    return cfg;
  }
  function cfgFromParams(params) { return applyParams(defaultCfg(), params || {}); }

  // ── 화재 (fire_risk.assess) ─────────────────────────────
  function gasScore(value, series, baseline, g, c) {
    const lvl = ramp(value == null ? 0 : value, g.warn, g.alarm);
    const sp = slopePerMin(series || []), rise = ramp(sp, g.rise_warn, g.rise_alarm);
    const z = robustZ(value, baseline || []), anom = ramp(z, c.z_lo, c.z_hi);
    return { g: Math.max(lvl, rise, anom), lvl, sp, rise, z, anom };
  }
  function assessFire(sig, c) {
    c = c || defaultCfg().fire;
    const h2 = sig.h2 || {}, voc = sig.voc || {}, cog = sig.co || {}, temp = sig.temp || {};
    const smoke = !!sig.smoke, curAb = !!sig.current_abnormal;
    const H = gasScore(h2.value, h2.series, h2.baseline, c.h2, c);
    const V = gasScore(voc.value, voc.series, voc.baseline, c.voc, c);
    const C = gasScore(cog.value, cog.series, cog.baseline, c.co, c);
    const tsp = slopePerMin(temp.series || []), tTerm = ramp(tsp, c.temp_rise_warn, c.temp_rise_alarm);
    const second = [H.g, V.g, C.g].sort((a, b) => b - a)[1];
    const pair = second >= 0.3 ? c.pair_boost * second : 0;
    const conf = (smoke || curAb) ? c.conf_boost : 0;
    let fri = 100 * clamp01(c.w_h2 * H.g + c.w_voc * V.g + c.w_co * C.g + pair + c.w_temp * tTerm + conf);
    const h2v = h2.value, vocv = voc.value, cov = cog.value;
    // 경보치 초과가 over_hold초 이어져야 강제 '극한'(over_held 없으면 곧바로 지속으로 봄)
    const held = sig.over_held, G = ["h2", "voc", "co"];
    const overNow = [[h2v, c.h2], [vocv, c.voc], [cov, c.co]].map(([v, g]) => v != null && v >= g.alarm);
    const over = overNow.map((o, i) => o && (held == null || (held[G[i]] || 0) >= c.over_hold));
    if (over.some(Boolean)) fri = Math.max(fri, c.crit_fri);
    if (smoke) fri = Math.max(fri, 85);
    const bothStrong = second >= 0.5;
    let stage;
    if (fri >= c.crit_fri || smoke || over.filter(Boolean).length >= 2) stage = "critical";
    else if (fri >= c.open_fri || bothStrong) stage = "danger";
    else if (fri >= c.watch_fri) stage = "watch";
    else stage = "normal";
    const R = [];
    if (H.rise > 0.3) R.push(`H2 상승 ${r2(H.sp)}%LEL/분`);
    if (H.anom > 0.3) R.push(`H2 평소대비 급등 z=${r2(H.z)}`);
    if (H.lvl > 0.3) R.push(`H2 ${h2v}%LEL(경고선 접근)`);
    if (V.rise > 0.3) R.push(`VOC 상승 ${r2(V.sp)}ppm/분`);
    if (V.anom > 0.3) R.push(`VOC 평소대비 급등 z=${r2(V.z)}`);
    if (V.lvl > 0.3) R.push(`VOC ${vocv}ppm(경고선 접근)`);
    if (C.rise > 0.3) R.push(`CO 상승 ${r2(C.sp)}ppm/분`);
    if (C.anom > 0.3) R.push(`CO 평소대비 급등 z=${r2(C.z)}`);
    if (C.lvl > 0.3) R.push(`CO ${cov}ppm(경고선 접근)`);
    if (pair > 0) R.push([["H2", H.g], ["VOC", V.g], ["CO", C.g]].filter(x => x[1] >= 0.3).map(x => x[0]).join("·") + " 동반 상승 — 열폭주 서명");
    overNow.forEach((o, i) => { if (o && !over[i]) R.push(`${["H2", "VOC", "CO"][i]} 경보치 초과 ${Math.floor((held || {})[G[i]] || 0)}초 — 지속 확인 중`); });
    if (tTerm > 0.3) R.push(`온도 급상승 ${r1(tsp)}°C/분`);
    if (smoke) R.push("열연기 감지");
    if (curAb) R.push("전류 이상");
    if (!R.length) R.push("정상 범위");
    return { fri: r1(fri), stage, reasons: R };
  }

  // ── 접점 발열 (contact_heat) ────────────────────────────
  function fitK(samples, c) {
    let num = 0, den = 0;
    for (const [, I, T, Ta] of samples) { if (I == null || T == null || Ta == null || I < c.i_min) continue; const x = I * I; num += x * (T - Ta); den += x * x; }
    if (den <= 1e-9) return c.k_default; return Math.min(c.k_max, Math.max(c.k_min, num / den));
  }
  // 정상 기간 학습 → 고정 → 아주 천천히 추종(ContactBaseline). cfg는 참조로 들고 있어 reconfigure가 바로 반영된다.
  class ContactBaseline {
    constructor(c) { this.cfg = c || defaultCfg().contact; this.last_ts = null; this.reset(); }
    reset() { const last = this.last_ts; Object.assign(this, { status: "learning", k: null, sxy: 0, sxx: 0, n: 0, t0: null,
      learned_at: null }); this.last_ts = last === undefined ? null : last; }   // 재학습은 reset 이후 표본만
    get ready() { return this.status === "ready" && this.k != null; }
    skip(ts) { if (ts != null && (this.last_ts == null || ts > this.last_ts)) this.last_ts = ts; }
    progress() {
      if (this.ready) return 1;
      if (this.t0 == null || this.last_ts == null) return 0;
      return Math.min(1, this.n / Math.max(1, this.cfg.learn_samples), (this.last_ts - this.t0) / Math.max(1, this.cfg.learn_span));
    }
    observe(ts, I, T, Ta) {
      const c = this.cfg;
      if (ts == null || I == null || T == null || Ta == null || I < c.i_min) return null;
      if (this.last_ts != null && ts <= this.last_ts) return null;
      const dt = this.last_ts != null ? ts - this.last_ts : 0;
      this.last_ts = ts;
      const x = I * I, y = T - Ta;
      if (!this.ready) {
        if (this.t0 == null) this.t0 = ts;
        this.sxy += x * y; this.sxx += x * x; this.n += 1;
        if (this.n >= c.learn_samples && ts - this.t0 >= c.learn_span && this.sxx > 1e-9) {
          this.k = Math.min(c.k_max, Math.max(c.k_min, this.sxy / this.sxx));
          this.status = "ready"; this.learned_at = ts; return "ready";
        }
        return null;
      }
      if (dt > 0 && x > 1e-9) {
        const kObs = Math.min(c.k_max, Math.max(c.k_min, y / x));
        const xRef = this.n ? Math.sqrt(this.sxx / this.n) : x;      // 평소 부하(학습 기간 I²의 RMS)
        if (Math.abs(kObs - this.k) * xRef < c.res_warn / 2) this.k += Math.min(1, dt / (c.tau_days * 86400)) * (kObs - this.k);
      }
      return null;
    }
  }
  function assessContact(cur, temp, amb, hist, c, kRef, learning) {
    c = c || defaultCfg().contact;
    const k = kRef != null ? kRef : fitK(hist || [], c);
    const dt = (temp != null && amb != null) ? temp - amb : 0;
    const exp = k * (cur || 0) * (cur || 0), res = dt - exp;
    const rser = [];
    for (const [ts, I, T, Ta] of (hist || [])) { if (I == null || T == null || Ta == null || I < c.i_min) continue; rser.push([ts, (T - Ta) - k * I * I]); }
    const rslope = slopePerMin(rser);
    let stage; const R = [];
    if (temp != null && temp >= c.t_abs_alarm) { stage = "danger"; R.push(`접점 온도 ${r1(temp)}°C — 절대 위험`); }
    else if ((cur || 0) < c.i_min) { stage = "normal"; R.push("부하 낮음 — 발열 판정 보류"); }
    else if (res >= c.res_alarm) { stage = "danger"; R.push(`전류 대비 초과발열 +${r1(res)}°C — 접촉저항 급증 의심`); }
    else if (res >= c.res_warn || rslope >= c.rise_warn) { stage = "watch";
      if (res >= c.res_warn) R.push(`전류 대비 발열 +${r1(res)}°C` + (kRef != null ? " — 기준 대비 서서히 증가(접점 풀림·부식 의심)" : ""));
      if (rslope >= c.rise_warn) R.push(`발열 추세 상승 ${r2(rslope)}°C/분 — 접점 열화 조짐`); }
    else { stage = "normal"; R.push("정상 — 전류 대비 발열 정상"); }
    if (learning != null && kRef == null) R.push(`기준 학습 중 ${Math.floor(learning * 100)}% — 느린 열화 판정은 학습 후`);
    return { stage, delta_t: r1(dt), expected: r1(exp), residual: r1(res), residual_slope: r2(rslope), k: Math.round(k * 1e4) / 1e4, reasons: R };
  }

  // ── 결로 (dewpoint) ─────────────────────────────────────
  function dewPoint(t, rh) { if (t == null || rh == null || rh <= 0) return null; rh = Math.max(1, Math.min(100, rh)); const a = 17.62, b = 243.12; const g = Math.log(rh / 100) + a * t / (b + t); return b * g / (a - g); }
  function assessDew(temp, rh, surface, hist, c) {
    c = c || defaultCfg().dew;
    const td = dewPoint(temp, rh);
    if (td == null) return { stage: "normal", dew_point: null, margin: null, margin_slope: 0, rh: rh || 0, action: "none", reasons: ["데이터 부족"] };
    const ref = surface != null ? surface : temp, margin = ref - td;
    const mser = [];
    for (const [ts, ht, hrh, hs] of (hist || [])) { const htd = dewPoint(ht, hrh); if (htd == null) continue; const href = hs != null ? hs : ht; mser.push([ts, href - htd]); }
    const mslope = slopePerMin(mser);
    const eta = mslope < 0 ? (margin - c.margin_alarm) / -mslope : null;
    const trendHit = mslope <= -c.fall_warn && margin <= c.trend_cap && eta != null && eta <= c.horizon_min;
    let stage, action; const R = [];
    if (margin <= c.margin_alarm) { stage = "danger"; action = "heater_fan"; R.push(`이슬점 여유 ${r1(margin)}°C — 결로 임박 (이슬점 ${r1(td)}°C)`); }
    else if (margin <= c.margin_warn || (rh != null && rh >= c.rh_high) || trendHit) { stage = "watch"; action = "fan"; if (margin <= c.margin_warn) R.push(`이슬점 여유 ${r1(margin)}°C 좁음`); if (rh != null && rh >= c.rh_high) R.push(`습도 ${Math.round(rh)}% 높음`); if (trendHit) R.push(`여유 축소 ${r2(mslope)}°C/분 — 약 ${Math.max(1, Math.round(eta))}분 뒤 결로 임박`); }
    else { stage = "normal"; action = "none"; R.push(`정상 — 이슬점 여유 ${r1(margin)}°C`); }
    return { stage, dew_point: r1(td), margin: r1(margin), margin_slope: r2(mslope), rh: r1(rh || 0), action, reasons: R };
  }

  // ── 컨트롤러 (fire_risk.VentController · predict.DewActuator) ──
  class VentController {
    constructor(c, recoverHold) { this.cfg = c; this.recover_hold = recoverHold == null ? 60 : recoverHold; this.open = false; this.manual = false; this.below = null; }
    setManual(open) { this.manual = true; this.open = !!open; }
    clearManual() { this.manual = false; }
    step(fri, stage, now) {
      if (this.manual) return null;
      if (stage === "danger" || stage === "critical") { this.below = null; if (!this.open) { this.open = true; return "open"; } return stage === "critical" ? "hold" : null; }
      if (this.open) {
        if (fri < this.cfg.close_fri) { if (this.below == null) this.below = now; else if (now - this.below >= this.recover_hold) { this.open = false; this.below = null; return "close"; } }
        else this.below = null;
      }
      return null;
    }
  }
  class DewActuator {
    constructor(recoverHold) { this.recover_hold = recoverHold == null ? 60 : recoverHold; this.heater = false; this.fan = false; this.manual = false; this.calm = null; }
    setManual(heater, fan) { this.manual = true; this.heater = !!heater; this.fan = !!fan; }
    clearManual() { this.manual = false; }
    step(stage, now) {
      if (this.manual) return null;
      if (stage === "danger") { this.calm = null; if (!(this.heater && this.fan)) { this.heater = true; this.fan = true; return "heater_fan"; } return null; }
      if (stage === "watch") { this.calm = null; if (!this.fan) { this.fan = true; return "fan"; } return null; }
      if (this.heater || this.fan) { if (this.calm == null) this.calm = now; else if (now - this.calm >= this.recover_hold) { this.heater = false; this.fan = false; this.calm = null; return "off"; } }
      return null;
    }
  }

  // ── 입력 조립 (inputs.py) ───────────────────────────────
  const ROLES = ["h2", "voc", "co", "current", "contact_temp", "ambient", "humidity", "smoke"];
  const GASES = ["h2", "voc", "co"];
  const WARMUP = 6, STALE_MIN = 15, MIN_SPAN = 20, SLOPE_N = 30;
  function despike(pts) {
    if (pts.length < 3) return pts;
    const out = [];
    for (let i = 2; i < pts.length; i++) out.push([pts[i][0], [pts[i - 2][1], pts[i - 1][1], pts[i][1]].sort((a, b) => a - b)[1]]);
    return out;
  }
  function asof(base, other, tol) {
    tol = tol == null ? 30 : tol; const out = []; let j = 0;
    for (const [ts] of base) {
      while (j + 1 < other.length && other[j + 1][0] <= ts) j++;
      const ok = other.length && other[j][0] <= ts && ts - other[j][0] <= tol;
      out.push(ok ? other[j][1] : null);
    }
    return out;
  }
  function currentValue(series, dev, key) {
    const vs = series(dev, key, 8).slice(-3).map(p => p[1]).sort((a, b) => a - b);
    if (!vs.length) return null;
    return vs.length === 2 ? vs[0] : vs[Math.floor(vs.length / 2)];
  }
  function readiness(series, pairs, now) {
    const ns = [], spans = [];
    for (const [dev, key] of pairs) {
      const pts = series(dev, key, SLOPE_N);
      const gaps = pts.slice(1).map((p, i) => p[0] - pts[i][0]).sort((a, b) => a - b);
      const limit = gaps.length ? Math.max(STALE_MIN, 4 * gaps[Math.floor(gaps.length / 2)]) : STALE_MIN;
      if (!pts.length || now - pts[pts.length - 1][0] > limit) return "데이터 끊김 — 판정 보류(상태 유지)";
      ns.push(pts.length); spans.push(pts[pts.length - 1][0] - pts[0][0]);
    }
    if (!ns.length) return "센서 없음";
    const m = Math.min.apply(null, ns);
    if (m < WARMUP) return `학습 중 ${m}/${WARMUP}표본`;
    const sp = Math.min.apply(null, spans);
    if (sp < MIN_SPAN) return `학습 중 ${Math.floor(sp)}/${MIN_SPAN}초`;
    return null;
  }
  // roles = {역할: [dev, key] | null}, series(dev, key, n) → 최근 n개 읽기 중 유효 [[ts, v]] (오래된→최신)
  function buildPanelInputs(roles, series, now) {
    const r = {}; ROLES.forEach(k => { r[k] = roles[k] || null; });
    const memo = {};
    const val = role => { if (!r[role]) return null; if (!(role in memo)) memo[role] = currentValue(series, r[role][0], r[role][1]); return memo[role]; };
    const ser = (role, n) => despike(series(r[role][0], r[role][1], n));
    const base = role => ser(role, 150).slice(0, -SLOPE_N).map(p => p[1]);
    const inputs = {};
    for (const g of GASES) if (r[g]) inputs[g] = { value: val(g), series: ser(g, SLOPE_N), baseline: base(g) };
    if (r.ambient) inputs.temp = { value: val("ambient"), series: ser("ambient", SLOPE_N) };
    inputs.smoke = !!(r.smoke && (val("smoke") || 0) > 0);
    if (r.current && r.contact_temp && r.ambient) {
      const ct = ser("contact_temp", 24), curA = asof(ct, ser("current", 30)), ambA = asof(ct, ser("ambient", 30)), hist = [];
      ct.forEach(([ts, T], i) => { if (curA[i] != null && ambA[i] != null) hist.push([ts, curA[i], T, ambA[i]]); });
      inputs.contact = { current: val("current"), temp: val("contact_temp"), ambient: val("ambient"), history: hist };
    }
    if (r.ambient && r.humidity) {
      let surf = val("ambient");
      if (val("contact_temp") != null && (surf == null || val("contact_temp") < surf)) surf = val("contact_temp");
      const hum = ser("humidity", 24), tA = asof(hum, ser("ambient", 30)), dh = [];
      hum.forEach(([ts, rh], i) => { if (tA[i] != null) dh.push([ts, tA[i], rh, tA[i]]); });
      inputs.dew = { temp: val("ambient"), rh: val("humidity"), surface: surf, history: dh };
    }
    const pending = {};
    for (const [algo, need] of [["fire", GASES], ["contact", ["current", "contact_temp", "ambient"]], ["dew", ["ambient", "humidity"]]]) {
      const pairs = need.filter(x => r[x]).map(x => r[x]);
      if (algo !== "fire" && pairs.length < need.length) { pending[algo] = "센서 없음"; continue; }
      const why = readiness(series, pairs, now);
      if (why) pending[algo] = why;
    }
    if (inputs.temp && readiness(series, [r.ambient], now) !== null) delete inputs.temp;
    const reps = { fire: r.h2 || r.voc || r.co, contact: r.contact_temp, dew: r.humidity };
    return { inputs, pending, reps };
  }

  // ── 판넬 통합 (predict.Predictor) ───────────────────────
  class Predictor {
    constructor(cfg) { this.cfg = cfg || defaultCfg(); this.autovent = true; this.panels = {}; }
    state(panel) {
      let s = this.panels[panel];
      if (!s) s = this.panels[panel] = { vent: new VentController(this.cfg.fire), dew: new DewActuator(),
        contact: new ContactBaseline(this.cfg.contact), overSince: {} };
      return s;
    }
    // 기준값만 바꾸고 판넬 상태(벤트·학습 기준)는 유지 — cfg 객체를 제자리로 고치므로 컨트롤러가 바로 본다
    reconfigure(params) { applyParams(this.cfg, params); }
    resetContactBaseline(panel) { this.state(panel).contact.reset(); }
    setActuator(panel, actuator, action) {
      const s = this.state(panel);
      if (actuator === "vent") { if (action === "auto") s.vent.clearManual(); else if (action === "open" || action === "close") s.vent.setManual(action === "open"); }
      else if (actuator === "heater" || actuator === "fan") {
        if (action === "auto") s.dew.clearManual();
        else { let h = s.dew.heater, f = s.dew.fan; if (actuator === "heater") h = action === "on"; else f = action === "on"; s.dew.setManual(h, f); }
      }
      return this.actuators(panel);
    }
    actuators(panel) {
      const s = this.state(panel);
      return { vent: { open: s.vent.open, mode: s.vent.manual ? "manual" : "auto" },
        heater: { on: s.dew.heater, mode: s.dew.manual ? "manual" : "auto" },
        fan: { on: s.dew.fan, mode: s.dew.manual ? "manual" : "auto" } };
    }
    assessPanel(panel, now, inputs, pending) {
      pending = pending || {};
      const s = this.state(panel), bl = s.contact;
      const c = inputs.contact || {}, hist = c.history || [];
      let blEvent = null;
      const contact = assessContact(c.current, c.temp, c.ambient, hist, this.cfg.contact,
        bl.ready ? bl.k : null, bl.ready ? null : bl.progress());
      if (!pending.contact && hist.length) {
        if (bl.ready || contact.stage === "normal") { for (const [ts, I, T, Ta] of hist) blEvent = bl.observe(ts, I, T, Ta) || blEvent; }
        else bl.skip(hist[hist.length - 1][0]);
      }
      const sig = { h2: inputs.h2 || {}, voc: inputs.voc || {}, co: inputs.co || {}, temp: inputs.temp || {},
        smoke: !!inputs.smoke, current_abnormal: contact.stage === "danger" && !pending.contact };
      const held = {};
      for (const g of GASES) {
        const v = sig[g].value;
        if (!pending.fire && v != null && v >= this.cfg.fire[g].alarm) { if (s.overSince[g] == null) s.overSince[g] = now; held[g] = now - s.overSince[g]; }
        else delete s.overSince[g];
      }
      sig.over_held = held;
      const fire = assessFire(sig, this.cfg.fire);
      const vAct = (this.autovent && !pending.fire) ? s.vent.step(fire.fri, fire.stage, now) : null;
      const d = inputs.dew || {};
      const dew = assessDew(d.temp, d.rh, d.surface, d.history || [], this.cfg.dew);
      const dAct = pending.dew ? null : s.dew.step(dew.stage, now);
      const out = {
        panel, ts: now,
        fire: { fri: fire.fri, stage: fire.stage, reasons: fire.reasons,
          vent: { open: s.vent.open, mode: s.vent.manual ? "manual" : "auto" }, action: vAct, autovent: this.autovent },
        contact: { stage: contact.stage, delta_t: contact.delta_t, expected: contact.expected, residual: contact.residual,
          residual_slope: contact.residual_slope, k: contact.k, reasons: contact.reasons,
          baseline: { status: bl.status, progress: Math.round(bl.progress() * 1000) / 1000,
            k: bl.k == null ? null : Math.round(bl.k * 1e5) / 1e5, learned_at: bl.learned_at }, baseline_event: blEvent },
        dew: { stage: dew.stage, dew_point: dew.dew_point, margin: dew.margin, margin_slope: dew.margin_slope, rh: dew.rh,
          heater: s.dew.heater, fan: s.dew.fan, mode: s.dew.manual ? "manual" : "auto", action: dAct, reasons: dew.reasons },
      };
      for (const k in pending) {
        const o = out[k]; if (!o) continue;
        o.stage = "pending"; o.reasons = [pending[k]];
        ["fri", "residual", "margin"].forEach(f => { if (f in o) o[f] = null; });
      }
      return out;
    }
  }

  root.JCCPredict = {
    ramp, clamp01, slopePerMin, robustZ, dewPoint, MIN_BASELINE,
    defaultCfg, applyParams, cfgFromParams,
    assessFire, assessContact, assessDew, fitK, ContactBaseline, VentController, DewActuator,
    despike, asof, currentValue, readiness, buildPanelInputs, Predictor,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
