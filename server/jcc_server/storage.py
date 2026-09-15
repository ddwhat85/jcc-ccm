"""데이터 저장 — SQLite.

표준 라이브러리 sqlite3만 쓴다(설치 불필요, 어디서나 동작). DB 접근을 이 한
파일에 가둬, 나중에 PostgreSQL로 옮길 때 여기만 바꾸면 되게 한다.

스키마
  devices   장비 1행 (마지막 접속 시각·현장명)
  readings  센서 측정 1행 (장비·센서키·값·시각)
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    device_id   TEXT PRIMARY KEY,
    site        TEXT,
    panel       TEXT,
    panel_name  TEXT,
    first_seen  REAL,
    last_seen   REAL
);
CREATE TABLE IF NOT EXISTS readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT NOT NULL,
    sensor_key  TEXT NOT NULL,
    name        TEXT,
    unit        TEXT,
    value       REAL,
    ok          INTEGER NOT NULL,
    ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_readings_dev_sensor_ts
    ON readings (device_id, sensor_key, ts);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings (ts);
CREATE TABLE IF NOT EXISTS discovered (
    device_id     TEXT NOT NULL,
    sensor_key    TEXT NOT NULL,
    name          TEXT,
    unit          TEXT,
    kind          TEXT,
    source        TEXT,
    confidence    TEXT,
    address       INTEGER,
    discovered_at REAL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    brand         TEXT,
    product       TEXT,
    part_no       TEXT,
    manual        TEXT,
    alarm_min     REAL,
    alarm_max     REAL,
    PRIMARY KEY (device_id, sensor_key)
);
-- 사용자 설정(셋팅값·알람 기준값 재정의). 재탐색해도 살아남게 별도 테이블에 둔다.
CREATE TABLE IF NOT EXISTS settings (
    device_id   TEXT NOT NULL,
    sensor_key  TEXT NOT NULL,
    setpoint    REAL,
    alarm_min   REAL,
    alarm_max   REAL,
    updated_at  REAL,
    PRIMARY KEY (device_id, sensor_key)
);
-- 활동 기록(감사 로그): 재시작·전원끄기·채널 켜기끄기·설정변경·자가진단 등.
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    device_id   TEXT,
    sensor_key  TEXT,
    etype       TEXT NOT NULL,
    detail      TEXT,
    source      TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_dev_ts ON events (device_id, ts);
"""

# 구버전 DB에 없던 컬럼을 채운다(있으면 조용히 무시).
_MIGRATIONS = [
    "ALTER TABLE discovered ADD COLUMN brand TEXT",
    "ALTER TABLE discovered ADD COLUMN product TEXT",
    "ALTER TABLE discovered ADD COLUMN part_no TEXT",
    "ALTER TABLE discovered ADD COLUMN manual TEXT",
    "ALTER TABLE discovered ADD COLUMN alarm_min REAL",
    "ALTER TABLE discovered ADD COLUMN alarm_max REAL",
]


def _as_float(v, default: float) -> float:
    """숫자로 바꿀 수 있으면 float, 아니면 default. 잘못된 값이 배치를 깨지 않게."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_float_or_none(v):
    """센서 값: 숫자면 float, None이나 비숫자면 None(=결측)."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class Storage:
    path: str

    def __post_init__(self) -> None:
        # check_same_thread=False + 락으로 ThreadingHTTPServer의 여러 스레드에서 공유.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")   # 동시 읽기/쓰기 견고
        self._lock = threading.Lock()
        self._alarm_state: dict = {}   # (device_id, key) -> "ok"|"alarm"  경보 전이 감지용
        self._live_state: dict = {}    # ("dev",id)/("sen",id,key) -> "up"|"down"  침묵 전이 감지용
        with self._lock:
            self._conn.executescript(_SCHEMA)
            for stmt in _MIGRATIONS:
                try:
                    self._conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # 이미 있는 컬럼
            self._conn.commit()

    # ── 쓰기 ────────────────────────────────────────────────
    def ingest(self, payload: dict) -> int:
        """펌웨어가 보낸 텔레메트리 한 묶음을 저장. 저장한 reading 수를 돌려준다."""
        device_id = str(payload.get("device_id") or "unknown")
        site = str(payload.get("site") or "")
        panel = str(payload.get("panel") or "")
        panel_name = str(payload.get("panel_name") or "")
        readings = payload.get("readings") or []
        now = time.time()

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """INSERT INTO devices (device_id, site, panel, panel_name, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(device_id) DO UPDATE SET
                     site=excluded.site, panel=excluded.panel,
                     panel_name=excluded.panel_name, last_seen=excluded.last_seen""",
                (device_id, site, panel, panel_name, now, now),
            )
            disabled = {
                r["sensor_key"] for r in self._conn.execute(
                    "SELECT sensor_key FROM discovered WHERE device_id=? AND enabled=0",
                    (device_id,)).fetchall()
            }
            rows = []
            for r in readings:
                if not isinstance(r, dict):
                    continue  # 형식이 깨진 reading은 건너뛰되 나머지는 살린다
                if str(r.get("key", "")) in disabled:
                    continue  # 비활성 채널: CCM이 멈춘 것으로 취급, 텔레메트리 버림
                rows.append((
                    device_id,
                    str(r.get("key", "")),
                    r.get("name", ""),
                    r.get("unit", ""),
                    _as_float_or_none(r.get("value")),
                    1 if r.get("ok") else 0,
                    _as_float(r.get("ts"), now),
                ))
            cur.executemany(
                """INSERT INTO readings
                   (device_id, sensor_key, name, unit, value, ok, ts)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            # 경보 전이 감지: 값이 알람 범위를 벗어나거나 정상 복귀할 때만 이벤트로 남긴다
            # (같은 락 안에서 직접 INSERT — log_event를 부르면 락 재진입 데드락).
            thr = {}
            for row in self._conn.execute(
                    "SELECT sensor_key, alarm_min, alarm_max FROM discovered WHERE device_id=?",
                    (device_id,)):
                thr[row["sensor_key"]] = (row["alarm_min"], row["alarm_max"])
            for row in self._conn.execute(
                    "SELECT sensor_key, alarm_min, alarm_max FROM settings WHERE device_id=?",
                    (device_id,)):
                base = thr.get(row["sensor_key"], (None, None))
                thr[row["sensor_key"]] = (
                    row["alarm_min"] if row["alarm_min"] is not None else base[0],
                    row["alarm_max"] if row["alarm_max"] is not None else base[1])
            for r in readings:
                if not isinstance(r, dict):
                    continue
                key = str(r.get("key", ""))
                v = _as_float_or_none(r.get("value"))
                if v is None or key in disabled or key not in thr:
                    continue
                amin, amax = thr[key]
                out = (amin is not None and v < amin) or (amax is not None and v > amax)
                state = "alarm" if out else "ok"
                prev = self._alarm_state.get((device_id, key), "ok")
                if state != prev:
                    self._alarm_state[(device_id, key)] = state
                    nm = r.get("name", key); un = r.get("unit", "")
                    if state == "alarm":
                        lim = f"{amax} 초과" if (amax is not None and v > amax) else f"{amin} 미만"
                        detail = f"{nm} {v}{un} — 알람({lim})"
                        etype = "alarm"
                    else:
                        detail = f"{nm} {v}{un} — 정상 복귀"
                        etype = "alarm_clear"
                    cur.execute(
                        "INSERT INTO events (ts, device_id, sensor_key, etype, detail, source) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (now, device_id, key, etype, detail, "system"))
            self._conn.commit()
            return len(rows)

    # ── 자동 탐색 결과 ──────────────────────────────────────
    def set_discovery(self, result: dict) -> int:
        """자동 탐색 인벤토리를 저장한다. 장비를 upsert하고 발견 센서를 교체한다."""
        now = time.time()
        panel = str(result.get("panel") or "")
        panel_name = str(result.get("panel_name") or "")
        site = str(result.get("site") or "")
        n = 0
        with self._lock:
            cur = self._conn.cursor()
            for ccm in result.get("ccms") or []:
                dev = str(ccm.get("device_id") or "")
                if not dev:
                    continue
                cur.execute(
                    """INSERT INTO devices (device_id, site, panel, panel_name, first_seen, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(device_id) DO UPDATE SET
                         site=excluded.site, panel=excluded.panel, panel_name=excluded.panel_name""",
                    (dev, site, panel, panel_name, now, 0),
                )
                cur.execute("DELETE FROM discovered WHERE device_id = ?", (dev,))
                for s in ccm.get("sensors") or []:
                    cur.execute(
                        """INSERT INTO discovered
                           (device_id, sensor_key, name, unit, kind, source, confidence, address,
                            discovered_at, brand, product, part_no, manual, alarm_min, alarm_max)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (dev, str(s.get("key", "")), s.get("name", ""), s.get("unit", ""),
                         s.get("kind", ""), s.get("source", ""), s.get("confidence", ""),
                         s.get("address"), now,
                         s.get("brand", ""), s.get("product", ""), s.get("part_no", ""),
                         s.get("manual", ""),
                         _as_float_or_none(s.get("alarm_min")), _as_float_or_none(s.get("alarm_max"))),
                    )
                    n += 1
            self._conn.commit()
        return n

    def _discovered_map(self) -> dict:
        """{device_id: [발견 센서 dict...]}"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT device_id, sensor_key, name, unit, kind, source, confidence, address, enabled, "
                "brand, product, part_no, manual, alarm_min, alarm_max "
                "FROM discovered ORDER BY device_id, rowid"
            ).fetchall()
        out: dict[str, list[dict]] = {}
        for r in rows:
            out.setdefault(r["device_id"], []).append(dict(r))
        return out

    def _settings_map(self) -> dict:
        """{(device_id, sensor_key): {setpoint, alarm_min, alarm_max}} 사용자 재정의."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT device_id, sensor_key, setpoint, alarm_min, alarm_max FROM settings"
            ).fetchall()
        return {(r["device_id"], r["sensor_key"]): dict(r) for r in rows}

    def set_setting(self, device_id: str, sensor_key: str,
                    setpoint=None, alarm_min=None, alarm_max=None) -> dict:
        """센서의 사용자 설정(셋팅값·알람 상/하한)을 upsert. 넘어온 필드만 갱신한다.
        None은 '변경 없음', 빈 문자열은 '해제(기본값으로 복귀)'로 다룬다."""
        def norm(v):
            if v is None:
                return "keep"        # 이번엔 안 건드림
            if v == "" or v == "null":
                return None          # 해제 → NULL
            return _as_float_or_none(v)
        sp, amin, amax = norm(setpoint), norm(alarm_min), norm(alarm_max)
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "SELECT setpoint, alarm_min, alarm_max FROM settings WHERE device_id=? AND sensor_key=?",
                (device_id, sensor_key)).fetchone()
            old = dict(cur) if cur else {"setpoint": None, "alarm_min": None, "alarm_max": None}
            new = {
                "setpoint":  old["setpoint"]  if sp == "keep"   else sp,
                "alarm_min": old["alarm_min"] if amin == "keep" else amin,
                "alarm_max": old["alarm_max"] if amax == "keep" else amax,
            }
            self._conn.execute(
                """INSERT INTO settings (device_id, sensor_key, setpoint, alarm_min, alarm_max, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(device_id, sensor_key) DO UPDATE SET
                     setpoint=excluded.setpoint, alarm_min=excluded.alarm_min,
                     alarm_max=excluded.alarm_max, updated_at=excluded.updated_at""",
                (device_id, sensor_key, new["setpoint"], new["alarm_min"], new["alarm_max"], now),
            )
            self._conn.commit()
        return new

    def set_channel(self, device_id: str, sensor_key: str, enabled: bool) -> bool:
        """센서 채널을 활성/비활성한다. = CCM에 그 채널을 켜고/끄라는 명령.
        비활성이면 이후 그 채널의 텔레메트리는 ingest에서 버려진다(하드웨어가 멈춘 것과 동일)."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE discovered SET enabled=? WHERE device_id=? AND sensor_key=?",
                (1 if enabled else 0, device_id, sensor_key),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def device_command(self, device_id: str, action: str) -> bool:
        """CCM에 전원 명령을 보낸다(재시작/전원끄기). = 실기에서는 SSH로 reboot/poweroff.

        시뮬레이션에서는 그 즉시 장비를 오프라인 처리(last_seen=0)해, 명령이 하드웨어에
        실제로 먹혔음을 화면에 반영한다. 재시작한 CCM은 다시 접속해 텔레메트리를 올리면
        자동으로 온라인으로 돌아온다."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE devices SET last_seen=0 WHERE device_id=?", (device_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def enable_all_channels(self) -> int:
        with self._lock:
            cur = self._conn.execute("UPDATE discovered SET enabled=1 WHERE enabled=0")
            self._conn.commit()
            return cur.rowcount

    def _disabled_set(self) -> set:
        with self._lock:
            rows = self._conn.execute(
                "SELECT device_id, sensor_key FROM discovered WHERE enabled=0"
            ).fetchall()
        return {(r["device_id"], r["sensor_key"]) for r in rows}

    # ── 읽기 ────────────────────────────────────────────────
    def list_devices(self) -> list[dict]:
        # 장비 목록과 전 장비의 센서 최신값을 각각 한 번의 쿼리로 가져온다(N+1 제거).
        with self._lock:
            devs = self._conn.execute(
                "SELECT device_id, site, panel, panel_name, first_seen, last_seen "
                "FROM devices ORDER BY device_id"
            ).fetchall()
            # 장비×센서별 최신 행 하나씩. MAX(ts)와 함께 오는 bare 컬럼은
            # SQLite가 그 최대 행의 값으로 채운다(3.7.11+ 보장).
            latest_rows = self._conn.execute(
                """SELECT device_id, sensor_key, name, unit, value, ok, MAX(ts) AS ts
                   FROM readings GROUP BY device_id, sensor_key
                   ORDER BY device_id, sensor_key"""
            ).fetchall()

        latest_map: dict[str, dict[str, dict]] = {}   # device -> sensor_key -> latest row
        for r in latest_rows:
            latest_map.setdefault(r["device_id"], {})[r["sensor_key"]] = {
                "sensor_key": r["sensor_key"], "name": r["name"], "unit": r["unit"],
                "value": r["value"], "ok": r["ok"], "ts": r["ts"],
            }

        disc_map = self._discovered_map()
        set_map = self._settings_map()
        now = time.time()
        out = []
        for d in devs:
            dev = d["device_id"]
            latest = latest_map.get(dev, {})
            discovered = disc_map.get(dev)
            if discovered:
                # 탐색된 장비: 발견 센서가 센서 집합의 기준. 텔레메트리 값이 있으면 채운다.
                sensors = []
                for s in discovered:
                    lv = latest.get(s["sensor_key"])
                    us = set_map.get((dev, s["sensor_key"]), {})
                    # 알람 기준값: 사용자 설정이 있으면 우선, 없으면 프로파일 기본값.
                    eff_min = us["alarm_min"] if us.get("alarm_min") is not None else s.get("alarm_min")
                    eff_max = us["alarm_max"] if us.get("alarm_max") is not None else s.get("alarm_max")
                    sensors.append({
                        "sensor_key": s["sensor_key"], "name": s["name"], "unit": s["unit"],
                        "kind": s["kind"], "source": s["source"], "confidence": s["confidence"],
                        "address": s.get("address"),
                        "enabled": bool(s.get("enabled", 1)),
                        "value": lv["value"] if lv else None,
                        "ok": lv["ok"] if lv else 0,
                        "ts": lv["ts"] if lv else None,
                        # 제품정보(AI 자동 식별) + 설정/알람
                        "brand": s.get("brand") or "", "product": s.get("product") or "",
                        "part_no": s.get("part_no") or "", "manual": s.get("manual") or "",
                        "alarm_min": eff_min, "alarm_max": eff_max,
                        "alarm_min_default": s.get("alarm_min"), "alarm_max_default": s.get("alarm_max"),
                        "setpoint": us.get("setpoint"),
                    })
            else:
                # 탐색 전 장비: 예전처럼 텔레메트리 최신값만
                sensors = list(latest.values())
            out.append({
                "device_id": dev,
                "site": d["site"],
                "panel": d["panel"] or "",
                "panel_name": d["panel_name"] or "",
                "first_seen": d["first_seen"],
                "last_seen": d["last_seen"],
                "online": (now - (d["last_seen"] or 0)) < 60,
                "discovered": bool(discovered),
                "latest": sensors,
            })
        return out

    def list_panels(self) -> list[dict]:
        """장비를 판넬 단위로 묶어 돌려준다. panel이 빈 CCM은 자기 자신을 판넬로 취급."""
        devices = self.list_devices()
        panels: dict[str, dict] = {}
        order: list[str] = []
        for d in devices:
            pid = d["panel"] or f"__solo__{d['device_id']}"
            if pid not in panels:
                panels[pid] = {
                    "panel": d["panel"] or d["device_id"],
                    "panel_name": d["panel_name"] or d["device_id"],
                    "site": d["site"],
                    "ccms": [],
                }
                order.append(pid)
            panels[pid]["ccms"].append(d)
        # 판넬 온라인 = 소속 CCM 중 하나라도 온라인
        result = []
        for pid in order:
            p = panels[pid]
            p["online"] = any(c["online"] for c in p["ccms"])
            p["ccm_count"] = len(p["ccms"])
            result.append(p)
        return result

    def history(self, device_id: str, sensor_key: str, limit: int = 200) -> list[dict]:
        """한 센서의 최근 이력(오래된→최신 순으로 반환)."""
        limit = max(1, min(limit, 5000))
        with self._lock:
            rows = self._conn.execute(
                """SELECT value, ok, ts FROM readings
                   WHERE device_id = ? AND sensor_key = ?
                   ORDER BY ts DESC LIMIT ?""",
                (device_id, sensor_key, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ── 활동 기록 (감사 로그) ────────────────────────────────
    def log_event(self, device_id: str, sensor_key: str, etype: str,
                  detail: str = "", source: str = "user") -> None:
        """조작/사건을 한 줄 기록한다. 락을 쥔 다른 메서드 안에서 부르지 말 것
        (재진입 데드락) — 반드시 락 밖(핸들러 계층)에서 호출한다."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, device_id, sensor_key, etype, detail, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), device_id, sensor_key or "", etype, detail, source))
            self._conn.commit()

    def list_events(self, device_id: str = "", sensor_key: str = "",
                    limit: int = 50) -> list[dict]:
        """최근 이벤트(최신순). device_id/sensor_key로 좁힐 수 있다.
        sensor_key를 주면 그 센서의 이벤트 + 소속 CCM 단위 이벤트도 함께 본다."""
        limit = max(1, min(limit, 500))
        q = "SELECT ts, device_id, sensor_key, etype, detail, source FROM events"
        cond, args = [], []
        if device_id:
            cond.append("device_id = ?"); args.append(device_id)
        if sensor_key:
            cond.append("sensor_key = ?"); args.append(sensor_key)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY ts DESC LIMIT ?"; args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    # ── 자가진단 ────────────────────────────────────────────
    def diagnose(self, device_id: str, sensor_key: str = "") -> dict:
        """센서/CCM 자가진단. 지금 가진 데이터로 통신·값·범위·식별을 점검한다.

        실기에서는 여기에 장치 자체 점검(CCM system_check.sh, 센서 Modbus 진단
        레지스터)을 더 붙인다. 시뮬에서는 수집 상태로 건강도를 평가한다."""
        now = time.time()
        dev = next((d for d in self.list_devices() if d["device_id"] == device_id), None)
        if not dev:
            return {"ok": False, "summary": "장치를 찾을 수 없음", "checks": []}

        checks: list[dict] = []

        def add(name, status, detail):  # status: pass|warn|fail
            checks.append({"name": name, "status": status, "detail": detail})

        if sensor_key:
            s = next((x for x in dev["latest"] if x.get("sensor_key") == sensor_key), None)
            if not s:
                return {"ok": False, "summary": "센서를 찾을 수 없음", "checks": []}
            # 1) 채널 활성
            if s.get("enabled") is False:
                add("채널 상태", "warn", "채널이 꺼져 있음(수집 중지)")
            else:
                add("채널 상태", "pass", "활성")
            # 2) 수신 신선도
            ts = s.get("ts")
            if ts and (now - ts) < 30:
                add("데이터 수신", "pass", f"{int(now - ts)}초 전 수신")
            elif ts:
                add("데이터 수신", "warn", f"{int(now - ts)}초간 갱신 없음")
            else:
                add("데이터 수신", "fail" if s.get("enabled") is not False else "warn", "수신 이력 없음")
            # 3) 값 유효성
            v = s.get("value")
            if v is not None and s.get("ok"):
                add("값 유효성", "pass", "정상 측정")
            elif s.get("enabled") is False:
                add("값 유효성", "warn", "채널 꺼짐으로 값 없음")
            else:
                add("값 유효성", "fail", "값 없음/읽기 오류")
            # 4) 알람 범위
            amin, amax = s.get("alarm_min"), s.get("alarm_max")
            if v is not None and amin is not None and amax is not None:
                if v < amin or v > amax:
                    add("측정 범위", "fail", f"알람 범위({amin}~{amax}) 벗어남: {v}")
                else:
                    add("측정 범위", "pass", f"정상 범위({amin}~{amax}) 내")
            # 5) 변동성(고착·드리프트) — 살아있어도 못 믿는 센서 색출
            st = self.history_stats(device_id, sensor_key)
            if st["stuck"]:
                add("변동성", "fail", "값이 고정됨 — 센서 고착/동결 의심")
            elif st["drift"]:
                add("추세", "warn", f"값 지속 이동({st['drift_pct']:+}%) — 드리프트/보정 필요")
            elif st["n"] >= 6:
                add("변동성", "pass", "정상 변동")
            # 6) 식별 신뢰도
            if s.get("confidence") == "추정":
                add("장치 식별", "warn", "추정 장치 — 실기 모델 확인 권장")
            else:
                add("장치 식별", "pass", "프로파일 확정")
            target = s.get("name") or sensor_key
        else:
            # CCM 진단
            if dev.get("online"):
                add("연결 상태", "pass", "온라인")
            else:
                add("연결 상태", "fail", "오프라인 — 접속 없음")
            sensors = dev.get("latest") or []
            total = len(sensors)
            live = sum(1 for x in sensors if x.get("ts") and (now - x["ts"]) < 30)
            off = sum(1 for x in sensors if x.get("enabled") is False)
            if total == 0:
                add("센서 응답", "warn", "연결된 센서 없음")
            elif live == total - off:
                add("센서 응답", "pass", f"{live}/{total} 정상 수신")
            elif live > 0:
                add("센서 응답", "warn", f"{live}/{total}만 수신 중")
            else:
                add("센서 응답", "fail", f"{total}개 중 수신 0")
            if off:
                add("채널 상태", "warn", f"꺼진 채널 {off}개")
            else:
                add("채널 상태", "pass", "모든 채널 활성")
            ls = dev.get("last_seen")
            if ls:
                add("마지막 접속", "pass" if (now - ls) < 60 else "warn",
                    f"{int(now - ls)}초 전")
            target = device_id

        worst = "fail" if any(c["status"] == "fail" for c in checks) else \
                ("warn" if any(c["status"] == "warn" for c in checks) else "pass")
        summary = {"pass": "정상", "warn": "주의 필요", "fail": "이상 감지"}[worst]
        return {"ok": True, "device_id": device_id, "sensor_key": sensor_key,
                "target": target, "status": worst, "summary": summary, "checks": checks}

    # ── 센서 건강도 분석 (고착·드리프트) ────────────────────
    def history_stats(self, device_id: str, sensor_key: str, n: int = 30) -> dict:
        """최근 이력으로 센서의 '믿을 수 있는지'를 본다.
        - 고착(stuck): 최근 값이 전혀 안 변함 → 센서 동결/케이블 단선 후 마지막값 유지.
        - 드리프트(drift): 값이 한 방향으로 꾸준히 이동 → 보정 필요/열화 전조.
        살아는 있어도 못 믿는 센서를 잡는 게 자가진단의 핵심."""
        pts = self.history(device_id, sensor_key, n)   # 오래된→최신
        vals = [p["value"] for p in pts if p.get("ok") and p.get("value") is not None]
        res = {"n": len(vals), "stuck": False, "drift": False, "drift_pct": 0.0}
        if len(vals) < 6:
            return res
        recent = vals[-8:]
        if max(recent) - min(recent) == 0:            # 최근 값이 전부 동일
            res["stuck"] = True
            return res
        if len(vals) >= 15:                            # 3등분 단조 이동이면 드리프트
            k = len(vals) // 3
            a = sum(vals[:k]) / k
            b = sum(vals[k:2 * k]) / k
            c = sum(vals[2 * k:]) / (len(vals) - 2 * k)
            scale = max(abs((a + c) / 2), 1e-6)
            pct = (c - a) / scale
            monotonic = (a <= b <= c) or (a >= b >= c)
            if monotonic and abs(pct) > 0.25:
                res["drift"] = True
                res["drift_pct"] = round(pct * 100, 1)
        return res

    def health_scan(self, sensor_timeout: float = 45) -> int:
        """살아있는(최근 수신) 센서의 고착·드리프트를 주기적으로 감지해 전이만 기록한다."""
        now = time.time()
        with self._lock:
            latest = {(r["device_id"], r["sensor_key"]): r["ts"] for r in self._conn.execute(
                "SELECT device_id, sensor_key, MAX(ts) AS ts FROM readings "
                "GROUP BY device_id, sensor_key").fetchall()}
            rows = self._conn.execute(
                "SELECT device_id, sensor_key, name, enabled FROM discovered").fetchall()
            sensors = [dict(r) for r in rows]
        to_log: list[tuple] = []
        for s in sensors:
            if not s.get("enabled", 1):
                continue
            dev, key, nm = s["device_id"], s["sensor_key"], (s.get("name") or s["sensor_key"])
            lt = latest.get((dev, key))
            if lt is None or (now - lt) >= sensor_timeout:
                continue                               # 침묵 센서는 침묵 감시가 담당
            st = self.history_stats(dev, key)
            state = "stuck" if st["stuck"] else ("drift" if st["drift"] else "ok")
            hkey = ("health", dev, key)
            prev = self._live_state.get(hkey, "ok")
            if state != prev:
                if state == "stuck":
                    to_log.append((dev, key, "stuck", f"{nm} 값이 고정됨 — 센서 고착 의심"))
                elif state == "drift":
                    to_log.append((dev, key, "drift", f"{nm} 값 지속 이동({st['drift_pct']:+}%) — 드리프트 의심"))
                elif prev in ("stuck", "drift"):
                    to_log.append((dev, key, "stuck_clear", f"{nm} 변동 정상화"))
                self._live_state[hkey] = state
        for dev, key, et, detail in to_log:
            self.log_event(dev, key, et, detail, source="system")
        return len(to_log)

    # ── 침묵 감지 (하트비트 watchdog) ────────────────────────
    def liveness_scan(self, device_timeout: float = 60, sensor_timeout: float = 45) -> int:
        """CCM/센서가 조용해졌는지 검사한다. '값 정상'이 아니라 '데이터가 안 옴'을 잡는다.

        온라인→침묵, 침묵→복구 '전이'가 있을 때만 이벤트로 남긴다(도배 방지).
        - CCM 침묵: last_seen이 device_timeout 넘게 갱신 안 됨 → 최우선 경보.
        - 센서 침묵: CCM은 살아있는데 그 센서만 sensor_timeout 넘게 조용 → 죽은 센서 하나 색출.
        한 번도 보고 안 한 센서/장비는 '침묵'이 아니라 '데이터 대기'로 보고 경보하지 않는다.
        """
        now = time.time()
        with self._lock:
            devs = {r["device_id"]: (r["last_seen"] or 0) for r in
                    self._conn.execute("SELECT device_id, last_seen FROM devices").fetchall()}
            latest = {(r["device_id"], r["sensor_key"]): r["ts"] for r in self._conn.execute(
                "SELECT device_id, sensor_key, MAX(ts) AS ts FROM readings "
                "GROUP BY device_id, sensor_key").fetchall()}
            disc: dict[str, list[dict]] = {}
            for r in self._conn.execute(
                    "SELECT device_id, sensor_key, name, enabled FROM discovered").fetchall():
                disc.setdefault(r["device_id"], []).append(dict(r))

        to_log: list[tuple] = []
        for dev, sensors in disc.items():
            ls = devs.get(dev, 0)
            dev_up = bool(ls) and (now - ls) < device_timeout
            dkey = ("dev", dev)
            prev = self._live_state.get(dkey)
            if dev_up:
                if prev == "down":
                    to_log.append((dev, "", "recovered", f"CCM {dev} 통신 복구"))
                self._live_state[dkey] = "up"
            else:
                if prev == "up":
                    to_log.append((dev, "", "silent", f"CCM {dev} 응답 없음 — {int(now - ls)}초 침묵"))
                    self._live_state[dkey] = "down"
                elif prev is None:
                    self._live_state[dkey] = "down"   # 시작이 침묵이면 조용히 기록(오탐 방지)

            for s in sensors:                          # CCM이 살아있을 때만 센서 침묵을 따진다
                if not s.get("enabled", 1):
                    continue                           # 사용자가 끈 센서는 침묵 아님
                key = s["sensor_key"]
                lt = latest.get((dev, key))
                if lt is None:
                    continue                           # 한 번도 보고 안 함 → 데이터 대기
                skey = ("sen", dev, key)
                sprev = self._live_state.get(skey)
                nm = s.get("name") or key
                if dev_up and (now - lt) < sensor_timeout:
                    if sprev == "down":
                        to_log.append((dev, key, "recovered", f"{nm} 데이터 복구"))
                    self._live_state[skey] = "up"
                elif dev_up:                           # CCM은 사는데 이 센서만 조용
                    if sprev == "up":
                        to_log.append((dev, key, "silent", f"{nm} {int(now - lt)}초째 데이터 없음"))
                        self._live_state[skey] = "down"
                    elif sprev is None:
                        self._live_state[skey] = "down"

        for dev, key, et, detail in to_log:            # 락 밖에서 로깅(재진입 회피)
            self.log_event(dev, key, et, detail, source="system")
        return len(to_log)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
