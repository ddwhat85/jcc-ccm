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
    seq: 1, t0: Date.now() / 1000, started: false,
  };
  const SEV = { alarm: "crit", silent: "crit", anomaly: "warn", stuck: "warn", drift: "warn", alarm_warn: "warn",
                fire: "crit", contact: "crit", dew: "warn" };
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

  // ── 예지보전 엔진 (server/jcc_server/{fire_risk,contact_heat,dewpoint,predict}.py 미러) ──
  function ramp(x, lo, hi) { if (hi <= lo) return x >= hi ? 1 : 0; if (x <= lo) return 0; if (x >= hi) return 1; return (x - lo) / (hi - lo); }
  function clamp01(x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }
  // 최소제곱 기울기(분당). 시각을 평균 중심으로 옮겨 계산한다 — 에포크 초(≈1.8e9)를 그대로
  // 제곱합하면 부동소수점 상쇄로 기울기가 0으로 뭉개진다(fire_risk.slope_per_min과 동일 방식).
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
  function robustZ(value, baseline) {
    const vals = baseline.filter(v => v != null); if (vals.length < 6 || value == null) return 0;
    // 참 중앙값(짝수면 가운데 둘의 평균) — fire_risk.robust_z와 동일
    const median = a => { const s = a.slice().sort((p, q) => p - q), m = s.length;
      return m % 2 ? s[(m - 1) / 2] : (s[m / 2 - 1] + s[m / 2]) / 2; };
    const med = median(vals), mad = median(vals.map(v => Math.abs(v - med)));
    let sigma = mad > 1e-9 ? 1.4826 * mad : 0;
    if (!sigma) { const m = vals.reduce((a, c) => a + c, 0) / vals.length; sigma = Math.sqrt(vals.reduce((a, c) => a + (c - m) * (c - m), 0) / vals.length); }
    if (sigma <= 1e-9) return 0; return (value - med) / sigma;
  }
  const FCFG = { h2: { warn: 10, alarm: 25, rw: 3, ra: 10 }, voc: { warn: 200, alarm: 1000, rw: 100, ra: 400 },
    co: { warn: 50, alarm: 200, rw: 20, ra: 80 },
    tRw: 1, tRa: 5, wH2: 0.38, wVoc: 0.38, wCo: 0.32, wTemp: 0.10, pair: 0.20, conf: 0.15, zLo: 3, zHi: 6,
    openFri: 55, closeFri: 20, critFri: 80, watchFri: 30 };
  function gasScore(value, series, baseline, g) {
    const lvl = ramp(value == null ? 0 : value, g.warn, g.alarm);
    const sp = slopePerMin(series || []), rise = ramp(sp, g.rw, g.ra);
    const z = robustZ(value, baseline || []), anom = ramp(z, FCFG.zLo, FCFG.zHi);
    return { g: Math.max(lvl, rise, anom), lvl, sp, rise, z, anom };
  }
  function assessFire(sig) {
    const h2 = sig.h2 || {}, voc = sig.voc || {}, cog = sig.co || {}, temp = sig.temp || {};
    const smoke = !!sig.smoke, curAb = !!sig.current_abnormal;
    const H = gasScore(h2.value, h2.series, h2.baseline, FCFG.h2);
    const V = gasScore(voc.value, voc.series, voc.baseline, FCFG.voc);
    const C = gasScore(cog.value, cog.series, cog.baseline, FCFG.co);
    const tsp = slopePerMin(temp.series || []), tTerm = ramp(tsp, FCFG.tRw, FCFG.tRa);
    // 동반 상승: 두 종 이상 → 둘째로 센 가스만큼 가산(CO 없으면 예전 min(H2,VOC)와 동일)
    const second = [H.g, V.g, C.g].sort((a, b) => b - a)[1];
    const pair = second >= 0.3 ? FCFG.pair * second : 0;
    const conf = (smoke || curAb) ? FCFG.conf : 0;
    let fri = 100 * clamp01(FCFG.wH2 * H.g + FCFG.wVoc * V.g + FCFG.wCo * C.g + pair + FCFG.wTemp * tTerm + conf);
    const h2v = h2.value, vocv = voc.value, cov = cog.value;
    const over = [[h2v, FCFG.h2], [vocv, FCFG.voc], [cov, FCFG.co]].map(([v, g]) => v != null && v >= g.alarm);
    if (over.some(Boolean)) fri = Math.max(fri, FCFG.critFri);
    if (smoke) fri = Math.max(fri, 85);
    const bothStrong = second >= 0.5;
    let stage;
    if (fri >= FCFG.critFri || smoke || over.filter(Boolean).length >= 2) stage = "critical";
    else if (fri >= FCFG.openFri || bothStrong) stage = "danger";
    else if (fri >= FCFG.watchFri) stage = "watch";
    else stage = "normal";
    const R = [];
    if (H.rise > 0.3) R.push(`H2 상승 ${Math.round(H.sp * 100) / 100}%LEL/분`);
    if (H.anom > 0.3) R.push(`H2 평소대비 급등 z=${Math.round(H.z * 100) / 100}`);
    if (H.lvl > 0.3) R.push(`H2 ${h2v}%LEL(경고선 접근)`);
    if (V.rise > 0.3) R.push(`VOC 상승 ${Math.round(V.sp * 100) / 100}ppm/분`);
    if (V.anom > 0.3) R.push(`VOC 평소대비 급등 z=${Math.round(V.z * 100) / 100}`);
    if (V.lvl > 0.3) R.push(`VOC ${vocv}ppm(경고선 접근)`);
    if (C.rise > 0.3) R.push(`CO 상승 ${Math.round(C.sp * 100) / 100}ppm/분`);
    if (C.anom > 0.3) R.push(`CO 평소대비 급등 z=${Math.round(C.z * 100) / 100}`);
    if (C.lvl > 0.3) R.push(`CO ${cov}ppm(경고선 접근)`);
    if (pair > 0) R.push([["H2", H.g], ["VOC", V.g], ["CO", C.g]].filter(x => x[1] >= 0.3).map(x => x[0]).join("·") + " 동반 상승 — 열폭주 서명");
    if (tTerm > 0.3) R.push(`온도 급상승 ${Math.round(tsp * 10) / 10}°C/분`);
    if (smoke) R.push("열연기 감지");
    if (curAb) R.push("전류 이상");
    if (!R.length) R.push("정상 범위");
    return { fri: Math.round(fri * 10) / 10, stage, reasons: R };
  }
  // 기준 학습: 운영 기본은 300표본·30분(contact_heat.ContactCfg). 시연 화면은 몇 분 안에
  // 학습 완료를 보여주려고 짧게 둔다(로직은 동일, 숫자만 다름).
  const CCFG = { iMin: 2, resWarn: 5, resAlarm: 12, tAbs: 60, riseWarn: 0.3, kDef: 0.03, kMin: 0, kMax: 1,
                 learnSamples: 45, learnSpan: 60, tauDays: 7 };
  function fitK(samples) {
    let num = 0, den = 0;
    for (const [ts, I, T, Ta] of samples) { if (I == null || T == null || Ta == null || I < CCFG.iMin) continue; const x = I * I; num += x * (T - Ta); den += x * x; }
    if (den <= 1e-9) return CCFG.kDef; return Math.min(CCFG.kMax, Math.max(CCFG.kMin, num / den));
  }
  // 접점 발열 기준 — contact_heat.ContactBaseline 미러(정상 기간 학습 → 고정 → 아주 천천히 추종)
  class ContactBaseline {
    constructor(cfg) { this.cfg = Object.assign({}, CCFG, cfg || {}); this.reset(); this.last_ts = null; }
    reset() { const last = this.last_ts; Object.assign(this, { status: "learning", k: null, sxy: 0, sxx: 0, n: 0, t0: null,
      learned_at: null }); this.last_ts = last === undefined ? null : last; }   // 재학습은 reset 이후 표본만
    get ready() { return this.status === "ready" && this.k != null; }
    skip(ts) { if (ts != null && (this.last_ts == null || ts > this.last_ts)) this.last_ts = ts; }   // 이상 구간은 안 배움
    progress() {
      if (this.ready) return 1;
      if (this.t0 == null || this.last_ts == null) return 0;
      return Math.min(1, this.n / Math.max(1, this.cfg.learnSamples), (this.last_ts - this.t0) / Math.max(1, this.cfg.learnSpan));
    }
    observe(ts, I, T, Ta) {
      const c = this.cfg;
      if (ts == null || I == null || T == null || Ta == null || I < c.iMin) return null;
      if (this.last_ts != null && ts <= this.last_ts) return null;
      const dt = this.last_ts != null ? ts - this.last_ts : 0;
      this.last_ts = ts;
      const x = I * I, y = T - Ta;
      if (!this.ready) {
        if (this.t0 == null) this.t0 = ts;
        this.sxy += x * y; this.sxx += x * x; this.n += 1;
        if (this.n >= c.learnSamples && ts - this.t0 >= c.learnSpan && this.sxx > 1e-9) {
          this.k = Math.min(c.kMax, Math.max(c.kMin, this.sxy / this.sxx));
          this.status = "ready"; this.learned_at = ts; return "ready";
        }
        return null;
      }
      if (dt > 0 && x > 1e-9) {
        const kObs = Math.min(c.kMax, Math.max(c.kMin, y / x));
        const xRef = this.n ? Math.sqrt(this.sxx / this.n) : x;      // 평소 부하(학습 기간 I²의 RMS)
        if (Math.abs(kObs - this.k) * xRef < c.resWarn / 2) this.k += Math.min(1, dt / (c.tauDays * 86400)) * (kObs - this.k);
      }
      return null;
    }
  }
  function assessContact(cur, temp, amb, hist, kRef, learning) {
    const k = kRef != null ? kRef : fitK(hist || []);
    const dt = (temp != null && amb != null) ? temp - amb : 0;
    const exp = k * (cur || 0) * (cur || 0), res = dt - exp;
    const rser = [];
    for (const [ts, I, T, Ta] of (hist || [])) { if (I == null || T == null || Ta == null || I < CCFG.iMin) continue; rser.push([ts, (T - Ta) - k * I * I]); }
    const rslope = slopePerMin(rser);
    let stage; const R = [];
    if (temp != null && temp >= CCFG.tAbs) { stage = "danger"; R.push(`접점 온도 ${Math.round(temp * 10) / 10}°C — 절대 위험`); }
    else if ((cur || 0) < CCFG.iMin) { stage = "normal"; R.push("부하 낮음 — 발열 판정 보류"); }
    else if (res >= CCFG.resAlarm) { stage = "danger"; R.push(`전류 대비 초과발열 +${Math.round(res * 10) / 10}°C — 접촉저항 급증 의심`); }
    else if (res >= CCFG.resWarn || rslope >= CCFG.riseWarn) { stage = "watch";
      if (res >= CCFG.resWarn) R.push(`전류 대비 발열 +${Math.round(res * 10) / 10}°C` + (kRef != null ? " — 기준 대비 서서히 증가(접점 풀림·부식 의심)" : ""));
      if (rslope >= CCFG.riseWarn) R.push(`발열 추세 상승 ${Math.round(rslope * 100) / 100}°C/분 — 접점 열화 조짐`); }
    else { stage = "normal"; R.push("정상 — 전류 대비 발열 정상"); }
    if (learning != null && kRef == null) R.push(`기준 학습 중 ${Math.floor(learning * 100)}% — 느린 열화 판정은 학습 후`);
    return { stage, delta_t: Math.round(dt * 10) / 10, expected: Math.round(exp * 10) / 10, residual: Math.round(res * 10) / 10, residual_slope: Math.round(rslope * 100) / 100, k: Math.round(k * 1e4) / 1e4, reasons: R };
  }
  const DCFG = { marginWarn: 3, marginAlarm: 1, rhHigh: 80, fallWarn: 0.4, horizonMin: 10, trendCap: 6 };
  function dewPoint(t, rh) { if (t == null || rh == null || rh <= 0) return null; rh = Math.max(1, Math.min(100, rh)); const a = 17.62, b = 243.12; const g = Math.log(rh / 100) + a * t / (b + t); return b * g / (a - g); }
  function assessDew(temp, rh, surface, hist) {
    const td = dewPoint(temp, rh);
    if (td == null) return { stage: "normal", dew_point: null, margin: null, margin_slope: 0, rh: rh || 0, action: "none", reasons: ["데이터 부족"] };
    const ref = surface != null ? surface : temp, margin = ref - td;
    const mser = [];
    for (const [ts, ht, hrh, hs] of (hist || [])) { const htd = dewPoint(ht, hrh); if (htd == null) continue; const href = hs != null ? hs : ht; mser.push([ts, href - htd]); }
    const mslope = slopePerMin(mser);
    // 추세 예지: 결로 임박선까지 남은 시간(분)이 가까울 때만 (dewpoint.py와 동일)
    const eta = mslope < 0 ? (margin - DCFG.marginAlarm) / -mslope : null;
    const trendHit = mslope <= -DCFG.fallWarn && margin <= DCFG.trendCap && eta != null && eta <= DCFG.horizonMin;
    let stage, action; const R = [];
    if (margin <= DCFG.marginAlarm) { stage = "danger"; action = "heater_fan"; R.push(`이슬점 여유 ${Math.round(margin * 10) / 10}°C — 결로 임박 (이슬점 ${Math.round(td * 10) / 10}°C)`); }
    else if (margin <= DCFG.marginWarn || (rh != null && rh >= DCFG.rhHigh) || trendHit) { stage = "watch"; action = "fan"; if (margin <= DCFG.marginWarn) R.push(`이슬점 여유 ${Math.round(margin * 10) / 10}°C 좁음`); if (rh != null && rh >= DCFG.rhHigh) R.push(`습도 ${Math.round(rh)}% 높음`); if (trendHit) R.push(`여유 축소 ${Math.round(mslope * 100) / 100}°C/분 — 약 ${Math.max(1, Math.round(eta))}분 뒤 결로 임박`); }
    else { stage = "normal"; action = "none"; R.push(`정상 — 이슬점 여유 ${Math.round(margin * 10) / 10}°C`); }
    return { stage, dew_point: Math.round(td * 10) / 10, margin: Math.round(margin * 10) / 10, margin_slope: Math.round(mslope * 100) / 100, rh: Math.round((rh || 0) * 10) / 10, action, reasons: R };
  }

  // ── 판넬 상태·컨트롤러 (predict.Predictor 미러) ──
  const PRED = { autovent: true, panels: {} };
  const predAlarmState = {};
  function predState(panel) {
    let s = PRED.panels[panel];
    if (!s) s = PRED.panels[panel] = { ventOpen: false, ventManual: false, ventBelow: null,
      heater: false, fan: false, dewManual: false, dewCalm: null, last: null,
      contact: new ContactBaseline() };
    return s;
  }
  function ventStep(s, fri, stage, t) {
    if (s.ventManual) return null;
    if (stage === "danger" || stage === "critical") { s.ventBelow = null; if (!s.ventOpen) { s.ventOpen = true; return "open"; } return stage === "critical" ? "hold" : null; }
    if (s.ventOpen) { if (fri < FCFG.closeFri) { if (s.ventBelow == null) s.ventBelow = t; else if (t - s.ventBelow >= 60) { s.ventOpen = false; s.ventBelow = null; return "close"; } } else s.ventBelow = null; }
    return null;
  }
  function dewStep(s, stage, t) {
    if (s.dewManual) return null;
    if (stage === "danger") { s.dewCalm = null; if (!(s.heater && s.fan)) { s.heater = true; s.fan = true; return "heater_fan"; } return null; }
    if (stage === "watch") { s.dewCalm = null; if (!s.fan) { s.fan = true; return "fan"; } return null; }
    if (s.heater || s.fan) { if (s.dewCalm == null) s.dewCalm = t; else if (t - s.dewCalm >= 60) { s.heater = false; s.fan = false; s.dewCalm = null; return "off"; } }
    return null;
  }
  function seriesOf(dev, key, n) {
    return (S.readings[K(dev, key)] || []).slice(-n).filter(p => p.ok && p.value != null).map(p => [p.ts, p.value]);
  }
  // 기울기·기준선용: 인과 3점 이동중앙값(단발 튐 제거 — storage._series(despike=True)와 동일)
  const PREDICT_WARMUP = 6, PREDICT_STALE_MIN = 15, PREDICT_MIN_SPAN = 20;   // storage.PREDICT_*와 동일
  function seriesF(dev, key, n) {
    const pts = seriesOf(dev, key, n);
    if (pts.length < 3) return pts;
    // 창이 3개로 꽉 찬 지점부터만(앞 두 점은 창이 2개라 튄 값이 새어 나옴)
    const out = [];
    for (let i = 2; i < pts.length; i++) out.push([pts[i][0], [pts[i - 2][1], pts[i - 1][1], pts[i][1]].sort((a, b) => a - b)[1]]);
    return out;
  }
  // base 각 시각에, 그 시각 이하에서 가장 최근의 other 값(tol초 이내) — 위치 대신 시각으로 짝짓기
  function asof(base, other, tol) {
    tol = tol || 30; const out = []; let j = 0;
    for (const [ts] of base) {
      while (j + 1 < other.length && other[j + 1][0] <= ts) j++;
      const ok = other.length && other[j][0] <= ts && ts - other[j][0] <= tol;
      out.push(ok ? other[j][1] : null);
    }
    return out;
  }
  function findSensor(pred) {
    for (const dev in S.discovered) for (const s of S.discovered[dev]) {
      if (s.enabled && pred(s)) return [dev, s];
    }
    return [null, null];
  }
  function panelInputs() {
    const [dH2, sH2] = findSensor(s => s.kind === "h2");
    const [dVoc, sVoc] = findSensor(s => s.kind === "voc");
    const [dCo, sCo] = findSensor(s => s.kind === "co");
    const [dCur, sCur] = findSensor(s => s.kind === "current");
    const [dCt, sCt] = findSensor(s => (s.key || "").includes("ncontact"));
    const [dAmb, sAmb] = findSensor(s => s.kind === "temp" && !(s.key || "").includes("ncontact"));
    const [dHum, sHum] = findSensor(s => s.kind === "humidity");
    const [dSmk, sSmk] = findSensor(s => s.kind === "smoke");
    // 현재값 = 최근 3표본 중앙값(단발 글리치로 벤트가 열리지 않게 — storage._panel_inputs와 동일)
    // 읽기 실패가 끼어도 '유효' 3표본을 채우도록 넉넉히 읽는다(2개면 튄 값이 중앙값으로 뽑힘 → 낮은 쪽)
    const lv = (dev, s) => {
      const v = seriesOf(dev, s.key, 8).slice(-3).map(p => p[1]).sort((a, b) => a - b);
      if (v.length) return v.length === 2 ? v[0] : v[Math.floor(v.length / 2)];
      const a = S.readings[K(dev, s.key)] || []; return a.length ? a[a.length - 1].value : null;
    };
    const inputs = {};
    // 기준선('평소')은 최근 30표본(지금 사건 구간)보다 이전 이력 — 사건이 기준선을 오염시키면 z가 줄어든다
    const base = (d, s) => seriesF(d, s.key, 150).slice(0, -30).map(p => p[1]);
    if (sH2) inputs.h2 = { value: lv(dH2, sH2), series: seriesF(dH2, sH2.key, 30), baseline: base(dH2, sH2) };
    if (sVoc) inputs.voc = { value: lv(dVoc, sVoc), series: seriesF(dVoc, sVoc.key, 30), baseline: base(dVoc, sVoc) };
    if (sCo) inputs.co = { value: lv(dCo, sCo), series: seriesF(dCo, sCo.key, 30), baseline: base(dCo, sCo) };
    if (sAmb) inputs.temp = { value: lv(dAmb, sAmb), series: seriesF(dAmb, sAmb.key, 30) };
    inputs.smoke = !!(sSmk && (lv(dSmk, sSmk) || 0) > 0);
    if (sCur && sCt && sAmb) {   // 접점온도 시각 기준 as-of 짝짓기(실제 ts → 분당 기울기 정확)
      const tS = seriesF(dCt, sCt.key, 24);
      const cA = asof(tS, seriesF(dCur, sCur.key, 30)), aA = asof(tS, seriesF(dAmb, sAmb.key, 30)), h = [];
      tS.forEach(([ts, T], i) => { if (cA[i] != null && aA[i] != null) h.push([ts, cA[i], T, aA[i]]); });
      inputs.contact = { current: lv(dCur, sCur), temp: lv(dCt, sCt), ambient: lv(dAmb, sAmb), history: h };
    }
    if (sAmb && sHum) {           // 습도 시각 기준 as-of
      let surf = lv(dAmb, sAmb); const ct = sCt ? lv(dCt, sCt) : null;
      if (ct != null && (surf == null || ct < surf)) surf = ct;
      const hS = seriesF(dHum, sHum.key, 24), tA = asof(hS, seriesF(dAmb, sAmb.key, 30)), h = [];
      hS.forEach(([ts, rh], i) => { if (tA[i] != null) h.push([ts, tA[i], rh, tA[i]]); });
      inputs.dew = { temp: lv(dAmb, sAmb), rh: lv(dHum, sHum), surface: surf, history: h };
    }
    const reps = {
      fire: [dH2, sH2 ? sH2.key : ""],
      contact: [dCt, sCt ? sCt.key : ""],
      dew: [dHum, sHum ? sHum.key : ""],
    };
    // 알고리즘별 준비 상태: 워밍업 전이거나 끊긴 입력이 있으면 그 알고리즘만 보류
    const t = now();
    const status = pairs => {
      const ns = [], spans = [];
      for (const [d, s] of pairs) {
        if (!s) continue;
        const pts = seriesOf(d, s.key, 30);   // 기울기 창만큼 — 유효 표본 수·시간 폭으로 판정
        // 끊김 한계 = max(15초, 보고간격 중앙값×4) — 센서 자기 주기에 맞춤(storage와 동일)
        const gaps = pts.slice(1).map((p, i) => p[0] - pts[i][0]).sort((a, b) => a - b);
        const limit = gaps.length ? Math.max(PREDICT_STALE_MIN, 4 * gaps[Math.floor(gaps.length / 2)]) : PREDICT_STALE_MIN;
        if (!pts.length || t - pts[pts.length - 1][0] > limit) return "데이터 끊김 — 판정 보류(상태 유지)";
        ns.push(pts.length); spans.push(pts[pts.length - 1][0] - pts[0][0]);
      }
      if (!ns.length) return "센서 없음";
      const m = Math.min.apply(null, ns);
      if (m < PREDICT_WARMUP) return `학습 중 ${m}/${PREDICT_WARMUP}표본`;
      const sp = Math.min.apply(null, spans);   // 짧은 창의 기울기는 잡음이 '분당 급상승'으로 부풀려짐
      return sp < PREDICT_MIN_SPAN ? `학습 중 ${Math.floor(sp)}/${PREDICT_MIN_SPAN}초` : null;
    };
    const pending = {};
    [["fire", [[dH2, sH2], [dVoc, sVoc], [dCo, sCo]]],
     ["contact", [[dCur, sCur], [dCt, sCt], [dAmb, sAmb]]],
     ["dew", [[dAmb, sAmb], [dHum, sHum]]]].forEach(([k, pr]) => { const w = status(pr); if (w) pending[k] = w; });
    // 함내온도는 화재에선 보조항 — 끊겼으면 그 항만 빼고 가스로 판정(storage와 동일)
    if (inputs.temp && status([[dAmb, sAmb]]) !== null) delete inputs.temp;
    return { inputs, reps, pending };
  }
  function predAlarmTransition(dev, key, kind, on, detail, clearDetail) {
    if (!dev) return;
    const sk = dev + ":" + key + ":" + kind, prev = predAlarmState[sk] || false;
    if (on && !prev) { openAlarm(dev, key, kind, detail); logEvent(dev, key, kind, detail, "system"); predAlarmState[sk] = true; }
    else if (!on && prev) { closeAlarm(dev, key, kind); logEvent(dev, key, kind + "_clear", clearDetail, "system"); predAlarmState[sk] = false; }
  }
  function actView(s) {
    return { vent: { open: s.ventOpen, mode: s.ventManual ? "manual" : "auto" },
      heater: { on: s.heater, mode: s.dewManual ? "manual" : "auto" },
      fan: { on: s.fan, mode: s.dewManual ? "manual" : "auto" } };
  }
  function tickPredict() {
    if (!S.started) return;
    const t = now();
    const online = Object.keys(S.discovered).some(dev => !S.poweredOff[dev] && S.lastSeen[dev] && (t - S.lastSeen[dev]) < 60);
    if (!online) return;
    const { inputs, reps, pending } = panelInputs();
    if (!Object.keys(inputs).length) return;
    const s = predState("panel-01");

    const c = inputs.contact || {};
    const bl = s.contact, hist = c.history || [];
    let blEvent = null;
    const contact = assessContact(c.current, c.temp, c.ambient, hist,
      bl.ready ? bl.k : null, bl.ready ? null : bl.progress());
    if (!pending.contact && hist.length) {   // 새 표본은 기준 학습기에 — 학습 중 이상 구간은 배우지 않음
      if (bl.ready || contact.stage === "normal") for (const [ts, I, T, Ta] of hist) blEvent = bl.observe(ts, I, T, Ta) || blEvent;
      else bl.skip(hist[hist.length - 1][0]);
    }
    if (blEvent === "ready") logEvent((reps.contact || [""])[0] || "", "", "baseline",
      `접점 발열 기준 학습 완료 — 이제 서서히 풀리는 접점도 감시 (k=${Math.round(bl.k * 1e5) / 1e5})`, "system");
    const sig = { h2: inputs.h2 || {}, voc: inputs.voc || {}, co: inputs.co || {}, temp: inputs.temp || {},
      smoke: !!inputs.smoke, current_abnormal: contact.stage === "danger" && !pending.contact };
    const fire = assessFire(sig);
    // 보류 중이면 액추에이터 상태 유지(안전측: 가스 데이터가 끊겼다고 열린 벤트를 닫지 않음)
    const vAct = (PRED.autovent && !pending.fire) ? ventStep(s, fire.fri, fire.stage, t) : null;
    const d = inputs.dew || {};
    const dew = assessDew(d.temp, d.rh, d.surface, d.history || []);
    const dAct = pending.dew ? null : dewStep(s, dew.stage, t);

    s.last = {
      panel: "panel-01", ts: t,
      fire: { fri: fire.fri, stage: fire.stage, reasons: fire.reasons,
        vent: { open: s.ventOpen, mode: s.ventManual ? "manual" : "auto" }, action: vAct, autovent: PRED.autovent },
      contact: { stage: contact.stage, delta_t: contact.delta_t, expected: contact.expected,
        residual: contact.residual, residual_slope: contact.residual_slope, k: contact.k, reasons: contact.reasons,
        baseline: { status: bl.status, progress: Math.round(bl.progress() * 1000) / 1000,
          k: bl.k == null ? null : Math.round(bl.k * 1e5) / 1e5, learned_at: bl.learned_at }, baseline_event: blEvent },
      dew: { stage: dew.stage, dew_point: dew.dew_point, margin: dew.margin, margin_slope: dew.margin_slope,
        rh: dew.rh, heater: s.heater, fan: s.fan, mode: s.dewManual ? "manual" : "auto", action: dAct, reasons: dew.reasons },
    };
    for (const k in pending) {   // 보류된 알고리즘은 숫자 대신 사유 (predict.assess_panel과 동일)
      const o = s.last[k]; if (!o) continue;
      o.stage = "pending"; o.reasons = [pending[k]];
      ["fri", "residual", "margin"].forEach(f => { if (f in o) o[f] = null; });
    }

    // 보류 중인 알고리즘은 경보 상태를 건드리지 않는다(끊겼다고 '해소'로 닫지 않음)
    if (!pending.fire) predAlarmTransition(reps.fire[0], reps.fire[1], "fire", fire.stage === "danger" || fire.stage === "critical",
      `화재 징조 감지 (FRI ${fire.fri}) — ` + fire.reasons.slice(0, 2).join(" · "), "화재 위험 해소 — 정상 복귀");
    if (!pending.contact) predAlarmTransition(reps.contact[0], reps.contact[1], "contact", contact.stage === "danger",
      "접점 발열 위험 — " + contact.reasons.slice(0, 2).join(" · "), "접점 발열 정상 복귀");
    if (!pending.dew) predAlarmTransition(reps.dew[0], reps.dew[1], "dew", dew.stage === "danger",
      "결로 위험 — " + dew.reasons.slice(0, 2).join(" · "), "결로 위험 해소");

    const rd = reps.fire[0] || "";
    if (vAct === "open") logEvent(rd, "", "vent_open", `🔥 화재 징조 → 벤트 자동 개방 (FRI ${fire.fri})`, "system");
    else if (vAct === "close") logEvent(rd, "", "vent_close", "환기 완료 → 벤트 자동 닫힘", "system");
    else if (vAct === "hold") logEvent(rd, "", "vent_hold", `극한 위험 유지 — 벤트 개방 유지 (FRI ${fire.fri})`, "system");
    const rh2 = reps.dew[0] || "";
    if (dAct === "heater_fan") logEvent(rh2, "", "dew_actuate", "결로 위험 → 히터·팬 가동", "system");
    else if (dAct === "fan") logEvent(rh2, "", "dew_actuate", "습도 상승 → 팬 가동", "system");
    else if (dAct === "off") logEvent(rh2, "", "dew_actuate", "결로 위험 해소 → 히터·팬 정지", "system");
  }
  function predView() {
    const s = PRED.panels["panel-01"];
    if (!s || !s.last) return [];
    const v = JSON.parse(JSON.stringify(s.last));   // 액추에이터 최신 상태 반영(수동 조작 즉시 표시)
    v.fire.vent = { open: s.ventOpen, mode: s.ventManual ? "manual" : "auto" };
    v.dew.heater = s.heater; v.dew.fan = s.fan; v.dew.mode = s.dewManual ? "manual" : "auto";
    return [v];
  }
  function setActuator(panel, actuator, action) {
    const s = predState(panel);
    if (actuator === "vent") {
      if (action === "auto") s.ventManual = false;
      else if (action === "open" || action === "close") { s.ventManual = true; s.ventOpen = action === "open"; }
    } else if (actuator === "heater" || actuator === "fan") {
      if (action === "auto") s.dewManual = false;
      else { if (actuator === "heater") s.heater = action === "on"; else s.fan = action === "on"; s.dewManual = true; }
    }
    const lab = { vent: "벤트", heater: "히터", fan: "팬" }[actuator] || actuator;
    const act = { open: "개방", close: "닫힘", on: "켜기", off: "끄기", auto: "자동복귀" }[action] || action;
    logEvent(panel, "", "actuator", `${lab} 수동 ${act}`, "user");
    return { ok: true, panel, actuators: actView(s) };
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
        (kindOf(b) === "smoke" && b.kind === "alarm"))],
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
          predState(panel).contact.reset();
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
  // 시연용: 콘솔에서 JCC_DEMO.episode("fire"|"contact"|"dew") 로 에피소드를 바로 시작한다.
  window.JCC_DEMO = {
    episode(kind) {
      if (["fire", "contact", "dew"].indexOf(kind) < 0) return "fire | contact | dew 중 하나";
      const w = now(); EPI.kind = kind; EPI.t0 = w; EPI.until = w + EPI_DUR;
      return `예지 에피소드 시작: ${kind} (${EPI_DUR}초)`;
    },
    // 파이썬 코어와의 일치 검사용(server/tests/test_parity.py) — 미러가 갈라지면 테스트가 잡는다
    _core: { slopePerMin, robustZ, assessFire, assessContact, assessDew, dewPoint, ContactBaseline, groupAlarms },
  };
  console.log("[JCC-CCM] 데모 모드: 브라우저 안에서 시뮬레이션이 돕니다 (서버 없음).");
})();
