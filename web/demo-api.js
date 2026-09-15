/* JCC-CCM 데모용 인-브라우저 백엔드.
 *
 * 왜 있나: Vercel 같은 정적 호스팅은 '항상 켜져 있는 서버'를 못 돌린다. 그런데 시연은
 * 실제 CCM 없이 시뮬레이션으로 돌아가므로, 그 시뮬레이션을 브라우저 안에서 돌리면
 * 서버가 아예 필요 없다 → 영구 주소 + 콜드스타트 없음 + 무료.
 *
 * 방식: fetch()를 가로채 서버 API와 똑같은 응답을 만든다. 화면 코드(index.html)는
 * 한 줄도 고치지 않는다 — 실서버에 붙일 때도 같은 화면을 그대로 쓴다.
 *
 * ⚠ 이 파일은 시연 전용이다. 실제 현장 운영은 server/(파이썬)가 담당한다.
 *   로직이 갈라지지 않도록, 판정 기준은 server/jcc_server/storage.py와 같게 유지한다.
 */
(function () {
  "use strict";

  // ── 제품 프로파일 (firmware/jcc_ccm/discovery/profiles.py와 동일) ──────────
  const PROFILES = {
    "TURCK-CCM-AMBIENT": {
      brand: "Turck", product: "IM18-CCM50 내장 온습도", part_no: "100022405", photo: "",
      manual: "CCM 내장 센서. 별도 배선 없이 함내 온습도를 측정. 보정 불필요.",
      emits: [
        { key: "cabinet_temp", name: "함내 온도", unit: "C", kind: "temp", alarm_min: 5, alarm_warn: 38, alarm_max: 45 },
        { key: "cabinet_humidity", name: "함내 습도", unit: "%RH", kind: "humidity", alarm_min: 20, alarm_warn: 70, alarm_max: 80 },
      ],
    },
    "TURCK-CCM-DOOR": {
      brand: "Turck", product: "IM18-CCM50 내장 거리센서", part_no: "100022405", photo: "",
      manual: "CCM 내장 ToF 거리센서. 도어 개폐를 거리(mm)로 감지. 닫힘 기준거리 캘리브레이션.",
      emits: [{ key: "door_distance", name: "도어 개폐", unit: "mm", kind: "door", alarm_min: 0, alarm_warn: 200, alarm_max: 300 }],
    },
    "BANNER-QM30VT": {
      brand: "Banner Engineering", product: "QM30VT2 진동·온도 센서", part_no: "806276", photo: "",
      manual: "2m 케이블. RMS 속도(mm/s)와 온도 출력. 설치축·감도 파라미터로 설정. 베어링·모터 이상진동 감시용.",
      emits: [{ key: "vibration", name: "진동", unit: "mm/s", kind: "vibration", alarm_min: 0, alarm_warn: 2.5, alarm_max: 4.0 }],
    },
    "BANNER-CT20A": {
      brand: "Banner Engineering", product: "S15C-CT20A-MQ 전류센서", part_no: "814928", photo: "",
      manual: "20A CT 관통형. 메인차단기 전류를 비접촉 측정. 정격 20A, 관통 전선 1가닥.",
      emits: [{ key: "main_current", name: "메인차단기 전류", unit: "A", kind: "current", alarm_min: 0, alarm_warn: 25, alarm_max: 30 }],
    },
    "BANNER-S15S-T": {
      brand: "Banner Engineering", product: "S15S-T-MQ 비접촉 온도센서", part_no: "813163", photo: "",
      manual: "적외선 비접촉 온도. 대상 방사율 설정 필요. 접점·단자 발열 감시용.",
      emits: [{ key: "ncontact_temp", name: "비접촉 온도", unit: "C", kind: "temp", alarm_min: 5, alarm_warn: 50, alarm_max: 60 }],
    },
    "INFRASENSING-H2": {
      brand: "InfraSensing", product: "Hydrogen (H2) Sensor", part_no: "H2-LEL",
      photo: "img/infrasensing-h2.png",
      manual: "0–100% LEL 수소 감지(보정 불요형). ESS 화재 전조. 4~20mA. 정기 기능시험 권장. " +
              "릴레이 3개(A·B 가스경보 / C 센서고장). C는 Fail-safe라 정전 시에도 고장으로 감지된다.",
      relays: [
        { id: "A", role: "가스 경보(위험)", trigger: "gas", setpoint: 25, unit: "%LEL", mode: "NO", failsafe: false },
        { id: "B", role: "가스 경보(경고)", trigger: "gas", setpoint: 10, unit: "%LEL", mode: "NO", failsafe: false },
        { id: "C", role: "센서 고장 감지", trigger: "fault", setpoint: null, unit: "", mode: "NO", failsafe: true },
      ],
      emits: [{ key: "h2_lel", name: "수소 농도", unit: "%LEL", kind: "h2", alarm_min: 0, alarm_warn: 10, alarm_max: 25 }],
    },
    "ONOFF-HSD200": {
      brand: "온오프시스템", product: "HSD200 열·연기 감지기", part_no: "HSD200", photo: "",
      manual: "열+연기 복합 감지. 접점 출력. 천장부 설치. 정기 청소·시험.",
      emits: [{ key: "smoke", name: "열연기", unit: "", kind: "smoke", alarm_min: 0, alarm_max: 1 }],
    },
  };
  const CCM_PHOTO = "img/turck-ccm50.png";
  const LABEL = {
    "TURCK-CCM-AMBIENT": "내장 온습도", "TURCK-CCM-DOOR": "내장 거리센서",
    "BANNER-QM30VT": "Banner QM30VT", "BANNER-CT20A": "Banner CT20A",
    "BANNER-S15S-T": "Banner S15S-T", "INFRASENSING-H2": "InfraSensing H2",
    "ONOFF-HSD200": "온오프 HSD200",
  };

  // 스마트 판넬 1개 = CCM 4대 (scanner.py의 _SIM_PANEL과 동일)
  const SIM = {
    panel: "panel-01", panel_name: "스마트 판넬", site: "인터배터리 데모",
    ccms: [
      { device_id: "ccm-2661", bus: [
        { ident: "INFRASENSING-H2", address: 1 }, { ident: "BANNER-CT20A", address: 2 },
        { ident: "UNKNOWN", address: 5, probe: 41.5 }] },
      { device_id: "ccm-2662", bus: [
        { ident: "TURCK-CCM-AMBIENT", builtin: true }, { ident: "TURCK-CCM-DOOR", builtin: true }] },
      { device_id: "ccm-2663", bus: [
        { ident: "TURCK-CCM-AMBIENT", builtin: true }, { ident: "BANNER-QM30VT", address: 3 }] },
      { device_id: "ccm-2664", bus: [
        { ident: "ONOFF-HSD200", address: 4 }, { ident: "BANNER-S15S-T", address: 6 }] },
    ],
  };

  // ── 상태 ────────────────────────────────────────────────────────────────
  const S = {
    discovered: {},   // dev -> [sensor...]
    lastSeen: {},     // dev -> ts
    readings: {},     // "dev:key" -> [{value, ok, ts}]
    events: [],       // 최신이 앞
    alarms: [],       // {id, device_id, sensor_key, kind, detail, severity, raised_at, acked_at, acked_by, escalated_at, cleared_at}
    settings: {},     // "dev:key" -> {setpoint, alarm_min, alarm_max, alarm_warn, relay_modes}
    panelNames: {},
    alarmState: {},   // "dev:key" -> ok|warn|alarm
    liveState: {},    // 키 -> up|down|stuck|drift|anomaly
    seq: 1, t0: Date.now() / 1000, started: false,
  };
  const SEV = { alarm: "crit", silent: "crit", anomaly: "warn", stuck: "warn", drift: "warn", alarm_warn: "warn" };
  const now = () => Date.now() / 1000;
  const K = (d, k) => d + ":" + k;

  function logEvent(dev, key, etype, detail, source) {
    S.events.unshift({ ts: now(), device_id: dev || "", sensor_key: key || "", etype, detail, source: source || "system" });
    if (S.events.length > 800) S.events.length = 800;
  }
  function openAlarm(dev, key, kind, detail) {
    if (S.alarms.some(a => !a.cleared_at && a.device_id === dev && a.sensor_key === key && a.kind === kind)) return;
    S.alarms.push({ id: S.seq++, device_id: dev, sensor_key: key, kind, detail,
      severity: SEV[kind] || "warn", raised_at: now(), acked_at: null, acked_by: null,
      escalated_at: null, cleared_at: null });
  }
  function closeAlarm(dev, key, kind) {
    S.alarms.forEach(a => { if (!a.cleared_at && a.device_id === dev && a.sensor_key === key && a.kind === kind) a.cleared_at = now(); });
  }
  function raise(dev, key, kind, detail) { openAlarm(dev, key, kind, detail); logEvent(dev, key, kind, detail); }
  function clear(dev, key, kind, clearType, detail) { closeAlarm(dev, key, kind); logEvent(dev, key, clearType, detail); }

  // ── 자동 탐색 ────────────────────────────────────────────────────────────
  function sourceLabel(raw) {
    const nm = LABEL[raw.ident] || raw.ident || "미상";
    if (raw.builtin) return "내장 · " + nm;
    return raw.address != null ? `Modbus #${raw.address} · ${nm}` : nm;
  }
  function infer(raw) {
    const v = raw.probe, key = "unknown_" + (raw.address != null ? raw.address : "x");
    let guess = "미확인 신호", unit = "", kind = "unknown", amax = 100;
    if (v == null) { /* 기본값 */ }
    else if (v >= 0 && v <= 1.5) { guess = "미확인 (가스/비율 추정)"; unit = "%"; kind = "ratio"; amax = 2.0; }
    else if (v >= 15 && v <= 35) { guess = "미확인 (온도 추정)"; unit = "C"; kind = "temp"; amax = 45; }
    else if (v >= 0 && v <= 100) { guess = "미확인 (압력/비율 추정)"; unit = "?"; kind = "pressure"; amax = 100; }
    else { guess = "미확인 (전류/전압 추정)"; unit = "?"; kind = "analog"; amax = 100; }
    return { key, name: guess, unit, kind, alarm_min: 0, alarm_max: amax, alarm_warn: null,
      brand: "미상", product: "미확인 장비", part_no: "", photo: "", relays: [],
      manual: "라이브러리에 없는 장비. 값 범위로 종류를 추정함. 실기에서 모델 확인 후 프로파일 등록 권장." };
  }
  function runDiscover() {
    S.discovered = {};
    for (const c of SIM.ccms) {
      const out = [];
      for (const raw of c.bus) {
        const prof = PROFILES[raw.ident];
        const src = sourceLabel(raw);
        if (prof) {
          for (const e of prof.emits) {
            out.push(Object.assign({}, e, {
              brand: prof.brand, product: prof.product, part_no: prof.part_no,
              manual: prof.manual, photo: prof.photo || "",
              relays: JSON.parse(JSON.stringify(prof.relays || [])),
              source: src, confidence: "확정", address: raw.address != null ? raw.address : null,
              enabled: true,
            }));
          }
        } else {
          out.push(Object.assign(infer(raw), { source: src, confidence: "추정",
            address: raw.address != null ? raw.address : null, enabled: true }));
        }
      }
      S.discovered[c.device_id] = out;
      if (S.lastSeen[c.device_id] == null) S.lastSeen[c.device_id] = 0;
    }
    S.started = true;
    const all = Object.values(S.discovered).flat();
    const inferred = all.filter(s => s.confidence === "추정").length;
    logEvent("", "", "discover", `자동 탐색: CCM ${SIM.ccms.length}대 · 센서 ${all.length}개`);
    return { ok: true, panel_name: SIM.panel_name, ccms: SIM.ccms.length,
      sensors: all.length, identified: all.length - inferred, inferred };
  }

  // ── 값 생성 (demo.py와 같은 패턴) ────────────────────────────────────────
  const SPIKE = { h2: 28, current: 33, vibration: 5.2, temp: 47, humidity: 88 };
  const ANOM = { h2: 6, current: 22, vibration: 2.2, temp: 36, humidity: 66 };
  const drop = {}, stuckU = {}, stuckV = {}, anomU = {}, anomB = {};
  const rnd = () => Math.random();
  const gauss = (m, s) => m + s * (Math.sqrt(-2 * Math.log(rnd() || 1e-9)) * Math.cos(2 * Math.PI * rnd()));
  function genValue(key, kind, t) {
    if (rnd() < 0.03) return null;
    let v;
    if (key.includes("temp") || kind === "temp") v = 27 + 3 * Math.sin(t / 30) + (rnd() - 0.5) * 0.6;
    else if (key.includes("humid") || kind === "humidity") v = 45 + 8 * Math.sin(t / 45) + (rnd() - 0.5) * 2;
    else if (key.includes("vibration") || kind === "vibration") v = Math.max(0, gauss(1.2, 0.4));
    else if (key.includes("door") || kind === "door") v = rnd() > 0.1 ? 12 : 340;
    else if (key.includes("h2") || kind === "h2") v = Math.max(0, gauss(0.4, 0.2));
    else if (key.includes("current") || kind === "current") v = 18 + 4 * Math.sin(t / 20) + (rnd() - 0.5);
    else v = Math.max(0, gauss(40, 3));
    if (rnd() < 0.06) {
      for (const k in SPIKE) if (key.includes(k) || kind === k) return SPIKE[k];
    }
    return Math.round(v * 100) / 100;
  }

  function effThresholds(dev, key, s) {
    const us = S.settings[K(dev, key)] || {};
    const pick = (a, b) => (a != null ? a : b);
    return [pick(us.alarm_min, s.alarm_min), pick(us.alarm_max, s.alarm_max), pick(us.alarm_warn, s.alarm_warn)];
  }

  // 한 주기: 각 CCM이 값을 올린다 + 2단계 경보 판정
  function tickFeed() {
    if (!S.started) return;
    const t = now() - S.t0, wall = now();
    for (const dev in S.discovered) {
      let sent = 0;
      for (const s of S.discovered[dev]) {
        if (!s.enabled) continue;
        const sk = K(dev, s.key);
        if (wall < (drop[sk] || 0)) continue;                 // 침묵 구간
        if (rnd() < 0.012) { drop[sk] = wall + 70; continue; } // 침묵 시작
        let val = genValue(s.key, s.kind, t);
        if (wall < (stuckU[sk] || 0)) val = stuckV[sk];        // 고착
        else if (wall < (anomU[sk] || 0)) val = Math.round(anomB[sk] * (1 + (rnd() - 0.5) * 0.06) * 100) / 100;
        else if (rnd() < 0.008) {
          for (const k in ANOM) if (s.key.includes(k) || s.kind === k) { anomU[sk] = wall + 40; anomB[sk] = ANOM[k]; val = ANOM[k]; break; }
        } else if (rnd() < 0.01) { stuckU[sk] = wall + 50; stuckV[sk] = val; }

        const arr = S.readings[sk] || (S.readings[sk] = []);
        arr.push({ value: val, ok: val != null ? 1 : 0, ts: wall });
        if (arr.length > 300) arr.shift();
        sent++;

        if (val == null) continue;
        const [amin, amax, awarn] = effThresholds(dev, s.key, s);
        let st = "ok";
        if ((amin != null && val < amin) || (amax != null && val > amax)) st = "alarm";
        else if (awarn != null && val > awarn) st = "warn";
        const prev = S.alarmState[sk] || "ok";
        if (st !== prev) {
          S.alarmState[sk] = st;
          const u = s.unit || "";
          closeAlarm(dev, s.key, "alarm"); closeAlarm(dev, s.key, "alarm_warn");
          if (st === "alarm") {
            const lim = (amax != null && val > amax) ? `${amax} 초과` : `${amin} 미만`;
            const d = `${s.name} ${val}${u} — 위험(${lim})`;
            openAlarm(dev, s.key, "alarm", d); logEvent(dev, s.key, "alarm", d);
          } else if (st === "warn") {
            const d = `${s.name} ${val}${u} — 경고(${awarn} 초과)`;
            openAlarm(dev, s.key, "alarm_warn", d); logEvent(dev, s.key, "alarm_warn", d);
          } else {
            logEvent(dev, s.key, "alarm_clear", `${s.name} ${val}${u} — 정상 복귀`);
          }
        }
      }
      if (sent) S.lastSeen[dev] = wall;
    }
  }

  // ── 감시(침묵·고착·드리프트·이상) ───────────────────────────────────────
  const DEV_TO = 45, SEN_TO = 30, ESC_AFTER = 45;
  function lastTs(dev, key) { const a = S.readings[K(dev, key)]; return a && a.length ? a[a.length - 1].ts : null; }
  function vals(dev, key, n) {
    const a = S.readings[K(dev, key)] || [];
    return a.slice(-n).filter(p => p.ok && p.value != null).map(p => p.value);
  }
  function historyStats(dev, key) {
    const v = vals(dev, key, 30);
    const r = { n: v.length, stuck: false, drift: false, drift_pct: 0 };
    if (v.length < 6) return r;
    const rec = v.slice(-8);
    if (Math.max.apply(null, rec) - Math.min.apply(null, rec) === 0) { r.stuck = true; return r; }
    if (v.length >= 15) {
      const k = Math.floor(v.length / 3), avg = x => x.reduce((p, c) => p + c, 0) / x.length;
      const a = avg(v.slice(0, k)), b = avg(v.slice(k, 2 * k)), c = avg(v.slice(2 * k));
      const scale = Math.max(Math.abs((a + c) / 2), 1e-6), pct = (c - a) / scale;
      if (((a <= b && b <= c) || (a >= b && b >= c)) && Math.abs(pct) > 0.25) {
        r.drift = true; r.drift_pct = Math.round(pct * 1000) / 10;
      }
    }
    return r;
  }
  function baselineStats(dev, key) {
    const v = vals(dev, key, 120);
    const r = { n: v.length, mu: null, sigma: null, z: 0, anomaly: false, direction: "" };
    if (v.length < 24) return r;
    const base = v.slice(0, -3), med = x => { const s = x.slice().sort((p, q) => p - q); return s[Math.floor(s.length / 2)]; };
    const mu = med(base), mad = med(base.map(x => Math.abs(x - mu)));
    let sigma = mad > 0 ? 1.4826 * mad : 0;
    if (!sigma) { const m = base.reduce((p, c) => p + c, 0) / base.length;
      sigma = Math.sqrt(base.reduce((p, c) => p + (c - m) * (c - m), 0) / base.length); }
    const cur = v.slice(-3).reduce((p, c) => p + c, 0) / 3;
    r.mu = Math.round(mu * 1000) / 1000; r.sigma = Math.round(sigma * 1000) / 1000;
    if (sigma > 1e-9) { const z = (cur - mu) / sigma; r.z = Math.round(z * 100) / 100;
      if (Math.abs(z) > 3.5) { r.anomaly = true; r.direction = z > 0 ? "급등" : "급락"; } }
    return r;
  }
  function tickMonitor() {
    if (!S.started) return;
    const t = now();
    for (const dev in S.discovered) {
      const ls = S.lastSeen[dev] || 0, up = ls > 0 && (t - ls) < DEV_TO;
      const dk = "dev:" + dev, dprev = S.liveState[dk];
      if (up) { if (dprev === "down") clear(dev, "", "silent", "recovered", `CCM ${dev} 통신 복구`); S.liveState[dk] = "up"; }
      else { if (dprev === "up") { raise(dev, "", "silent", `CCM ${dev} 응답 없음 — ${Math.round(t - ls)}초 침묵`); S.liveState[dk] = "down"; }
             else if (dprev == null) S.liveState[dk] = "down"; }

      for (const s of S.discovered[dev]) {
        if (!s.enabled) continue;
        const lt = lastTs(dev, s.key); if (lt == null) continue;
        const sk = "sen:" + dev + ":" + s.key, sprev = S.liveState[sk];
        if (up && (t - lt) < SEN_TO) {
          if (sprev === "down") clear(dev, s.key, "silent", "recovered", `${s.name} 데이터 복구`);
          S.liveState[sk] = "up";
        } else if (up) {
          if (sprev === "up") { raise(dev, s.key, "silent", `${s.name} ${Math.round(t - lt)}초째 데이터 없음`); S.liveState[sk] = "down"; }
          else if (sprev == null) S.liveState[sk] = "down";
        }
        // 건강(고착·드리프트·이상) — 살아있는 센서만
        if (!up || (t - lt) >= SEN_TO) continue;
        const st = historyStats(dev, s.key);
        let state = "ok", anom = null;
        if (st.stuck) state = "stuck";
        else if (st.drift) state = "drift";
        else {
          const arr = S.readings[K(dev, s.key)] || [];
          const cur = arr.length ? arr[arr.length - 1].value : null;
          const [amin, amax] = effThresholds(dev, s.key, s);
          const within = cur != null && (amin == null || cur >= amin) && (amax == null || cur <= amax);
          if (!within) continue;                       // 임계 초과는 경보가 담당
          const bl = baselineStats(dev, s.key);
          if (bl.anomaly) { state = "anomaly"; anom = bl; }
        }
        const hk = "health:" + dev + ":" + s.key, hprev = S.liveState[hk] || "ok";
        if (state !== hprev) {
          ["stuck", "drift", "anomaly"].forEach(k => closeAlarm(dev, s.key, k));
          if (state === "stuck") raise(dev, s.key, "stuck", `${s.name} 값이 고정됨 — 센서 고착 의심`);
          else if (state === "drift") raise(dev, s.key, "drift", `${s.name} 값 지속 이동(${st.drift_pct > 0 ? "+" : ""}${st.drift_pct}%) — 드리프트 의심`);
          else if (state === "anomaly") raise(dev, s.key, "anomaly", `${s.name} 평소 대비 ${anom.direction}(z=${anom.z}) — 임계 전 조기감지`);
          else if (hprev === "anomaly") logEvent(dev, s.key, "anomaly_clear", `${s.name} 평소 수준 회복`);
          else logEvent(dev, s.key, "stuck_clear", `${s.name} 변동 정상화`);
          S.liveState[hk] = state;
        }
      }
    }
    // 미확인 위험 경보 상향 (주의는 제외 — 알림 피로 방지)
    S.alarms.filter(a => !a.cleared_at && !a.acked_at && !a.escalated_at &&
      a.severity === "crit" && a.raised_at < t - ESC_AFTER).forEach(a => {
      a.escalated_at = t;
      logEvent(a.device_id, a.sensor_key, "escalate", `미확인 경보 상향: ${a.detail}`);
    });
  }

  // ── API 응답 구성 ───────────────────────────────────────────────────────
  function deviceList() {
    const t = now();
    return Object.keys(S.discovered).map(dev => {
      const sensors = S.discovered[dev].map(s => {
        const arr = S.readings[K(dev, s.key)] || [];
        const last = arr.length ? arr[arr.length - 1] : null;
        const us = S.settings[K(dev, s.key)] || {};
        const [amin, amax, awarn] = effThresholds(dev, s.key, s);
        const relays = JSON.parse(JSON.stringify(s.relays || []));
        const modes = us.relay_modes || {};
        relays.forEach(r => { if (modes[r.id]) r.mode = modes[r.id]; });
        return { sensor_key: s.key, name: s.name, unit: s.unit, kind: s.kind,
          source: s.source, confidence: s.confidence, address: s.address, enabled: !!s.enabled,
          value: last ? last.value : null, ok: last ? last.ok : 0, ts: last ? last.ts : null,
          brand: s.brand || "", product: s.product || "", part_no: s.part_no || "",
          manual: s.manual || "", photo: s.photo || "",
          alarm_min: amin, alarm_max: amax, alarm_warn: awarn,
          alarm_min_default: s.alarm_min, alarm_max_default: s.alarm_max, alarm_warn_default: s.alarm_warn,
          setpoint: us.setpoint != null ? us.setpoint : null, relays };
      });
      const ls = S.lastSeen[dev] || 0;
      return { device_id: dev, site: SIM.site, panel: SIM.panel,
        panel_name: S.panelNames[SIM.panel] || SIM.panel_name,
        first_seen: S.t0, last_seen: ls, online: ls > 0 && (t - ls) < 60,
        discovered: true, latest: sensors };
    });
  }
  function panelList() {
    const devs = deviceList();
    if (!devs.length) return [];
    return [{ panel: SIM.panel, panel_name: S.panelNames[SIM.panel] || SIM.panel_name,
      site: SIM.site, ccms: devs, online: devs.some(d => d.online), ccm_count: devs.length }];
  }
  function diagnose(dev, key) {
    const t = now(), d = deviceList().find(x => x.device_id === dev);
    if (!d) return { ok: false, summary: "장치를 찾을 수 없음", checks: [] };
    const checks = [], add = (name, status, detail) => checks.push({ name, status, detail });
    let target = dev;
    if (key) {
      const s = d.latest.find(x => x.sensor_key === key);
      if (!s) return { ok: false, summary: "센서를 찾을 수 없음", checks: [] };
      target = s.name || key;
      add("채널 상태", s.enabled ? "pass" : "warn", s.enabled ? "활성" : "채널이 꺼져 있음(수집 중지)");
      if (s.ts && (t - s.ts) < 30) add("데이터 수신", "pass", `${Math.round(t - s.ts)}초 전 수신`);
      else if (s.ts) add("데이터 수신", "warn", `${Math.round(t - s.ts)}초간 갱신 없음`);
      else add("데이터 수신", s.enabled ? "fail" : "warn", "수신 이력 없음");
      if (s.value != null && s.ok) add("값 유효성", "pass", "정상 측정");
      else if (!s.enabled) add("값 유효성", "warn", "채널 꺼짐으로 값 없음");
      else add("값 유효성", "fail", "값 없음/읽기 오류");
      if (s.value != null && s.alarm_min != null && s.alarm_max != null) {
        if (s.value < s.alarm_min || s.value > s.alarm_max)
          add("측정 범위", "fail", `알람 범위(${s.alarm_min}~${s.alarm_max}) 벗어남: ${s.value}`);
        else add("측정 범위", "pass", `정상 범위(${s.alarm_min}~${s.alarm_max}) 내`);
      }
      const hs = historyStats(dev, key);
      if (hs.stuck) add("변동성", "fail", "값이 고정됨 — 센서 고착/동결 의심");
      else if (hs.drift) add("추세", "warn", `값 지속 이동(${hs.drift_pct > 0 ? "+" : ""}${hs.drift_pct}%) — 드리프트/보정 필요`);
      else if (hs.n >= 6) add("변동성", "pass", "정상 변동");
      const bl = baselineStats(dev, key);
      if (bl.anomaly) add("이상탐지", "warn", `평소(μ=${bl.mu}) 대비 ${bl.direction} — z=${bl.z} (임계 전 조기감지)`);
      else if (bl.mu != null) add("이상탐지", "pass", `학습 평소값 근처 (μ=${bl.mu}, z=${bl.z})`);
      add("장치 식별", s.confidence === "추정" ? "warn" : "pass",
        s.confidence === "추정" ? "추정 장치 — 실기 모델 확인 권장" : "프로파일 확정");
    } else {
      add("연결 상태", d.online ? "pass" : "fail", d.online ? "온라인" : "오프라인 — 접속 없음");
      const tot = d.latest.length, off = d.latest.filter(x => !x.enabled).length;
      const live = d.latest.filter(x => x.ts && (t - x.ts) < 30).length;
      if (!tot) add("센서 응답", "warn", "연결된 센서 없음");
      else if (live === tot - off) add("센서 응답", "pass", `${live}/${tot} 정상 수신`);
      else if (live > 0) add("센서 응답", "warn", `${live}/${tot}만 수신 중`);
      else add("센서 응답", "fail", `${tot}개 중 수신 0`);
      add("채널 상태", off ? "warn" : "pass", off ? `꺼진 채널 ${off}개` : "모든 채널 활성");
      if (d.last_seen) add("마지막 접속", (t - d.last_seen) < 60 ? "pass" : "warn", `${Math.round(t - d.last_seen)}초 전`);
    }
    const worst = checks.some(c => c.status === "fail") ? "fail" : (checks.some(c => c.status === "warn") ? "warn" : "pass");
    const summary = { pass: "정상", warn: "주의 필요", fail: "이상 감지" }[worst];
    logEvent(dev, key || "", "diagnose", "자가진단: " + summary);
    return { ok: true, device_id: dev, sensor_key: key || "", target, status: worst, summary, checks };
  }

  // ── fetch 가로채기 ──────────────────────────────────────────────────────
  const realFetch = window.fetch ? window.fetch.bind(window) : null;
  const J = (obj, status) => new Response(JSON.stringify(obj), {
    status: status || 200, headers: { "Content-Type": "application/json; charset=utf-8" } });

  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    let path = url;
    try { path = new URL(url, location.href).pathname + new URL(url, location.href).search; } catch (e) {}
    const method = ((init && init.method) || (input && input.method) || "GET").toUpperCase();
    let body = {};
    try { if (init && init.body) body = JSON.parse(init.body); } catch (e) {}
    const qs = new URLSearchParams((path.split("?")[1] || ""));
    const p = path.split("?")[0];

    // 데모가 처리하지 않는 주소(이미지 등)는 원래 fetch로
    if (!p.startsWith("/api/") && p !== "/health" && p !== "/v1/telemetry") {
      return realFetch ? realFetch(input, init) : Promise.reject(new Error("no fetch"));
    }
    try {
      if (p === "/health") return Promise.resolve(J({ ok: true, ts: now() }));
      if (p === "/api/panels") return Promise.resolve(J({ panels: panelList() }));
      if (p === "/api/devices") return Promise.resolve(J({ devices: deviceList() }));
      if (p === "/api/alarms")
        return Promise.resolve(J({ alarms: S.alarms.filter(a => !a.cleared_at).sort((a, b) => b.raised_at - a.raised_at).slice(0, 200) }));
      if (p === "/api/notify/status") return Promise.resolve(J({ channels: [] }));
      if (p === "/api/events") {
        const dev = qs.get("device_id") || "", key = qs.get("sensor_key") || "";
        const lim = Math.min(parseInt(qs.get("limit") || "50", 10) || 50, 500);
        let ev = S.events;
        if (dev) ev = ev.filter(e => e.device_id === dev);
        if (key) ev = ev.filter(e => e.sensor_key === key);
        return Promise.resolve(J({ events: ev.slice(0, lim) }));
      }
      const mh = p.match(/^\/api\/devices\/([^/]+)\/history$/);
      if (mh) {
        const sensor = qs.get("sensor") || "";
        if (!sensor) return Promise.resolve(J({ error: "sensor 파라미터가 필요합니다" }, 400));
        const lim = Math.min(parseInt(qs.get("limit") || "200", 10) || 200, 5000);
        const arr = (S.readings[K(mh[1], sensor)] || []).slice(-lim);
        return Promise.resolve(J({ device_id: mh[1], sensor, points: arr }));
      }
      if (method === "POST") {
        if (p === "/api/discover") return Promise.resolve(J(runDiscover()));
        if (p === "/api/diagnose") return Promise.resolve(J(diagnose(String(body.device_id || ""), String(body.sensor_key || ""))));
        if (p === "/api/alarm/ack") {
          const a = S.alarms.find(x => x.id === Number(body.alarm_id) && !x.cleared_at && !x.acked_at);
          if (a) { a.acked_at = now(); a.acked_by = String(body.by || "operator");
            logEvent(a.device_id, a.sensor_key, "ack", `경보 확인(${a.acked_by}): ${a.detail}`, "user"); }
          return Promise.resolve(J({ ok: !!a, alarm_id: Number(body.alarm_id) }));
        }
        if (p === "/api/channel") {
          const dev = String(body.device_id || ""), key = String(body.sensor_key || ""), en = !!body.enabled;
          const s = (S.discovered[dev] || []).find(x => x.key === key);
          if (s) { s.enabled = en;
            if (!en) { S.alarms.forEach(a => { if (!a.cleared_at && a.device_id === dev && a.sensor_key === key) a.cleared_at = now(); });
              delete S.alarmState[K(dev, key)]; delete S.liveState["sen:" + dev + ":" + key]; delete S.liveState["health:" + dev + ":" + key]; }
            logEvent(dev, key, en ? "channel_on" : "channel_off", en ? "센서 켜기" : "센서 끄기", "user"); }
          return Promise.resolve(J({ ok: !!s, device_id: dev, sensor_key: key, enabled: en }));
        }
        if (p === "/api/channels/enable-all") {
          let n = 0;
          for (const dev in S.discovered) S.discovered[dev].forEach(s => { if (!s.enabled) { s.enabled = true; n++; } });
          return Promise.resolve(J({ ok: true, enabled: n }));
        }
        if (p === "/api/setting") {
          const dev = String(body.device_id || ""), key = String(body.sensor_key || "");
          const cur = S.settings[K(dev, key)] || (S.settings[K(dev, key)] = {});
          const norm = v => (v == null ? undefined : (v === "" || v === "null" ? null : (isNaN(parseFloat(v)) ? null : parseFloat(v))));
          ["setpoint", "alarm_min", "alarm_max", "alarm_warn"].forEach(f => {
            const v = norm(body[f]); if (v !== undefined) cur[f] = v;
          });
          if (body.relay_modes && typeof body.relay_modes === "object") cur.relay_modes = body.relay_modes;
          logEvent(dev, key, "setting", `셋팅=${cur.setpoint} 경고=${cur.alarm_warn} 위험=${cur.alarm_min}~${cur.alarm_max}`, "user");
          return Promise.resolve(J(Object.assign({ ok: true, device_id: dev, sensor_key: key }, cur)));
        }
        if (p === "/api/device/command") {
          const dev = String(body.device_id || ""), act = String(body.action || "");
          if (!dev || (act !== "restart" && act !== "shutdown"))
            return Promise.resolve(J({ error: "device_id와 action(restart|shutdown)이 필요합니다" }, 400));
          S.lastSeen[dev] = 0;
          logEvent(dev, "", act, act === "restart" ? "재시작 명령" : "전원 끄기 명령", "user");
          return Promise.resolve(J({ ok: true, device_id: dev, action: act }));
        }
        if (p === "/api/panel/name") {
          const panel = String(body.panel || "").trim(), name = String(body.name || "").trim().slice(0, 60);
          if (!panel) return Promise.resolve(J({ error: "panel이 필요합니다" }, 400));
          if (name) S.panelNames[panel] = name; else delete S.panelNames[panel];
          logEvent("", "", "rename", `판넬 이름 변경: ${panel} → ${name || "(기본값으로 복귀)"}`, "user");
          return Promise.resolve(J({ ok: true, panel, name }));
        }
        if (p === "/api/notify/test")
          return Promise.resolve(J({ ok: false, channels: [],
            error: "데모 화면에서는 실제 알림을 보내지 않습니다. 카카오 알림톡·문자는 실서버에서 동작합니다." }, 400));
        if (p === "/v1/telemetry") return Promise.resolve(J({ ok: true, stored: 0 }));
      }
    } catch (e) {
      return Promise.resolve(J({ error: String(e && e.message || e) }, 500));
    }
    return Promise.resolve(J({ error: "not found" }, 404));
  };

  // 시뮬레이션 구동 — 탐색 전에는 아무것도 만들지 않는다(첫 화면은 빈 화면).
  setInterval(tickFeed, 2000);
  setInterval(tickMonitor, 8000);
  console.log("[JCC-CCM] 데모 모드: 브라우저 안에서 시뮬레이션이 돕니다 (서버 없음).");
})();
