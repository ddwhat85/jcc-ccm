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

    def close(self) -> None:
        with self._lock:
            self._conn.close()
