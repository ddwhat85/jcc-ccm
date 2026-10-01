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
    "SENSIRION-VOC": {
      brand: "Sensirion", product: "SGP41 VOC 가스센서", part_no: "SGP41", photo: "",
      manual: "휘발성 유기화합물(VOC) 감지. 리튬셀 오프가스·전해액 증발의 조기 서명. " +
              "H2와 동반 상승하면 열폭주 전조. I2C, 정기 자동 보정.",
      emits: [{ key: "voc_ppm", name: "VOC 농도", unit: "ppm", kind: "voc", alarm_min: 0, alarm_warn: 200, alarm_max: 1000 }],
    },
    "GENERIC-CO": {   // ⚠ 모델 미확정 — 범용 CO 트랜스미터 자리(profiles.py와 동일)
      brand: "미정", product: "CO(일산화탄소) 가스 트랜스미터 — 모델 미확정", part_no: "", photo: "",
      manual: "전기화학식 CO 센서(0~1000ppm). 리튬셀 열폭주 오프가스·절연물 탄화의 서명. " +
              "H2·VOC와 동반 상승하면 열폭주 전조. 센서 수명(보통 2~3년) 주기 교체.",
      emits: [{ key: "co_ppm", name: "CO 농도", unit: "ppm", kind: "co", alarm_min: 0, alarm_warn: 50, alarm_max: 200 }],
    },
  };
  const CCM_PHOTO = "img/turck-ccm50.png";
  const LABEL = {
    "TURCK-CCM-AMBIENT": "내장 온습도", "TURCK-CCM-DOOR": "내장 거리센서",
    "BANNER-QM30VT": "Banner QM30VT", "BANNER-CT20A": "Banner CT20A",
    "BANNER-S15S-T": "Banner S15S-T", "INFRASENSING-H2": "InfraSensing H2",
    "ONOFF-HSD200": "온오프 HSD200", "SENSIRION-VOC": "Sensirion VOC", "GENERIC-CO": "CO 트랜스미터(미정)",
  };

  // 스마트 판넬 1개 = CCM 4대 (scanner.py의 _SIM_PANEL과 동일)
  const SIM = {
    panel: "panel-01", panel_name: "스마트 판넬", site: "인터배터리 데모",
    ccms: [
      { device_id: "ccm-2661", bus: [
        { ident: "INFRASENSING-H2", address: 1 }, { ident: "SENSIRION-VOC", address: 7 },
        { ident: "GENERIC-CO", address: 8 },
        { ident: "BANNER-CT20A", address: 2 },
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
    poweredOff: {},   // dev -> true  (전원 끄기 명령을 받은 CCM은 값을 안 올린다)
    alarmState: {},   // "dev:key" -> ok|warn|alarm
    liveState: {},    // 키 -> up|down|stuck|drift|anomaly
    seq: 1, eseq: 1, t0: Date.now() / 1000, started: false,
  };
  const SEV = { alarm: "crit", silent: "crit", anomaly: "warn", stuck: "warn", drift: "warn", alarm_warn: "warn",
                fire: "crit", contact: "crit", dew: "warn", actuator_fault: "crit" };
  const now = () => Date.now() / 1000;
  const K = (d, k) => d + ":" + k;

  function logEvent(dev, key, etype, detail, source) {
    S.events.unshift({ id: S.eseq++, ts: now(), device_id: dev || "", sensor_key: key || "", etype, detail, source: source || "system" });
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
      S.discovered[c.device_id] = out.concat(MANUAL.list.filter(m => m.device_id === c.device_id).map(manualNode));   // 다시 찾아도 직접 지정 센서 유지
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
  const drop = {}, stuckU = {}, stuckV = {}, anomU = {}, anomB = {}, ccmDrop = {};

  // ── 예지보전 에피소드 (demo.py 미러) ────────────────────────────────────────
  // 드물게 화재징조/접점발열/결로가 임계 前에 서서히 진행 → 엔진이 벤트/히터·팬 자동 작동.
  let EPI = { kind: null, t0: 0, until: 0 };
  const EPI_DUR = 90;
  function epiOverride(wall, key, kind, val) {
    const k = EPI.kind;
    if (!k || wall > EPI.until) return null;
    const prog = Math.min(1, (wall - EPI.t0) / 60);          // 60초에 걸쳐 상승
    const j = (rnd() - 0.5) * 2;                             // 실센서 잡음(고정값이면 고착 오탐)
    if (k === "fire") {
      if (kind === "h2") return Math.round((0.5 + 17 * prog + 0.15 * j) * 100) / 100;  // 0.5→~17.5 %LEL
      if (kind === "voc") return Math.round((30 + 770 * prog + 5 * j) * 10) / 10;       // 30→~800 ppm
      if (kind === "co") return Math.round((3 + 147 * prog + j) * 10) / 10;              // 3→~150 ppm
      if (kind === "temp" && !key.includes("ncontact")) return Math.round(((val != null ? val : 27) + 9 * prog) * 100) / 100;  // 평소값 위에 얹음(계단 방지)
    } else if (k === "contact") {
      if (key.includes("ncontact")) return Math.round((27 + 24 * prog + 0.2 * j) * 100) / 100;  // 접점온도만 상승
    } else if (k === "dew") {
      if (kind === "humidity") return Math.round(Math.min(99, 55 + 40 * prog + 0.4 * j) * 10) / 10;  // 55→~95 %RH
    }
    return null;
  }

  // ── 자가치유 L1(채널)·L2(CCM) — server/jcc_server/heal.py 미러 ───────────────
  const HEAL = { enabled: true, l1: true, max: 2, cooldown: 60, dailyCap: 30, targets: ["silent", "stuck"],
                 l2: true, l2max: 1, l2cooldown: 180, l2dailyCap: 10, l2onChannelFail: true };
  const healAt = {};                 // "dev:key" -> 채널 재시작 시각(피더가 일시장애 해제)
  const healDevAt = {};              // dev -> CCM 재시작 시각(피더가 CCM 침묵 해제)
  const healRec = {};                // "dev:key" -> {n,last,episode,gaveup}
  const healDevRec = {};             // dev -> {n,last,episode,gaveup,reason}
  let healDay = null, healDayN = 0, healDayN2 = 0;
  function restartChannel(dev, key, note) {
    healAt[K(dev, key)] = now();
    logEvent(dev, key, "heal_restart", note, "system");
  }
  function restartDevice(dev, note) {
    healDevAt[dev] = now();
    logEvent(dev, "", "heal2_restart", note, "system");
  }
  function healTick() {
    if (!HEAL.enabled) return;
    const t = now(), day = Math.floor(t / 86400);
    if (day !== healDay) { healDay = day; healDayN = 0; healDayN2 = 0; }
    const active = S.alarms.filter(a => !a.cleared_at);

    // ── L1: 센서 채널 재시작 ──
    const seen = {};
    for (const a of (HEAL.l1 ? active : [])) {
      const key = a.sensor_key;
      if (!key || HEAL.targets.indexOf(a.kind) < 0) continue;   // 센서 채널 장애만
      const sk = K(a.device_id, key); seen[sk] = 1;
      let rec = healRec[sk];
      if (!rec || rec.episode !== a.raised_at) { rec = healRec[sk] = { n: 0, last: 0, episode: a.raised_at, gaveup: false }; }
      if (a.acked_at) continue;                                 // 사람이 조치 중 → 보류
      if (rec.n >= HEAL.max) {
        if (!rec.gaveup) { rec.gaveup = true;
          logEvent(a.device_id, key, "heal_giveup",
            `자동복구 실패: 채널 재시작 ${HEAL.max}회로 복구 안 됨` +
            ((HEAL.l2 && HEAL.l2onChannelFail) ? " — CCM 재시작으로 승격" : " — 사람 확인 필요"), "system"); }
        continue;
      }
      if (t - rec.last < HEAL.cooldown) continue;               // 쿨다운
      if (healDayN >= HEAL.dailyCap) continue;                  // 하루 총량
      rec.n++; rec.last = t; healDayN++;
      restartChannel(a.device_id, key, `자동복구 L1: 채널 재시작 ${rec.n}/${HEAL.max}차 시도 (${a.kind})`);
    }
    if (HEAL.l1) for (const sk in healRec) {
      if (!seen[sk]) { const rec = healRec[sk]; delete healRec[sk];
        if (rec.n > 0 && !rec.gaveup) {
          const i = sk.indexOf(":");
          logEvent(sk.slice(0, i), sk.slice(i + 1), "heal_ok", "자동복구 성공: 채널 재시작 후 정상 복귀", "system");
        }
      }
    }

    // ── L2: CCM 재시작 ──
    if (!HEAL.l2) return;
    const targets = {};   // dev -> {reason, episode, acked}
    for (const a of active) {
      if (a.kind === "silent" && !a.sensor_key) targets[a.device_id] = { reason: "silent", episode: a.raised_at, acked: !!a.acked_at };
    }
    if (HEAL.l2onChannelFail) {
      for (const sk in healRec) {
        if (healRec[sk].gaveup) { const dev = sk.slice(0, sk.indexOf(":"));
          if (!targets[dev]) targets[dev] = { reason: "channel_fail", episode: healRec[sk].episode, acked: false }; }
      }
    }
    const seenDev = {};
    for (const dev in targets) {
      const tg = targets[dev]; seenDev[dev] = 1;
      let drec = healDevRec[dev];
      if (!drec || drec.episode !== tg.episode) { drec = healDevRec[dev] = { n: 0, last: 0, episode: tg.episode, gaveup: false, reason: tg.reason }; }
      if (tg.acked) continue;
      if (drec.n >= HEAL.l2max) {
        if (!drec.gaveup) { drec.gaveup = true;
          logEvent(dev, "", "heal2_giveup", `CCM 자동 재시작 ${HEAL.l2max}회로도 복구 안 됨 — 사람 확인 필요`, "system"); }
        continue;
      }
      if (t - drec.last < HEAL.l2cooldown) continue;
      if (healDayN2 >= HEAL.l2dailyCap) continue;
      drec.n++; drec.last = t; healDayN2++;
      const why = tg.reason === "silent" ? "CCM 침묵" : "채널 재시작 실패 → 승격";
      restartDevice(dev, `자동복구 L2: CCM 재시작 ${drec.n}/${HEAL.l2max}차 시도 (${why})`);
    }
    for (const dev in healDevRec) {
      if (!seenDev[dev]) { const drec = healDevRec[dev]; delete healDevRec[dev];
        if (drec.n > 0 && !drec.gaveup) logEvent(dev, "", "heal2_ok", "자동복구 성공: CCM 재시작 후 정상 복귀", "system");
      }
    }
  }
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
    else if (key.includes("voc") || kind === "voc") v = Math.max(0, gauss(30, 10));
    else if (kind === "co" || key.startsWith("co_")) v = Math.max(0, gauss(3, 1));   // ppm, 평소 한 자릿수
    else if (key.includes("current") || kind === "current") v = 18 + 4 * Math.sin(t / 20) + (rnd() - 0.5);
    else if (key.includes("smoke") || kind === "smoke") v = rnd() > 0.02 ? 0 : 1;  // 평소 0, 드물게 감지
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
    // 예지 에피소드 스케줄: 하나 끝나면 쿨다운, 유휴면 드물게 새로 시작.
    if (EPI.kind && wall > EPI.until) { EPI.kind = null; EPI.until = wall + 60; }
    else if (!EPI.kind && wall > EPI.until && rnd() < 0.02) {
      EPI.kind = ["fire", "contact", "dew"][Math.floor(rnd() * 3)];
      EPI.t0 = wall; EPI.until = wall + EPI_DUR;
    }
    for (const dev in S.discovered) {
      if (S.poweredOff[dev]) continue;      // 전원 꺼진 CCM은 아무 값도 안 올린다
      // L2 자가치유가 이 CCM을 재시작했으면 침묵 구간 해제(재시작이 두절을 고침)
      if (healDevAt[dev]) { delete healDevAt[dev]; delete ccmDrop[dev]; }
      // 드물게 CCM 전체가 한동안 침묵(게이트웨이 두절) → watchdog가 CCM 침묵으로 잡고 L2가 복구
      if (wall < (ccmDrop[dev] || 0)) continue;
      if (rnd() < 0.0015) { ccmDrop[dev] = wall + 120; continue; }   // CCM마다 드물게(4대 합쳐 몇 분에 한 번꼴)
      let sent = 0;
      for (const s of S.discovered[dev]) {
        if (!s.enabled) continue;
        const sk = K(dev, s.key);
        if (s.pendingUntil && wall < s.pendingUntil) continue;   // 직접 지정 센서: CCM 반영 전엔 값 없음
        // 자가치유(L1)가 이 채널을 재시작했으면 진행 중이던 일시 장애(침묵·고착·이상)를 해제
        if (healAt[sk]) { delete healAt[sk]; delete drop[sk]; delete stuckU[sk]; delete anomU[sk]; }
        if (wall < (drop[sk] || 0)) continue;                 // 침묵 구간
        if (rnd() < 0.004) { drop[sk] = wall + 70; continue; } // 침묵 시작(드물게 — 잦으면 예지 입력이 자주 끊김)
        let val = genValue(s.key, s.kind, t);
        const ov = epiOverride(wall, s.key, s.kind, val);      // 예지 에피소드 오버라이드(임계 前 상승)
        if (ov != null) val = ov;
        else if (wall < (stuckU[sk] || 0)) val = stuckV[sk];        // 고착
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
      if (S.poweredOff[dev]) continue;      // 일부러 끈 CCM은 침묵 경보로 나무라지 않는다
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
        if (st.stuck && s.kind !== "smoke") state = "stuck";   // 접점(열연기)은 늘 0이 정상 — 고착 판정 제외
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
    // 자가치유 L1: 채널 장애(침묵·고착)는 먼저 자동 재시작으로 복구를 시도한다.
    healTick();
    // 미확인 위험 경보 상향 (주의는 제외 — 알림 피로 방지).
    // 자동복구가 손대는 종류(침묵)는 여기서 상향 로그를 남기지만, 대개 재시작으로 먼저 풀린다.
    S.alarms.filter(a => !a.cleared_at && !a.acked_at && !a.escalated_at &&
      a.severity === "crit" && a.raised_at < t - ESC_AFTER).forEach(a => {
      a.escalated_at = t;
      logEvent(a.device_id, a.sensor_key, "escalate", `미확인 경보 상향: ${a.detail}`);
    });
  }

  // ── 예지보전 (판정 코어는 predict-core.js 한 벌 — 여기엔 데모 상태와의 연결만) ──
  // 판정·입력 조립·컨트롤러는 서버 storage.predict_scan과 같은 흐름(buildPanelInputs → Predictor.assessPanel).
  // 시연 화면은 몇 분 안에 접점 기준 학습 완료를 보여주려고 학습 조건만 짧게 둔다(로직은 동일).
  const JP = window.JCCPredict;
  const DEMO_CFG = JP.defaultCfg();
  DEMO_CFG.contact.learn_samples = 45; DEMO_CFG.contact.learn_span = 60;
  const PREDICTOR = new JP.Predictor(DEMO_CFG);
  const PRED_LAST = {};
  const predAlarmState = {};
  function seriesOf(dev, key, n) {
    return (S.readings[K(dev, key)] || []).slice(-n).filter(p => p.ok && p.value != null).map(p => [p.ts, p.value]);
  }
  function findSensor(pred) {
    for (const dev in S.discovered) for (const s of S.discovered[dev]) {
      if (s.enabled && pred(s)) return [dev, s.key];
    }
    return null;
  }
  // 판넬 역할 → [dev, key] (inputs.roles_from_kinds와 같은 규칙)
  function panelRoles() {
    return {
      h2: findSensor(s => s.kind === "h2"), voc: findSensor(s => s.kind === "voc"), co: findSensor(s => s.kind === "co"),
      current: findSensor(s => s.kind === "current"),
      contact_temp: findSensor(s => (s.key || "").includes("ncontact")),
      ambient: findSensor(s => s.kind === "temp" && !(s.key || "").includes("ncontact")),
      humidity: findSensor(s => s.kind === "humidity"), smoke: findSensor(s => s.kind === "smoke"),
    };
  }
  function predAlarmTransition(dev, key, kind, on, detail, clearDetail) {
    if (!dev) return;
    const sk = dev + ":" + key + ":" + kind, prev = predAlarmState[sk] || false;
    if (on && !prev) { openAlarm(dev, key, kind, detail); logEvent(dev, key, kind, detail, "system"); predAlarmState[sk] = true; }
    else if (!on && prev) { closeAlarm(dev, key, kind); logEvent(dev, key, kind + "_clear", clearDetail, "system"); predAlarmState[sk] = false; }
  }
  function tickPredict() {
    if (!S.started) return;
    const t = now();
    const online = Object.keys(S.discovered).some(dev => !S.poweredOff[dev] && S.lastSeen[dev] && (t - S.lastSeen[dev]) < 60);
    if (!online) return;
    const asm = JP.buildPanelInputs(panelRoles(), seriesOf, t);
    if (!Object.keys(asm.inputs).length) return;
    const pending = asm.pending, reps = {};
    ["fire", "contact", "dew"].forEach(k => { reps[k] = asm.reps[k] || ["", ""]; });
    const res = PREDICTOR.assessPanel("panel-01", t, asm.inputs, pending);
    PRED_LAST["panel-01"] = res;
    const f = res.fire, c = res.contact, d = res.dew;
    if (c.baseline_event === "ready") logEvent(reps.contact[0] || "", "", "baseline",
      `접점 발열 기준 학습 완료 — 이제 서서히 풀리는 접점도 감시 (k=${c.baseline.k})`, "system");
    // 보류 중인 알고리즘은 경보 상태를 건드리지 않는다(끊겼다고 '해소'로 닫지 않음)
    if (!pending.fire) predAlarmTransition(reps.fire[0], reps.fire[1], "fire", f.stage === "danger" || f.stage === "critical",
      `화재 징조 감지 (FRI ${f.fri}) — ` + f.reasons.slice(0, 2).join(" · "), "화재 위험 해소 — 정상 복귀");
    if (!pending.contact) predAlarmTransition(reps.contact[0], reps.contact[1], "contact", c.stage === "danger",
      "접점 발열 위험 — " + c.reasons.slice(0, 2).join(" · "), "접점 발열 정상 복귀");
    if (!pending.dew) predAlarmTransition(reps.dew[0], reps.dew[1], "dew", d.stage === "danger",
      "결로 위험 — " + d.reasons.slice(0, 2).join(" · "), "결로 위험 해소");
    const rd = reps.fire[0] || "";
    if (f.action === "open") logEvent(rd, "", "vent_open", `🔥 화재 징조 → 벤트 자동 개방 (FRI ${f.fri})`, "system");
    else if (f.action === "close") logEvent(rd, "", "vent_close", "환기 완료 → 벤트 자동 닫힘", "system");
    else if (f.action === "hold") logEvent(rd, "", "vent_hold", `극한 위험 유지 — 벤트 개방 유지 (FRI ${f.fri})`, "system");
    const rh2 = reps.dew[0] || "";
    if (d.action === "heater_fan") logEvent(rh2, "", "dew_actuate", "결로 위험 → 히터·팬 가동", "system");
    else if (d.action === "fan") logEvent(rh2, "", "dew_actuate", "습도 상승 → 팬 가동", "system");
    else if (d.action === "off") logEvent(rh2, "", "dew_actuate", "결로 위험 해소 → 히터·팬 정지", "system");
    tickVentGuard(t);
  }
  // 시연: 벤트 고장(걸림) — 화재 징조로 벤트를 열라는데 댐퍼가 닫힌 채 걸린 상황.
  // 현장 CCM의 작동 확인(guard.py)을 흉내 낸다: 작동 시간이 지나도 안 열리면 '작동 실패' 위험 경보.
  const STUCK = { until: 0, since: null, fault: false, dev: "" };
  const STUCK_TRAVEL = 12;          // 시연용으로 짧게(운영 기본 30초 × 재시도)
  function tickVentGuard(t) {
    if (!STUCK.until) return;
    const want = PREDICTOR.actuators("panel-01").vent.open, active = t < STUCK.until;
    STUCK.dev = ((panelRoles().h2) || [""])[0] || STUCK.dev;
    if (active && want) {
      if (STUCK.since == null) STUCK.since = t;
      if (!STUCK.fault && t - STUCK.since >= STUCK_TRAVEL) {
        STUCK.fault = true;
        const d = "벤트 작동 실패 — 명령 개방·실제 닫힘 — 구동기 걸림·배선·전원 확인";
        openAlarm(STUCK.dev, "vent", "actuator_fault", d); logEvent(STUCK.dev, "vent", "actuator_fault", d, "system");
      }
      return;
    }
    STUCK.since = null;
    if (STUCK.fault && !active) {
      STUCK.fault = false; closeAlarm(STUCK.dev, "vent", "actuator_fault");
      logEvent(STUCK.dev, "vent", "actuator_fault_clear", "벤트 작동 정상 확인 — 작동 실패 해소", "system");
    }
    if (!active && !STUCK.fault) STUCK.until = 0;
  }
  function predView() {
    const last = PRED_LAST["panel-01"];
    if (!last) return [];
    const v = JSON.parse(JSON.stringify(last));   // 액추에이터 최신 상태 반영(수동 조작 즉시 표시)
    const a = PREDICTOR.actuators("panel-01");
    v.fire.vent = a.vent;
    v.dew.heater = a.heater.on; v.dew.fan = a.fan.on; v.dew.mode = a.heater.mode;
    if (STUCK.until) {                // 벤트 고장 시연 중: 현장 CCM이 확인한 것처럼 보고
      const conf = STUCK.fault ? "fault" : (a.vent.open && STUCK.since != null ? "moving" : "ok");
      v.edge = { [STUCK.dev || "ccm-demo"]: { age: 0, failsafe: false, actuators: { vent: {
        on: a.vent.open, mode: a.vent.mode, confirm: conf, position: STUCK.fault || conf === "moving" ? "closed" : (a.vent.open ? "open" : "closed"),
        fault: STUCK.fault ? "명령 개방·실제 닫힘 — 구동기 걸림·배선·전원 확인" : "" } } } };
    }
    return [v];
  }
  function setActuator(panel, actuator, action) {
    const acts = PREDICTOR.setActuator(panel, actuator, action);
    const lab = { vent: "벤트", heater: "히터", fan: "팬" }[actuator] || actuator;
    const act = { open: "개방", close: "닫힘", on: "켜기", off: "끄기", auto: "자동복귀" }[action] || action;
    logEvent(panel, "", "actuator", `${lab} 수동 ${act}`, "user");
    return { ok: true, panel, actuators: acts };
  }

  // ── 경보 원인 묶기 (server/jcc_server/incidents.py 미러) ──────────────────
  const INC_PRE = 1800, INC_CCM_SLACK = 60, INC_SEV = { crit: 2, warn: 1 };
  const INC_TITLE = { fire: "화재 징조 — 가스·온도 동반 이상", contact: "접점 발열 — 접점온도 이상", dew: "결로 위험 — 습도 이상" };
  function groupAlarms(alarms, sensors, panels) {
    const items = alarms.map(a => Object.assign({}, a)).sort((a, b) => ((a.raised_at || 0) - (b.raised_at || 0)) || ((a.id || 0) - (b.id || 0)));
    const used = new Set(), out = [];
    const kindOf = a => sensors[(a.device_id || "") + ":" + (a.sensor_key || "")] || "";
    const panelOf = a => panels[a.device_id || ""] || a.device_id || "";
    const make = (cause, p, members, title) => {
      const ids = members.map(m => m.id); ids.forEach(i => used.add(i));
      const sev = members.map(m => m.severity || "warn").reduce((x, y) => (INC_SEV[y] || 0) > (INC_SEV[x] || 0) ? y : x);
      out.push({ key: `${cause}:${p.device_id || ""}:${p.sensor_key || ""}:${p.id}`, cause, title, severity: sev, primary: p.id,
        alarms: ids, count: ids.length, started_at: Math.min(...members.map(m => m.raised_at || 0)),
        acked: members.every(m => !!m.acked_at), device_id: p.device_id || "", sensor_key: p.sensor_key || "" });
    };
    const attach = (root, pred) => { const t0 = (root.raised_at || 0) - INC_PRE;
      return [root].concat(items.filter(a => a.id !== root.id && !used.has(a.id) && (a.raised_at || 0) >= t0 && pred(a))); };
    for (const a of items) {                    // 1) CCM 두절(이후 경보만, 예지 원인은 제외)
      if (used.has(a.id) || a.kind !== "silent" || a.sensor_key) continue;
      const t0 = (a.raised_at || 0) - INC_CCM_SLACK;
      const members = [a].concat(items.filter(b => b.id !== a.id && !used.has(b.id) && b.device_id === a.device_id &&
        (b.raised_at || 0) >= t0 && ["fire", "contact", "dew"].indexOf(b.kind) < 0));
      make("ccm", a, members, `CCM ${a.device_id} 통신 두절 — 그 CCM의 경보는 통신 문제의 결과`);
    }
    const rules = [
      ["fire", (r, b) => panelOf(b) === panelOf(r) && (
        (["h2", "voc", "co"].indexOf(kindOf(b)) >= 0 && ["alarm", "alarm_warn", "anomaly", "drift"].indexOf(b.kind) >= 0) ||
        (kindOf(b) === "temp" && !(b.sensor_key || "").includes("ncontact") && ["anomaly", "drift", "alarm_warn", "alarm"].indexOf(b.kind) >= 0) ||
        (kindOf(b) === "smoke" && b.kind === "alarm") ||
        (b.kind === "actuator_fault" && b.sensor_key === "vent"))],   // 화재 중 벤트 고장(incidents.py와 동일)
      ["contact", (r, b) => b.device_id === r.device_id && b.sensor_key === r.sensor_key && ["alarm", "alarm_warn", "anomaly", "drift"].indexOf(b.kind) >= 0],
      ["dew", (r, b) => panelOf(b) === panelOf(r) && kindOf(b) === "humidity" && ["alarm", "alarm_warn", "anomaly", "drift", "stuck"].indexOf(b.kind) >= 0],
    ];
    for (const [cause, pred] of rules) for (const a of items) {
      if (used.has(a.id) || a.kind !== cause) continue;
      make(cause, a, attach(a, b => pred(a, b)), INC_TITLE[cause]);
    }
    const bySensor = {};                        // 5) 같은 센서의 여러 경보
    for (const a of items) if (!used.has(a.id)) (bySensor[(a.device_id || "") + ":" + (a.sensor_key || "")] ||= []).push(a);
    for (const k in bySensor) {
      const g = bySensor[k];
      const p = g.reduce((x, y) => { const sx = INC_SEV[x.severity || "warn"] || 0, sy = INC_SEV[y.severity || "warn"] || 0;
        return sy > sx || (sy === sx && (y.raised_at || 0) < (x.raised_at || 0)) ? y : x; });
      make("sensor", p, g, p.detail || p.kind || "경보");
    }
    const PRIO = { fire: 0, contact: 1, dew: 2, ccm: 3, sensor: 4 };   // 같은 심각도면 안전 사건 먼저
    out.sort((a, b) => ((INC_SEV[b.severity] || 0) - (INC_SEV[a.severity] || 0)) ||
      ((PRIO[a.cause] ?? 9) - (PRIO[b.cause] ?? 9)) || (b.started_at - a.started_at));
    return out;
  }
  function incidentList(alarms) {
    const sensors = {}, panels = {};
    for (const dev in S.discovered) { panels[dev] = SIM.panel; for (const s of S.discovered[dev]) sensors[dev + ":" + s.key] = s.kind || ""; }
    return groupAlarms(alarms, sensors, panels);
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
  function diagnose(dev, key, quiet) {
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
    if (!quiet) logEvent(dev, key || "", "diagnose", "자가진단: " + summary);
    return { ok: true, device_id: dev, sensor_key: key || "", target, status: worst, summary, checks };
  }

  function diagnoseAll() {
    const counts = { pass: 0, warn: 0, fail: 0 };
    const problems = [];
    let checked = 0;
    const worstDetail = (rep) => {
      const bad = (rep.checks || []).find(c => c.status === "fail")
        || (rep.checks || []).find(c => c.status === "warn");
      return bad ? bad.detail : (rep.summary || "");
    };
    for (const d of deviceList()) {
      let rep = diagnose(d.device_id, "", true);
      counts[rep.status] = (counts[rep.status] || 0) + 1; checked++;
      if (rep.status !== "pass") problems.push({ device_id: d.device_id, sensor_key: "",
        kind: "ccm", target: rep.target, status: rep.status, issue: worstDetail(rep) });
      for (const s of d.latest || []) {
        rep = diagnose(d.device_id, s.sensor_key, true);
        counts[rep.status] = (counts[rep.status] || 0) + 1; checked++;
        if (rep.status !== "pass") problems.push({ device_id: d.device_id, sensor_key: s.sensor_key,
          kind: "sensor", target: rep.target, status: rep.status, issue: worstDetail(rep) });
      }
    }
    const order = { fail: 0, warn: 1 };
    problems.sort((a, b) => (order[a.status] ?? 2) - (order[b.status] ?? 2));
    const overall = counts.fail ? "fail" : (counts.warn ? "warn" : "pass");
    const summary = { pass: "모든 장비 정상", warn: "주의 필요 항목 있음", fail: "이상 장비 있음" }[overall];
    logEvent("", "", "healthcheck", `전체 점검: ${summary} (정상 ${counts.pass}·주의 ${counts.warn}·이상 ${counts.fail})`);
    return { ok: true, checked, counts, overall, summary, problems };
  }

  // ── 기간별 종합 리포트 (storage.build_report 미러) ──────────────────────────
  const PROB_LABEL = { alarm: "위험", alarm_warn: "경고", silent: "침묵", stuck: "고착", drift: "드리프트", anomaly: "이상",
                       fire: "화재 징조", contact: "접점 발열", dew: "결로" };
  function buildReport(days) {
    const t = now();
    days = Math.max(1 / 24, days || 7);
    const since = t - days * 86400;
    const ev = {};                       // etype -> count
    const probMap = {};                  // "dev:key" -> {etype:count}
    for (const e of S.events) {
      if (e.ts < since) continue;
      ev[e.etype] = (ev[e.etype] || 0) + 1;
      if (PROB_LABEL[e.etype]) { const k = K(e.device_id, e.sensor_key);
        (probMap[k] || (probMap[k] = {}))[e.etype] = (probMap[k][e.etype] || 0) + 1; }
    }
    const nameUnit = {};
    let totalSensors = 0;
    for (const dev in S.discovered) for (const s of S.discovered[dev]) {
      nameUnit[K(dev, s.key)] = [s.name || s.key, s.unit || ""]; totalSensors++;
    }
    // 센서별 값 통계
    const sensors = [];
    for (const dev in S.discovered) for (const s of S.discovered[dev]) {
      const arr = (S.readings[K(dev, s.key)] || []).filter(p => p.ts >= since && p.ok && p.value != null);
      if (!arr.length) continue;
      let mn = Infinity, mx = -Infinity, sum = 0;
      for (const p of arr) { if (p.value < mn) mn = p.value; if (p.value > mx) mx = p.value; sum += p.value; }
      const pm = probMap[K(dev, s.key)] || {};
      sensors.push({ device_id: dev, sensor_key: s.key, name: s.name || s.key, unit: s.unit || "",
        count: arr.length, min: Math.round(mn * 100) / 100, avg: Math.round((sum / arr.length) * 100) / 100,
        max: Math.round(mx * 100) / 100, problems: Object.assign({}, pm) });
    }
    sensors.sort((a, b) => a.name.localeCompare(b.name));
    // 문제 하드웨어 Top
    const problems = [];
    for (const k in probMap) { const pm = probMap[k]; const i = k.indexOf(":");
      const dev = k.slice(0, i), key = k.slice(i + 1);
      const nm = nameUnit[k] ? nameUnit[k][0] : (key || dev);
      problems.push({ device_id: dev, sensor_key: key, name: key ? nm : ("CCM " + dev),
        count: Object.values(pm).reduce((a, c) => a + c, 0),
        detail: Object.keys(pm).map(x => `${PROB_LABEL[x] || x} ${pm[x]}`).join(" · ") });
    }
    problems.sort((a, b) => b.count - a.count);
    const crit = (ev.alarm || 0) + (ev.silent || 0) + (ev.fire || 0) + (ev.contact || 0);
    const warn = (ev.alarm_warn || 0) + (ev.stuck || 0) + (ev.drift || 0) + (ev.anomaly || 0) + (ev.dew || 0);
    // 예지보전 성과(storage._predict_report 미러): 화재 징조 뒤 30분 안에 가스가 위험선에 닿았는가
    const gases = [];
    for (const dev in S.discovered) for (const s of S.discovered[dev]) {
      if (["h2", "voc", "co"].indexOf(s.kind) < 0) continue;
      const lim = effThresholds(dev, s.key, s)[1]; if (lim != null) gases.push([dev, s.key, lim]);
    }
    let prevented = 0, reached = 0, watching = 0; const leads = [];
    for (const e of S.events) {
      if (e.etype !== "fire" || e.ts < since) continue;
      let first = null;
      for (const [dev, key, lim] of gases)
        for (const pt of (S.readings[K(dev, key)] || []))
          if (pt.ok && pt.value != null && pt.ts >= e.ts && pt.ts <= e.ts + 1800 && pt.value >= lim && (first == null || pt.ts < first)) first = pt.ts;
      if (first == null) { if (t - e.ts < 1800) watching++; else prevented++; }   // 30분 관찰 전엔 '관찰 중'
      else { reached++; leads.push((first - e.ts) / 60); }
    }
    const predict = {
      fire: { detected: ev.fire || 0, prevented, reached, watching,
        lead_min_avg: leads.length ? Math.round(leads.reduce((a, c) => a + c, 0) / leads.length * 10) / 10 : null,
        vent_auto: ev.vent_open || 0, vent_edge: 0, failsafe: 0 },
      contact: { detected: ev.contact || 0 },
      dew: { detected: ev.dew || 0, actuations: ev.dew_actuate || 0 },
      manual: ev.actuator || 0 };
    const l1ok = ev.heal_ok || 0, l1gu = ev.heal_giveup || 0, l2ok = ev.heal2_ok || 0, l2gu = ev.heal2_giveup || 0;
    const autoFixed = l1ok + l2ok, resolved = l1ok + l1gu + l2ok + l2gu;
    let online = 0;
    for (const dev in S.discovered) if (!S.poweredOff[dev] && S.lastSeen[dev] && (t - S.lastSeen[dev]) < 45) online++;
    return { ok: true, range_days: days, since, generated_at: t,
      summary: {
        devices: { total: Object.keys(S.discovered).length, online },
        sensors: { total: totalSensors },
        alarms: { crit, warn, total: crit + warn },
        acks: ev.ack || 0, escalations: ev.escalate || 0,
        heal: { l1_restart: ev.heal_restart || 0, l1_ok: l1ok, l1_giveup: l1gu,
          l2_restart: ev.heal2_restart || 0, l2_ok: l2ok, l2_giveup: l2gu,
          auto_fixed: autoFixed, success_rate: resolved ? Math.round(100 * autoFixed / resolved) : null },
        predict },
      sensors, problems: problems.slice(0, 8) };
  }

  // ── 튜닝 콘솔 (데모: 서버 대신 브라우저가 같은 관문을 돈다 — 판정 코어는 predict-core.js 한 벌) ──
  // 손잡이 목록·시나리오는 빌드 때 파이썬 정본에서 뽑아 tuning-data.js(window.JCC_TUNING_DATA)로 싣는다.
  const TUNING = { history: [] };
  const tuneData = () => window.JCC_TUNING_DATA || null;
  function tuneDefaultsDemo() { return Object.fromEntries(tuneData().params.params.map(q => [q.key, q.default])); }
  function tuneActiveDemo() {
    return TUNING.history[0] || { version: 0, params: tuneDefaultsDemo(), created_at: null, author: "", note: "코드 기본값", kind: "default", scorecard: null };
  }
  function tuneValidateDemo(prm) {   // params.validate와 같은 규칙
    if (!prm || typeof prm !== "object" || Array.isArray(prm)) return "설정 형식 오류 — 키:값 묶음이 아님";
    const reg = tuneData().params, keys = new Set(reg.params.map(q => q.key));
    const unknown = Object.keys(prm).filter(k => !keys.has(k));
    if (unknown.length) return `모르는 키: ${unknown.slice(0, 3).join(", ")}`;
    for (const q of reg.params) {
      if (!(q.key in prm)) return `값 빠짐: ${q.key}`;
      const v = prm[q.key];
      if (typeof v !== "number" || !isFinite(v)) return `${q.label}(${q.key}) 값이 숫자가 아님`;
      if (v < q.hard[0] || v > q.hard[1]) return `${q.label} ${v}${q.unit} — 절대 한계 ${q.hard[0]}~${q.hard[1]} 밖`;
    }
    const lab = k => reg.params.find(q => q.key === k).label;
    for (const [a, , b] of reg.relations) if (!(prm[a] < prm[b])) return `${lab(a)}(${prm[a]}) < ${lab(b)}(${prm[b]}) 이어야 함`;
    return null;
  }
  function tuneGateDemo(action, body) {
    if (!tuneData()) return [{ error: "튜닝 데이터가 없습니다" }, 503];
    let prm = body.params, note = String(body.note || "").slice(0, 200);
    if (action === "rollback") {
      const v = body.version;
      const t = v === "default" || v === 0 ? { version: 0, params: tuneDefaultsDemo() } : TUNING.history.find(h => h.version === v);
      if (!t) return [{ error: "없는 설정 버전입니다" }, 400];
      prm = t.params; note = note || (t.version === 0 ? "코드 기본값으로" : `v${t.version}로`);
    }
    const why = tuneValidateDemo(prm);
    if (why) return [{ error: why }, 400];
    const card = JCCPredict.scorecard(tuneData().scenarios, prm); delete card.runs;
    if (action === "evaluate") return [{ scorecard: card }, 200];
    if (card.missed) {
      logEvent("", "", "tuning_reject", `예지 기준 적용 거부 — 사고 ${card.missed}건 놓침 (데모)`, "user");
      return [{ error: `사고 시나리오 ${card.missed}건을 놓쳐 적용할 수 없습니다`, scorecard: card }, 409];
    }
    const cur = tuneActiveDemo().version;
    if (Number.isInteger(body.base_version) && body.base_version !== cur)
      return [{ error: "그 사이 다른 설정이 먼저 적용됐습니다 — 새 설정을 확인하세요", active: tuneActiveDemo() }, 409];
    const ver = cur + 1, kind = action === "rollback" ? "rollback" : "apply";
    TUNING.history.unshift({ version: ver, params: Object.assign({}, prm), created_at: now(), author: "데모", note, kind,
      scorecard: { caught: card.caught, missed: card.missed, false_alarms: card.false_alarms } });
    PREDICTOR.reconfigure(prm);                       // 데모 판넬의 실시간 판정에도 바로 반영
    logEvent("", "", kind === "rollback" ? "tuning_rollback" : "tuning_apply",
      `예지 기준 v${ver} ${kind === "rollback" ? "되돌림" : "적용"}${note ? " — " + note : ""} (데모)`, "user");
    return [{ ok: true, version: ver, scorecard: card }, 200];
  }

  // ── 설치 점검 (데모: commission.py와 같은 규칙, 출력 시험은 현장 CCM 대신 시간으로 흉내) ──
  const CM = { tests: {}, reports: [], seq: 1 };
  const CM_ADVICE = { "연결 상태": "CCM 전원·이더넷 케이블·서버 주소(config.toml [transport]) 확인",
    "센서 응답": "응답 없는 센서의 전원·RS485 배선(A/B 극성)·Modbus 주소 확인", "채널 상태": "꺼진 채널이 있음 — 편집 → 꺼진 센서 모두 켜기",
    "데이터 수신": "센서 전원·RS485 배선(A/B 극성)·Modbus 주소·통신속도 확인", "값 유효성": "읽기 오류 — 배선·주소·통신속도(9600/19200) 확인",
    "측정 범위": "값이 경보 범위 밖 — 실제 이상인지 먼저 확인, 아니면 센서 위치·교정 확인", "변동성": "값이 멈춤 — 센서 동결·단선 의심, 센서 전원 재투입",
    "추세": "값이 한쪽으로 계속 이동 — 교정 필요 여부 확인", "장치 식별": "추정 장치 — 실제 모델명을 확인해 프로파일 확정" };
  const cmWorst = items => { const st = items.map(i => i.status); for (const s of ["fail", "wait", "warn"]) if (st.includes(s)) return s; return st.length && st.includes("pass") ? "pass" : (st.length ? "skip" : "pass"); };
  const cmItem = (target, status, detail, advice) => ({ target, status, detail, advice: advice || "" });
  function cmFromDiag(rep, target) {
    const checks = (rep.checks || []).filter(c => c.name !== "이상탐지");
    const bad = checks.find(c => c.status === "fail") || checks.find(c => c.status === "warn");
    return bad ? cmItem(target, bad.status, `${bad.name}: ${bad.detail}`, CM_ADVICE[bad.name]) : cmItem(target, "pass", checks.slice(0, 2).map(c => c.detail).join(" · ") || "정상");
  }
  function cmCheck(panel) {
    const p = panelList().find(x => x.panel === panel); if (!p) return null;
    const t = now(), steps = [];
    let items = p.ccms.map(d => cmFromDiag(diagnose(d.device_id, "", true), `CCM ${d.device_id}`));
    items.push(cmItem("현장 예지", "pass", "데모 — 브라우저가 현장 CCM 판정을 흉내 냄"));
    steps.push({ key: "connect", title: "연결", status: cmWorst(items), items });
    items = [];
    p.ccms.forEach(d => d.latest.forEach(s => {
      const target = `${s.name || s.sensor_key} (${d.device_id})`;
      let it = cmFromDiag(diagnose(d.device_id, s.sensor_key, true), target);
      const n = (S.readings[K(d.device_id, s.sensor_key)] || []).length;
      if (it.status !== "fail" && n < 6 && s.enabled) it = cmItem(target, "wait", `관찰 중 — 값 ${n}/6개 수신(약 30초 기다림)`);
      items.push(it);
    }));
    steps.push({ key: "sensors", title: "센서", status: cmWorst(items), items });
    const r = panelRoles(), W = { h2: "수소", voc: "VOC", co: "CO", current: "전류", contact_temp: "접점온도", ambient: "함내온도", humidity: "습도" };
    const gases = ["h2", "voc", "co"].filter(g => r[g]);
    items = [cmItem("화재 징조 예지", gases.length ? "pass" : "warn", gases.length ? "가능 — " + gases.map(g => W[g]).join("·") : "불가 — 가스 센서(H2·VOC·CO) 없음")];
    [["접점 발열 예지", ["current", "contact_temp", "ambient"]], ["결로 예지", ["ambient", "humidity"]]].forEach(([title, need]) => {
      const miss = need.filter(x => !r[x]).map(x => W[x]);
      items.push(cmItem(title, miss.length ? "warn" : "pass", miss.length ? `불가 — ${miss.join(", ")} 없음` : "가능 — " + need.map(x => W[x]).join("·")));
    });
    steps.push({ key: "roles", title: "예지 역할", status: cmWorst(items), items });
    items = [["vent", "벤트"], ["fan", "팬"]].map(([k, nm]) => {
      const x = (CM.tests[panel] || {})[k];
      if (!x) return cmItem(nm, "wait", "데모 — 시험 전", "판넬 앞에서 [시험]을 누르세요 — 실제로 켜졌다 꺼집니다");
      const age = t - x.t0;
      if (age < 3) return cmItem(nm, "wait", `${nm} 켜기 명령을 보냄 — 현장 CCM 확인 기다리는 중`);
      if (age < 6) return cmItem(nm, "wait", `${nm} 켜짐 확인 — 끄기 명령을 보냄`);
      return k === "vent" ? cmItem(nm, "pass", "개방·닫힘 모두 실제 작동 확인")
        : cmItem(nm, "warn", "가동·정지 명령 반영됨(릴레이만 확인)", "위치 스위치가 없어 실제 움직임은 모름 — 눈으로 확인하고, 보조접점 달린 구동기 권장");
    });
    steps.push({ key: "outputs", title: "출력 시험", status: cmWorst(items), items });
    const sts = steps.map(s => s.status);
    const overall = sts.includes("fail") ? "fail" : sts.includes("wait") ? "wait" : sts.includes("warn") ? "warn" : "pass";
    return { panel, panel_name: p.panel_name, ts: t, overall, steps };
  }
  function cmPost(action, b) {
    const panel = String(b.panel || "");
    if (action === "output_test") {
      if (["vent", "fan"].indexOf(b.actuator) < 0) return [{ error: "이 판넬엔 그 출력을 쥔 현장 CCM이 없습니다" }, 400];
      const cur = (CM.tests[panel] || {})[b.actuator];
      if (cur && now() - cur.t0 < 6) return [{ error: "이미 시험 중입니다" }, 400];
      (CM.tests[panel] = CM.tests[panel] || {})[b.actuator] = { t0: now() };
      logEvent(panel, "", "actuator", `${b.actuator === "vent" ? "벤트" : "팬"} 설치 시험 (데모)`, "user");
      return [{ ok: true }, 200];
    }
    if (action === "complete") {
      const rep = cmCheck(panel); if (!rep) return [{ error: "없는 판넬입니다" }, 404];
      rep.note = String(b.note || "").slice(0, 500); rep.by = "데모";
      const verdict = { pass: "합격", warn: "조건부 합격", wait: "미완료 항목 있음", fail: "불합격" }[rep.overall];
      const id = CM.seq++; CM.reports.unshift({ id, panel, ts: now(), by: "데모", overall: rep.overall, report: rep });
      logEvent(panel, "", "commission", `설치 점검 완료 기록: ${rep.panel_name} — ${verdict} (데모)`, "user");
      return [{ ok: true, id, overall: rep.overall, verdict }, 200];
    }
    return [{ error: "없는 작업입니다" }, 404];
  }

  // ── 정기 점검 일지 (데모: 서버 inspection.py와 같은 모양, 메모리에만 — 새로고침하면 사라짐. 사진은 화면 안에 보관) ──
  const PCHKD = { list: [], seq: 1, pseq: 1 };
  const PCHK_CL = [
    ["외관·청결", [["clean", "판넬 내부 먼지·이물 청소"], ["filter", "환기 팬 필터 상태(막힘·오염)"], ["door", "도어 패킹·잠금·접지선"]]],
    ["전기", [["terminal", "주요 단자 조임 상태(변색·풀림)"], ["breaker", "차단기 외관·발열 흔적"], ["current", "부하 전류가 화면 값과 맞는지(클램프 측정)"]]],
    ["센서", [["gas_test", "가스 센서 기능시험(표준가스 또는 시험 버튼)"], ["temp_cmp", "온습도 센서 — 기준계와 비교"], ["wiring", "센서 배선·고정·커넥터"]]],
    ["출력", [["vent", "벤트 열림·닫힘 작동 시험"], ["heater_fan", "히터·팬 작동 시험"]]],
    ["통신·전원", [["ccm", "CCM 상태등·통신(화면에 실시간 값)"], ["power", "CCM 전원·배선·방수"]]],
  ];
  function pchkFocusDemo(panel) {
    const prev = PCHKD.list.filter(x => x.panel === panel && x.status === "done").sort((a, b) => b.done_at - a.done_at)[0];
    const since = prev ? prev.done_at : now() - 90 * 86400, items = [];
    const al = S.alarms.filter(a => a.raised_at >= since), falsey = al.filter(a => a.cause === "false" || a.cause === "work").length;
    const by = {};
    al.filter(a => a.cause !== "false" && a.cause !== "work").forEach(a => { (by[a.kind + "|" + a.sensor_key] = by[a.kind + "|" + a.sensor_key] || []).push(a); });
    Object.values(by).sort((a, b) => b.length - a.length).forEach(l => { const a = l[0];
      items.push({ prio: a.severity === "crit" ? 0 : 2, text: `${a.detail.split(" — ")[0]} — ${l.length}회, 원인 확인`, check: null }); });
    ((rulView()[0] || {}).items || []).forEach(i => { if (i.status === "reached" || (i.status === "ok" && i.days <= 60))
      items.push({ prio: i.days <= 14 ? 0 : 1, text: `${i.label}: ${i.say} — ${i.advice}`, check: "terminal" }); });
    if (!prev || now() - prev.done_at >= 80 * 86400) items.push({ prio: 2, text: "가스 센서 정기 기능시험(제조사 권장 — 표준가스 또는 시험 버튼)", check: "gas_test" });
    if (falsey) items.push({ prio: 3, text: `오경보·시험 작업으로 기록된 경보 ${falsey}건 — 반복되면 경보 기준 조정 검토`, check: null });
    items.sort((a, b) => a.prio - b.prio);
    const d = new Date(since * 1000);
    return { items: items.slice(0, 12), since, since_label: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}` + (prev ? " (지난 점검)" : " (최근 90일)"),
      alarms: al.length, false_alarms: falsey };
  }
  const pchkPub = x => x && Object.assign({}, x, { photos: x.photos.map(p => ({ id: p.id, ts: p.ts, caption: p.caption, by: p.by, src: p.src })) });
  function pchkHistDemo(panel) {
    return PCHKD.list.filter(x => !panel || x.panel === panel).sort((a, b) => (b.done_at || b.created_at) - (a.done_at || a.created_at))
      .map(x => ({ id: x.id, panel: x.panel, status: x.status, created_at: x.created_at, done_at: x.done_at || null, by: x.by, next_due: x.next_due || null,
        overall: x.report ? x.report.overall : null, panel_name: x.report ? x.report.panel_name : null, signer: x.report ? x.report.signer : null }));
  }
  function pchkPost(action, b) {
    const pl = panelList();
    if (action === "start") {
      const p = pl.find(x => x.panel === b.panel); if (!p) return [{ error: "없는 판넬입니다" }, 400];
      let d = PCHKD.list.find(x => x.panel === p.panel && x.status === "draft");
      if (!d) { d = { id: PCHKD.seq++, panel: p.panel, status: "draft", created_at: now(), updated_at: now(), by: "데모", photos: [],
          data: { checklist: PCHK_CL.map(([g, its]) => ({ group: g, items: its.map(([k, l]) => ({ key: k, label: l, applies: k !== "heater_fan" })) })),
            results: {}, notes: {}, summary: "", interval: 90 } };
        PCHKD.list.push(d); logEvent(p.panel, "", "inspection", `정기 점검 시작: ${p.panel_name} (데모)`, "user"); }
      return [pchkPub(d), 200];
    }
    const d = PCHKD.list.find(x => x.id === b.id); if (!d) return [{ error: "없는 점검입니다" }, 400];
    if (d.status !== "draft") return [{ error: "완료된 점검은 고칠 수 없습니다" }, 400];
    const keys = new Set(d.data.checklist.flatMap(g => g.items.map(i => i.key)));
    if (action === "save") {
      Object.entries(b.results || {}).forEach(([k, v]) => { if (keys.has(k) && ["ok", "fix", "bad", "na"].includes(v)) d.data.results[k] = v; });
      Object.entries(b.notes || {}).forEach(([k, v]) => { if (keys.has(k)) d.data.notes[k] = String(v || "").slice(0, 300); });
      if ("summary" in b) d.data.summary = String(b.summary || "").slice(0, 3000);
      if ([30, 60, 90, 180, 365].includes(b.interval)) d.data.interval = b.interval;
      d.updated_at = now(); return [pchkPub(d), 200];
    }
    if (action === "photo") {
      if (!/^data:image\/(jpeg|png);base64,/.test(String(b.data || ""))) return [{ error: "image/jpeg, image/png만 올릴 수 있습니다" }, 400];
      if (d.photos.length >= 20) return [{ error: "사진은 점검 하나에 20장까지입니다" }, 400];
      const id = PCHKD.pseq++; d.photos.push({ id, ts: now(), caption: String(b.caption || "").slice(0, 120), by: "데모", src: b.data }); return [{ id }, 200];
    }
    if (action === "photo_delete") { const n = d.photos.length; d.photos = d.photos.filter(p => p.id !== b.photo_id);
      return n !== d.photos.length ? [{ ok: true }, 200] : [{ error: "지울 수 없는 사진입니다" }, 400]; }
    if (action === "complete") {
      const left = d.data.checklist.flatMap(g => g.items).filter(i => i.applies && !d.data.results[i.key]);
      if (left.length) return [{ error: `체크리스트 ${left.length}개가 남았습니다 — 예: ${left[0].label}` }, 400];
      const signer = String(b.signer || "").trim().slice(0, 40); if (!signer) return [{ error: "고객 확인자 이름을 적어 주세요" }, 400];
      if (!/^data:image\/png;base64,/.test(String(b.signature || ""))) return [{ error: "서명이 없습니다" }, 400];
      const p = pl.find(x => x.panel === d.panel) || { panel_name: d.panel }, res = d.data.results;
      const counts = { ok: 0, fix: 0, bad: 0, na: 0 }; Object.values(res).forEach(v => counts[v]++);
      const overall = counts.bad ? "bad" : counts.fix ? "fix" : "ok", t = now(), cid = ACC.owner[d.panel];
      d.report = { panel: d.panel, panel_name: p.panel_name, site: p.site || "", customer: cid != null ? ((ACC.customers.find(x => x.id === cid) || {}).name || "") : "",
        started_at: d.created_at, done_at: t, by: "데모", signer, signature: b.signature, focus: pchkFocusDemo(d.panel), checklist: d.data.checklist,
        results: res, notes: d.data.notes, summary: d.data.summary, counts, overall, interval: d.data.interval, next_due: t + d.data.interval * 86400,
        photos: d.photos.map(x => ({ id: x.id, caption: x.caption, src: x.src })) };
      Object.assign(d, { status: "done", done_at: t, next_due: d.report.next_due });
      logEvent(d.panel, "", "inspection", `정기 점검 완료: ${p.panel_name} — ${{ ok: "이상 없음", fix: "현장 조치 완료", bad: "조치 필요 항목 있음" }[overall]} (확인 ${signer}, 데모)`, "user");
      return [pchkPub(d), 200];
    }
    return [{ error: "없는 작업입니다" }, 404];
  }

  // ── 현장 목록 (데모: 서버 fleet.py와 같은 모양·규칙. 데모는 판넬 1개) ──
  function fleetView() {
    const t = now(), pv = predView()[0] || {}, life = (rulView()[0] || {}).items || [];
    return panelList().map(p => {
      const keys = new Set([p.panel, ...p.ccms.map(c => c.device_id)]);
      const al = S.alarms.filter(a => !a.cleared_at && keys.has(a.device_id));
      const crit = al.filter(a => a.severity === "crit").length, on = p.ccms.filter(c => c.online).length;
      const f = pv.fire || {}, c = pv.contact || {}, d = pv.dew || {};
      const near = life.find(i => i.status === "reached" || i.status === "ok");
      let why = [], status;
      if (crit) why.push(`위험 경보 ${crit}`);
      if (["danger", "critical"].includes(f.stage)) why.push(`화재 징조(FRI ${f.fri})`);
      if (c.stage === "danger") why.push("접점 발열 위험");
      if (why.length) status = "crit";
      else if (!on) { status = "offline"; why = ["CCM 연결 끊김"]; }
      else {
        if (al.length - crit) why.push(`주의 경보 ${al.length - crit}`);
        if (on < p.ccms.length) why.push(`CCM ${p.ccms.length - on}대 끊김`);
        if (["watch", "warning"].includes(f.stage)) why.push("화재 지켜보는 중");
        if (d.stage === "danger") why.push("결로 위험");
        if (near && (near.status === "reached" || near.days <= 14)) why.push("남은 여유 2주 이내");
        status = why.length ? "warn" : "ok";
      }
      const cm = CM.reports.find(r => r.panel === p.panel), cid = ACC.owner[p.panel];
      const ls = Math.max(0, ...p.ccms.map(x => x.last_seen || 0));
      return { panel: p.panel, panel_name: p.panel_name, site: p.site || "", customer_id: cid ?? null,
        customer: cid != null ? ((ACC.customers.find(x => x.id === cid) || {}).name || "") : "",
        status, why: why.slice(0, 3), ccm_online: on, ccm_total: p.ccms.length, last_seen: ls || null,
        alarms: { crit, warn: al.length - crit, unacked: al.filter(a => !a.acked_at).length },
        predict: { fire: f.stage, fri: f.fri, contact: c.stage, dew: d.stage, vent_open: !!(f.vent && f.vent.open) },
        life: near ? { label: near.label, say: near.say, days: near.days, status: near.status } : null,
        commission: cm ? { overall: cm.overall, ts: cm.ts } : null,
        next_inspection: (PCHKD.list.filter(x => x.panel === p.panel && x.status === "done").sort((a, b) => b.done_at - a.done_at)[0] || {}).next_due || null,
        sensors: p.ccms.reduce((n, x) => n + (x.latest || []).length, 0), age: ls ? Math.round(t - ls) : null };
    });
  }

  // ── 노드 추가: 센서 직접 지정 (데모: 서버 manual_sensors.py와 같은 모양·검사, CCM 반영은 5초 뒤로 흉내) ──
  const MANUAL = { list: [], ver: {} };
  const DT = ["uint16", "int16", "uint32", "int32", "float32"];
  function manualClean(sp) {
    if (!sp || typeof sp !== "object") throw new Error("센서 지정은 객체여야 합니다");
    const key = String(sp.key || "").trim(), tcp = sp.driver === "modbus_tcp";
    if (!/^[a-z0-9_]{2,40}$/.test(key)) throw new Error("센서 키는 영문 소문자·숫자·_ 2~40자여야 합니다");
    if (!["modbus", "modbus_tcp"].includes(sp.driver)) throw new Error("연결 방식은 RS485 또는 Modbus TCP여야 합니다");
    const int = (v, lo, hi, w) => { if (!Number.isInteger(v) || v < lo || v > hi) throw new Error(`${w}는 ${lo}~${hi} 사이여야 합니다`); return v; };
    const num = (v, w) => { if (typeof v !== "number" || !isFinite(v) || Math.abs(v) > 1e6) throw new Error(`${w}는 ±1,000,000 안의 숫자여야 합니다`); return v; };
    const out = { key, name: String(sp.name || "").trim().slice(0, 40) || key, unit: String(sp.unit || "").trim().slice(0, 12), driver: sp.driver,
      slave: int(sp.slave, tcp ? 0 : 1, tcp ? 255 : 247, tcp ? "유닛 ID" : "Modbus 주소"), register: int(sp.register ?? 0, 0, 65535, "레지스터 번호"),
      type: sp.type || "input", datatype: sp.datatype || "uint16", scale: num(sp.scale ?? 1, "배율"), offset: num(sp.offset ?? 0, "보정값") };
    if (out.scale === 0) throw new Error("배율은 0일 수 없습니다");
    if (!["input", "holding"].includes(out.type)) throw new Error("레지스터 종류는 input 또는 holding이어야 합니다");
    if (!DT.includes(out.datatype)) throw new Error(`형식은 ${DT.join(", ")} 중 하나여야 합니다`);
    if (tcp) { const h = String(sp.host || "").trim();
      if (!/^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$/.test(h) || h.includes("..")) throw new Error("IP 주소(또는 호스트 이름)가 올바르지 않습니다");
      out.host = h; out.port = int(sp.port ?? 502, 1, 65535, "포트"); }
    return out;
  }
  function manualNode(m) {
    const sp = m.spec, mt = m.meta || {};
    return { key: sp.key, name: sp.name, unit: sp.unit, kind: mt.kind || "", brand: mt.brand || "", product: mt.product || "",
      part_no: mt.part_no || "", manual: mt.manual || "", photo: mt.photo || "", relays: [],
      alarm_min: mt.alarm_min ?? null, alarm_max: mt.alarm_max ?? null, alarm_warn: mt.alarm_warn ?? null,
      source: sp.driver, confidence: "수동", address: sp.driver === "modbus" ? sp.slave : `${sp.host}:${sp.port}#${sp.slave}`,
      enabled: true, pendingUntil: m.applyAt };
  }
  function manualPost(b) {
    const dev = String(b.device_id || "");
    if (b.action === "add") {
      if (!S.discovered[dev]) return [{ error: "없는 CCM입니다" }, 400];
      let sp; try { sp = manualClean(b.spec); } catch (e) { return [{ error: e.message }, 400]; }
      if (S.discovered[dev].some(x => x.key === sp.key)) return [{ error: `이 CCM에 이미 '${sp.key}' 센서가 있습니다 — 다른 키를 쓰세요` }, 400];
      if (MANUAL.list.filter(m => m.device_id === dev).length >= 16) return [{ error: "CCM 하나에 수동 센서는 16개까지입니다" }, 400];
      const v = MANUAL.ver[dev] = (MANUAL.ver[dev] || 0) + 1;
      const m = { device_id: dev, spec: sp, meta: b.meta || {}, created_at: now(), by: "데모", applyAt: now() + 5 };
      MANUAL.list.push(m);
      if (sp.driver === "modbus") S.discovered[dev] = S.discovered[dev].filter(x => !(x.confidence === "추정" && x.address === sp.slave));
      S.discovered[dev].push(manualNode(m));
      logEvent(dev, sp.key, "manual_sensor", `센서 직접 지정: ${sp.name} (${sp.driver === "modbus" ? "RS485 주소 " + sp.slave : "Modbus TCP " + sp.host + ":" + sp.port}) — CCM 반영 대기 v${v} (데모)`, "user");
      return [{ ok: true, sensor: Object.assign({}, sp, { version: v }) }, 200];
    }
    if (b.action === "remove") {
      const i = MANUAL.list.findIndex(m => m.device_id === dev && m.spec.key === b.key);
      if (i < 0) return [{ error: "없는 수동 센서입니다" }, 404];
      MANUAL.list.splice(i, 1); S.discovered[dev] = (S.discovered[dev] || []).filter(x => x.key !== b.key);
      MANUAL.ver[dev] = (MANUAL.ver[dev] || 0) + 1;
      logEvent(dev, b.key, "manual_sensor", `직접 지정한 센서 삭제: ${b.key} (데모)`, "user");
      return [{ ok: true }, 200];
    }
    return [{ error: "action은 add 또는 remove" }, 400];
  }
  function manualStatus() {
    return MANUAL.list.map(m => ({ device_id: m.device_id, key: m.spec.key, spec: m.spec, meta: m.meta, created_at: m.created_at,
      by: m.by, status: now() >= m.applyAt ? "applied" : "pending", reason: "" }));
  }
  function profileList() {
    return Object.keys(PROFILES).filter(k => !k.startsWith("TURCK-CCM")).map(k => { const p = PROFILES[k];
      return { ident: k, brand: p.brand, product: p.product, part_no: p.part_no || "", manual: p.manual || "", photo: p.photo || "",
        emits: p.emits.map(e => ({ key: e.key, name: e.name, unit: e.unit, kind: e.kind, alarm_min: e.alarm_min, alarm_warn: e.alarm_warn, alarm_max: e.alarm_max })) }; });
  }

  // ── 남은 여유 (데모: 지난 21일 하루 대표값을 시연용으로 합성 — 실서버는 rul.py가 실제 기록으로 같은 계산) ──
  function rulEstimate(vals, thr, dir) {           // rul.estimate와 같은 규칙(Theil–Sen, 25~75 백분위)
    const sg = dir === "down" ? -1 : 1, pts = vals.slice(-30).map((v, i) => [i, v * sg]), n = pts.length;
    const out = { status: "insufficient", n_days: n, threshold: thr, current: n ? vals[vals.length - 1] : null, days: null, days_lo: null, days_hi: null };
    if (n < 7) return out;
    const sl = []; for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) sl.push((pts[j][1] - pts[i][1]) / (pts[j][0] - pts[i][0]));
    sl.sort((a, b) => a - b);
    const q = p => { const k = (sl.length - 1) * p, a = Math.floor(k), b = Math.min(a + 1, sl.length - 1); return sl[a] + (sl[b] - sl[a]) * (k - a); };
    const m = q(0.5), ints = pts.map(([x, y]) => y - m * x).sort((a, b) => a - b), b0 = ints[Math.floor(ints.length / 2)];
    const cur = b0 + m * pts[n - 1][0], gap = thr * sg - cur, lo = q(0.25), hi = q(0.75);
    out.current = Math.round(cur * sg * 1000) / 1000; out.slope_per_day = m * sg;
    if (gap <= 0) return Object.assign(out, { status: "reached" });
    if (lo <= 0) return Object.assign(out, { status: hi < 0 ? "away" : "flat" });
    if (gap / m > 365) return Object.assign(out, { status: "far" });
    return Object.assign(out, { status: "ok", days: gap / m, days_lo: gap / hi, days_hi: Math.min(gap / lo, 730) });
  }
  function rulSay(i) {
    if (i.status === "insufficient") return `데이터 ${i.n_days}일 — 7일 쌓이면 예측`;
    if (i.status === "reached") return "이미 기준선에 닿음";
    if (i.status === "flat" || i.status === "away") return "뚜렷하게 나빠지는 추세 없음";
    if (i.status === "far") return "1년 넘게 여유";
    const [u, k] = i.days >= 21 ? ["주", 7] : ["일", 1], a = Math.max(1, Math.round(i.days_lo / k)), z = Math.max(1, Math.round(i.days_hi / k));
    const what = { contact: "위험 기준까지", gas: "경고선까지(기준선 드리프트)" }[i.kind] || "경고선까지";
    return `${what} 약 ${Math.max(1, Math.round(i.days / k))}${u}` + (a !== z ? ` (${a}~${z}${u})` : "");
  }
  function rulView() {
    const wob = (i, a) => Math.sin(i * 2.399) * a;                     // 결정적 잔물결(새로고침해도 같은 값)
    const series = (f) => Array.from({ length: 21 }, (_, i) => f(i));
    const res = (PREDICTOR.contact_cfg && PREDICTOR.contact_cfg.res_alarm) || 11;
    const items = [
      Object.assign(rulEstimate(series(i => 3.1 + 0.11 * i + wob(i, 0.25)), res, "up"), { kind: "contact", unit: "°C",
        label: `접점 발열 잔차 → 위험 기준 ${res}°C`, advice: "접점 조임·청소 점검을 계획하세요" }),
      Object.assign(rulEstimate(series(i => 0.6 + 0.004 * i + wob(i, 0.05)), 10, "up"), { kind: "gas", unit: "%LEL",
        label: "수소 → 경고선 10%LEL", advice: "" }),
      Object.assign(rulEstimate(series(i => 31 + wob(i, 0.8)), 45, "up"), { kind: "sensor", unit: "°C",
        label: "함내 온도 → 경고선 45°C", advice: "" }),
    ];
    items.forEach(i => { i.say = rulSay(i); if (i.status !== "ok" && i.status !== "reached") i.advice = ""; });
    const order = { reached: 0, ok: 1, insufficient: 3, flat: 4, far: 5, away: 6 };
    items.sort((a, b) => (order[a.status] - order[b.status]) || ((a.days ?? 1e9) - (b.days ?? 1e9)));
    return [{ panel: SIM.panel, panel_name: S.panelNames[SIM.panel] || SIM.panel_name, items, demo: "시연용 합성 기록(21일)" }];
  }

  // ── AI에게 물어보기 (데모: 실제 AI 호출 없음 — 시연 데이터를 요약한 예시 답. 실서버는 ai.py가 Claude로 답한다) ──
  const AIDEMO = { used: 0, limit: 200 };
  function aiDemoStatus() {
    const on = ACC.owner[SIM.panel] == null || (ACC.customers.find(c => c.id === ACC.owner[SIM.panel]) || {}).ai_enabled;
    return { available: true, reason: "", enabled: true, blocked: "", panels: on ? 1 : 0, excluded_panels: on ? 0 : 1,
      model: "데모(실제 AI 아님)", usage: { questions: AIDEMO.used, limit: AIDEMO.limit, remaining: AIDEMO.limit - AIDEMO.used } };
  }
  function aiDemoAsk(q) {
    q = String(q || "").trim();
    if (!q) return [{ error: "질문을 입력하세요" }, 400];
    if (q.length > 1000) return [{ error: "질문은 1000자 이내로 써 주세요" }, 400];
    if (!aiDemoStatus().panels) return [{ answer: "", note: "이 고객사는 AI가 꺼져 있어 판넬 기록을 보내지 않았습니다(계정 관리에서 켜기).",
      evidence: [], model: "데모", usage: {}, remaining: AIDEMO.limit - AIDEMO.used }, 200];
    AIDEMO.used++;
    const t = now(), hm = ts => { const d = new Date(ts * 1000); return `${d.getMonth() + 1}월 ${d.getDate()}일 ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };
    const evidence = [], cite = (ref, kind, label, ts) => { if (!evidence.some(e => e.ref === ref))
      evidence.push({ ref, kind, label: String(label || "").slice(0, 200), ts, panel: SIM.panel, cited: true }); return `[${ref}]`; };
    const name = S.panelNames[SIM.panel] || SIM.panel_name;
    const pv = predView()[0], lines = [];
    const am = q.match(/경보#(\d+)/), one = am && S.alarms.find(a => a.id === +am[1]);
    if (one) {
      lines.push(`${hm(one.raised_at)}에 ${name}에서 경보가 났습니다: ${one.detail || one.kind} ${cite("경보#" + one.id, "alarm", one.detail || one.kind, one.raised_at)}`);
      lines.push(one.cleared_at ? `${hm(one.cleared_at)}에 정상으로 돌아왔습니다.` : "아직 해제되지 않았습니다.");
    } else {
      const al = S.alarms.filter(a => a.raised_at >= t - 7 * 86400).sort((a, b) => b.raised_at - a.raised_at);
      lines.push((al.length ? `최근 7일 동안 ${name}에서 경보가 ${al.length}건 났습니다.` : `최근 7일 동안 ${name}에 경보가 없었습니다.`) +
        ` ${cite("경보 이력:7일", "alarm", `최근 7일 경보 ${al.length}건`, t)}`);
      al.slice(0, 4).forEach(a => lines.push(`- ${hm(a.raised_at)} ${a.detail || a.kind}${a.cleared_at ? " (해제됨)" : " (아직 열림)"} ${cite("경보#" + a.id, "alarm", a.detail || a.kind, a.raised_at)}`));
    }
    const acts = S.events.filter(e => /^(vent_open|vent_close|vent_hold|dew_actuate|actuator_fault)$/.test(e.etype)).slice(0, 3);
    if (acts.length) { lines.push("", "자동 조치 기록:");
      acts.forEach(e => lines.push(`- ${hm(e.ts)} ${e.detail} ${cite("기록#" + e.id, "event", e.detail, e.ts)}`)); }
    if (pv) {
      const f = pv.fire || {}, c = pv.contact || {}, d = pv.dew || {};
      lines.push("", `지금 판정: 화재 ${f.stage || "-"}(FRI ${f.fri ?? "-"}), 접점 ${c.stage || "-"}, 결로 ${d.stage || "-"} ${cite("상태:" + SIM.panel, "status", name + " 지금 상태", t)}`);
      if (f.stage && f.stage !== "normal" && (f.reasons || []).length) lines.push(`화재 판정 근거: ${f.reasons.slice(0, 2).join(" · ")}`);
    }
    lines.push("", "현장에서는 경보가 아직 열려 있다면 해당 센서 주변과 벤트 동작을 눈으로 확인해 주세요.");
    return [{ answer: lines.join("\n"), note: "데모 답변입니다 — 실제 AI가 아니라 시연 기록을 요약한 예시입니다. 운영 서버에서는 Claude가 기록을 직접 조회해 답합니다.",
      evidence, model: "데모", usage: {}, remaining: AIDEMO.limit - AIDEMO.used }, 200];
  }

  // ── 계정 관리 (데모: 메모리에만 — 새로고침하면 사라짐. 실서버는 accounts.py) ──
  const ACC = { customers: [{ id: 1, name: "데모 고객사", created_at: 0, monthly_notify: false, ai_enabled: true }], owner: {}, receivers: {}, users: [], seq: 2 };
  ACC.owner[SIM.panel] = 1;                      // 시연: 데모 판넬은 데모 고객사 소속
  function accTemp() { const a = "abcdefghjkmnpqrstuvwxyzACDEFGHJKLMNPQRSTUVWXYZ23456789"; let s = "";
    for (let i = 0; i < 14; i++) s += a[Math.floor(Math.random() * a.length)]; return s; }
  function accView() {
    return { customers: ACC.customers.map(c => Object.assign({}, c, {
        panels: Object.keys(ACC.owner).filter(p => ACC.owner[p] === c.id).sort(), receivers: ACC.receivers[c.id] || [] })),
      users: ACC.users.map(u => Object.assign({}, u)), env_admin: false,
      panels: panelList().map(p => ({ panel: p.panel, panel_name: p.panel_name, customer_id: ACC.owner[p.panel] ?? null, online: p.online })) };
  }
  function accAdmin(action, b) {
    const cust = id => ACC.customers.find(c => c.id === id);
    if (action === "customer") { const name = String(b.name || "").trim(); if (!name) return [{ error: "고객사 이름이 필요합니다" }, 400];
      const c = { id: ACC.seq++, name, created_at: now() }; ACC.customers.push(c); logEvent("", "", "account", `고객사 추가: ${name} (데모)`, "user");
      return [{ ok: true, id: c.id }, 200]; }
    if (action === "panel") { if (b.customer_id != null && !cust(b.customer_id)) return [{ error: "판넬과 고객사를 확인하세요" }, 400];
      if (b.customer_id == null) delete ACC.owner[b.panel]; else ACC.owner[b.panel] = b.customer_id; return [{ ok: true }, 200]; }
    if (action === "monthly_notify") { const c = cust(b.customer_id); if (!c) return [{ error: "고객사를 확인하세요" }, 400];
      c.monthly_notify = !!b.on; return [{ ok: true }, 200]; }
    if (action === "ai") { const c = cust(b.customer_id); if (!c) return [{ error: "고객사를 확인하세요" }, 400];
      c.ai_enabled = !!b.on; return [{ ok: true }, 200]; }
    if (action === "receivers") { if (!cust(b.customer_id)) return [{ error: "고객사와 번호 목록이 필요합니다" }, 400];
      const nums = []; (b.numbers || []).forEach(n => { const d = String(n).replace(/\D/g, ""); if (d.length >= 9 && d.length <= 12 && nums.indexOf(d) < 0) nums.push(d); });
      ACC.receivers[b.customer_id] = nums.slice(0, 20); return [{ ok: true, numbers: ACC.receivers[b.customer_id] }, 200]; }
    if (action === "user") { const name = String(b.username || "").trim();
      if (!/^[A-Za-z0-9._@-]{3,40}$/.test(name)) return [{ error: "아이디는 3~40자 영문·숫자·._@- 만 됩니다" }, 400];
      if (ACC.users.some(u => u.username === name)) return [{ error: "이미 있는 아이디입니다" }, 400];
      if (["admin", "manager", "viewer"].indexOf(b.role) < 0) return [{ error: "등급을 확인하세요" }, 400];
      if (b.role !== "admin" && !cust(b.customer_id)) return [{ error: "고객 계정은 고객사를 지정해야 합니다" }, 400];
      const u = { id: ACC.seq++, username: name, role: b.role, customer_id: b.role === "admin" ? null : b.customer_id,
        customer: b.role === "admin" ? null : cust(b.customer_id).name, must_change: true, disabled: false };
      ACC.users.push(u); logEvent("", "", "account", `계정 추가: ${name} (${b.role}) (데모)`, "user");
      return [{ ok: true, id: u.id, temp_password: accTemp() }, 200]; }
    const u = ACC.users.find(x => x.id === b.user_id);
    if (!u) return [{ error: "없는 계정입니다" }, 400];
    if (action === "user/reset") { u.must_change = true; return [{ ok: true, temp_password: accTemp() }, 200]; }
    if (action === "user/disable") { u.disabled = !!b.disabled; return [{ ok: true }, 200]; }
    return [{ error: "없는 관리 작업입니다" }, 404];
  }

  // ── 월간 리포트 (데모: 서버 monthly.py와 같은 모양, 수치는 시연 데이터 최근 30일) ──
  const MONTHLY = [];
  function monthlyIssue(cid, period) {
    const c = ACC.customers.find(x => x.id === cid); if (!c) return null;
    const [y, m] = period.split("-").map(Number);
    const start = Date.UTC(y, m - 1, 1, -9) / 1000, end = Date.UTC(y, m, 1, -9) / 1000;   // 한국 시간 달
    const base = buildReport(30), pr = base.summary.predict, panels = panelList().filter(p => ACC.owner[p.panel] === cid);
    const rows = panels.map(p => ({ panel: p.panel, panel_name: p.panel_name, ccms: p.ccms.length, uptime: 100,
      crit: base.summary.alarms.crit, warn: base.summary.alarms.warn, faults: 0, commission: null }));
    const advice = base.problems.filter(x => /드리프트|고착/.test(x.detail)).map(x => `${x.name}: ${/드리프트/.test(x.detail) ? "값이 한쪽으로 계속 이동했습니다 — 센서 교정을 권합니다" : "값이 멈춘 적이 있습니다 — 센서 상태 점검을 권합니다"}`);
    const rep = { customer: c.name, customer_id: cid, period, range: [start, end], generated_at: now(), panels: rows,
      summary: { panels: rows.length, uptime: rows.length ? 100 : null,
        precursors: pr.fire.detected + pr.contact.detected + pr.dew.detected, fire: pr.fire, contact: pr.contact.detected, dew: pr.dew,
        vent_auto: pr.fire.vent_auto, alarms_crit: base.summary.alarms.crit, alarms_warn: base.summary.alarms.warn, ack_min_avg: null,
        self_heal: base.summary.heal.auto_fixed, self_heal_rate: base.summary.heal.success_rate, faults: 0 },
      problems: base.problems.slice(0, 6), advice: advice.length ? advice.slice(0, 6) : ["특이사항 없음 — 지금 상태를 유지하세요"] };
    if (c.ai_enabled) {        // 데모: 실서버는 Claude가 리포트 수치만으로 쓴다(숫자 검사 통과분만). 여기선 같은 모양의 예시
      const s = rep.summary;
      rep.ai_note = { model: "데모", at: now(), text: (s.precursors ? `이번 달 사고 징조 ${s.precursors}건을 먼저 잡았고` : "이번 달은 사고 징조가 없었고") +
        (s.vent_auto ? `, 벤트가 ${s.vent_auto}번 스스로 열려 처리했습니다. ` : ", 자동 조치가 필요한 일은 없었습니다. ") +
        `위험 경보는 ${s.alarms_crit}건, 주의는 ${s.alarms_warn}건이었습니다. ${rep.advice[0]}. (데모 해설 — 실제 AI 아님)` };
    }
    const i = MONTHLY.findIndex(r => r.customer_id === cid && r.period === period);
    const row = { customer_id: cid, period, created_at: now(), by: "데모", report: rep, customer: c.name };
    if (i >= 0) MONTHLY[i] = row; else MONTHLY.push(row);
    MONTHLY.sort((a, b) => (b.period > a.period ? 1 : b.period < a.period ? -1 : a.customer_id - b.customer_id));
    logEvent("", "", "monthly", `${c.name} ${period} 월간 리포트 발행 (데모)`, "system");
    return rep;
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
      if (p === "/api/incidents") {
        const al = S.alarms.filter(a => !a.cleared_at).sort((a, b) => b.raised_at - a.raised_at).slice(0, 200);
        return Promise.resolve(J({ incidents: incidentList(al), alarms: al }));
      }
      if (p === "/api/notify/status") return Promise.resolve(J({ channels: [] }));
      if (p === "/api/auth/status") return Promise.resolve(J({ enabled: false, authed: false }));   // 데모: 시연용 로그인
      if (p === "/api/heal/config") {
        if (method === "POST") {
          const changed = [];
          const lab = { enabled: "자가치유 전체", l1: "채널 재시작(L1)", l2: "CCM 재시작(L2)" };
          [["enabled", "enabled"], ["l1_enabled", "l1"], ["l2_enabled", "l2"]].forEach(([f, k]) => {
            if (f in body) { const v = !!body[f]; if (HEAL[k] !== v) { HEAL[k] = v; changed.push(`${lab[k]} ${v ? "켜짐" : "꺼짐"}`); } }
          });
          if (changed.length) logEvent("", "", "heal_config", "자가치유 설정 변경: " + changed.join(", "), "user");
        }
        return Promise.resolve(J({ available: true, enabled: HEAL.enabled, l1_enabled: HEAL.l1, l2_enabled: HEAL.l2 }));
      }
      if (p === "/api/predict") return Promise.resolve(J({ panels: predView() }));
      if (p === "/api/admin/accounts") return Promise.resolve(J(accView()));
      if (p === "/api/ai/status") return Promise.resolve(J(aiDemoStatus()));
      if (p === "/api/rul") return Promise.resolve(J({ panels: rulView() }));
      if (p === "/api/sensor/profiles") return Promise.resolve(J({ profiles: profileList() }));
      if (p === "/api/fleet") return Promise.resolve(J({ panels: fleetView() }));
      if (p === "/api/export/alarms.csv" || p === "/api/export/readings.csv") {     // 데모: 메모리의 기록으로 같은 모양 CSV
        const days = Math.min(366, Math.max(1, parseFloat(qs.get("days") || "30"))), since = now() - days * 86400;
        const t = ts => { if (!ts) return ""; const d = new Date(ts * 1000), z = n => String(n).padStart(2, "0");
          return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}:${z(d.getSeconds())}`; };
        const q = v => /[",\r\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v);
        const CAUSE = { real: "실제 이상", false: "오경보", work: "시험·작업", other: "그 밖" };
        let rows, name;
        if (p.endsWith("alarms.csv")) {
          rows = [["경보번호", "판넬", "기기", "센서", "종류", "심각도", "내용", "발생", "확인", "확인자", "원인", "조치 메모", "해제"]].concat(
            S.alarms.filter(a => a.raised_at >= since).sort((x, y) => y.raised_at - x.raised_at).map(a => [a.id, S.panelNames[SIM.panel] || SIM.panel_name,
              a.device_id, a.sensor_key || "", a.kind, a.severity === "crit" ? "위험" : "주의", a.detail || "", t(a.raised_at), t(a.acked_at),
              a.acked_by || "", CAUSE[a.cause] || "", a.ack_note || "", t(a.cleared_at)]));
          name = "jcc-alarms-demo.csv";
        } else {
          const dev = qs.get("device_id") || "", key = qs.get("sensor") || "";
          rows = [["시각", "기기", "센서", "이름", "값", "단위", "정상 수신"]].concat((S.readings[K(dev, key)] || []).filter(r => r.ts >= since)
            .map(r => [t(r.ts), dev, key, key, r.value == null ? "" : r.value, "", r.ok ? "예" : "아니오"]));
          name = `jcc-${dev}-${key}-demo.csv`;
        }
        const csv = "\ufeff" + rows.map(r => r.map(q).join(",")).join("\r\n") + "\r\n";
        return Promise.resolve(new Response(csv, { status: 200, headers: { "Content-Type": "text/csv; charset=utf-8",
          "Content-Disposition": `attachment; filename="${name}"` } }));
      }
      if (p === "/api/inspection" && method === "GET") { const pn = qs.get("panel") || "";
        if (!panelList().some(x => x.panel === pn)) return Promise.resolve(J({ error: "없는 판넬입니다" }, 404));
        return Promise.resolve(J({ focus: pchkFocusDemo(pn), draft: pchkPub(PCHKD.list.find(x => x.panel === pn && x.status === "draft")) || null, history: pchkHistDemo(pn) })); }
      if (p === "/api/inspections") return Promise.resolve(J({ inspections: pchkHistDemo("") }));
      const mpi = p.match(/^\/api\/inspection\/(\d+)$/);
      if (mpi && method === "GET") { const x = PCHKD.list.find(y => y.id === +mpi[1]); return Promise.resolve(x ? J(pchkPub(x)) : J({ error: "볼 수 없는 점검입니다" }, 404)); }
      const mpa = p.match(/^\/api\/inspection\/([a-z_]+)$/);
      if (mpa && method === "POST") { const [obj, st] = pchkPost(mpa[1], body); return Promise.resolve(J(obj, st)); }
      if (p === "/api/sensor/manual" && method === "GET") return Promise.resolve(J({ sensors: manualStatus() }));
      if (p === "/api/sensor/manual" && method === "POST") { const [obj, st] = manualPost(body); return Promise.resolve(J(obj, st)); }
      if (p === "/api/ai/ask" && method === "POST") { const [obj, st] = aiDemoAsk(body.question);
        return new Promise(res => setTimeout(() => res(J(obj, st)), 900)); }   // 생각하는 동안의 기다림도 시연
      if (p === "/api/monthly") { const q = qs.get("customer_id");
        return Promise.resolve(J({ reports: MONTHLY.filter(r => !q || String(r.customer_id) === q) })); }
      if (p === "/api/monthly/issue" && method === "POST") {
        if (!Number.isInteger(body.customer_id) || !/^\d{4}-(0[1-9]|1[0-2])$/.test(String(body.period || "")))
          return Promise.resolve(J({ error: "고객사와 월(YYYY-MM)을 확인하세요" }, 400));
        const rep = monthlyIssue(body.customer_id, body.period);
        return Promise.resolve(rep ? J({ ok: true, report: rep }) : J({ error: "없는 고객사입니다" }, 400)); }
      if (p === "/api/commission/check") { const r = cmCheck(qs.get("panel") || ""); return Promise.resolve(r ? J(r) : J({ error: "없는 판넬입니다" }, 404)); }
      if (p === "/api/commission/reports") return Promise.resolve(J({ reports: CM.reports }));
      const mc = p.match(/^\/api\/commission\/(output_test|complete)$/);
      if (mc && method === "POST") { const [obj, st] = cmPost(mc[1], body); return Promise.resolve(J(obj, st)); }
      const ma = p.match(/^\/api\/admin\/([a-z_/]+)$/);
      if (ma && method === "POST") { const [obj, st] = accAdmin(ma[1], body); return Promise.resolve(J(obj, st)); }
      if (p === "/api/tuning/params") return Promise.resolve(tuneData() ? J(tuneData().params) : J({ error: "튜닝 데이터 없음" }, 503));
      if (p === "/api/tuning/scenarios") return Promise.resolve(tuneData() ? J({ scenarios: tuneData().scenarios }) : J({ error: "튜닝 데이터 없음" }, 503));
      if (p === "/api/tuning/config" && method === "GET") {
        if (!tuneData()) return Promise.resolve(J({ error: "튜닝 데이터 없음" }, 503));
        const hist = TUNING.history.slice(0, 20).map(h => { const c = Object.assign({}, h); delete c.params; return c; });
        return Promise.resolve(J({ active: tuneActiveDemo(), history: hist, edge: {}, defaults: tuneDefaultsDemo() }));
      }
      const mt = p.match(/^\/api\/tuning\/(evaluate|apply|rollback)$/);
      if (mt && method === "POST") { const [obj, st] = tuneGateDemo(mt[1], body); return Promise.resolve(J(obj, st)); }
      if (p === "/api/healthcheck") return Promise.resolve(J(diagnoseAll()));
      if (p === "/api/report") return Promise.resolve(J(buildReport(parseFloat(qs.get("days") || "7"))));
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
        if (p === "/api/predict/baseline") {
          const panel = String(body.panel || "");
          if (body.action !== "relearn" || !panel) return Promise.resolve(J({ error: "{panel, action:'relearn'}이 필요합니다" }, 400));
          PREDICTOR.resetContactBaseline(panel);
          logEvent(panel, "", "baseline", "접점 발열 기준 재학습 시작(정비 후)", "user");
          return Promise.resolve(J({ ok: true, panel, edge_devices: [] }));
        }
        if (p === "/api/predict/actuator") {
          const panel = String(body.panel || ""), actuator = String(body.actuator || ""), action = String(body.action || "");
          if (["vent", "heater", "fan"].indexOf(actuator) < 0 || ["open", "close", "on", "off", "auto"].indexOf(action) < 0)
            return Promise.resolve(J({ error: "actuator/action 값이 올바르지 않습니다" }, 400));
          return Promise.resolve(J(setActuator(panel, actuator, action)));
        }
        if (p === "/api/diagnose") return Promise.resolve(J(diagnose(String(body.device_id || ""), String(body.sensor_key || ""))));
        if (p === "/api/alarm/ack") {
          const CAUSE = { real: "실제 이상", false: "오경보", work: "시험·작업", other: "그 밖" };
          const note = String(body.note || "").trim().slice(0, 200), cause = CAUSE[body.cause] ? body.cause : "";
          const a = S.alarms.find(x => x.id === Number(body.alarm_id));
          let ok = false;
          if (a && !a.cleared_at && !a.acked_at) { ok = true; a.acked_at = now(); a.acked_by = "데모";
            a.ack_note = note || null; a.cause = cause || null;
            logEvent(a.device_id, a.sensor_key, "ack", `경보 확인(데모): ${a.detail}` + (cause || note ? " · " + [CAUSE[cause], note].filter(Boolean).join(" — ") : ""), "user"); }
          else if (a && a.acked_at && (note || cause)) { ok = true; if (note) a.ack_note = note; if (cause) a.cause = cause;
            logEvent(a.device_id, a.sensor_key, "ack", `경보 메모(데모): ${a.detail} · ` + [CAUSE[cause], note].filter(Boolean).join(" — "), "user"); }
          return Promise.resolve(J({ ok, alarm_id: Number(body.alarm_id) }));
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
          if (act === "shutdown") S.poweredOff[dev] = true;   // 값 공급 중단 → 실제로 꺼짐
          else delete S.poweredOff[dev];                       // 재시작 → 다시 살아남
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
  setInterval(tickPredict, 4000);   // 예지보전: 화재·접점발열·결로 + 액추에이터
  // 시연용: 콘솔에서 JCC_DEMO.episode("fire"|"contact"|"dew"|"vent_stuck") 로 에피소드를 바로 시작한다.
  window.JCC_DEMO = {
    episode(kind) {
      if (kind === "vent_stuck") {    // 화재 징조 + 벤트가 닫힌 채 걸림
        const w = now(); EPI.kind = "fire"; EPI.t0 = w; EPI.until = w + EPI_DUR;
        STUCK.until = w + EPI_DUR; STUCK.since = null;
        return `시연: 화재 징조 중 벤트 걸림 (${EPI_DUR}초)`;
      }
      if (["fire", "contact", "dew"].indexOf(kind) < 0) return "fire | contact | dew | vent_stuck 중 하나";
      const w = now(); EPI.kind = kind; EPI.t0 = w; EPI.until = w + EPI_DUR;
      return `예지 에피소드 시작: ${kind} (${EPI_DUR}초)`;
    },
    // 파이썬 코어와의 일치 검사용(server/tests/test_parity.py) — 미러가 갈라지면 테스트가 잡는다
    _core: { groupAlarms },   // 예지 코어는 window.JCCPredict
  };
  console.log("[JCC-CCM] 데모 모드: 브라우저 안에서 시뮬레이션이 돕니다 (서버 없음).");
})();
