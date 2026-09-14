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
    PRIMARY KEY (device_id, sensor_key)
);
"""


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
                           (device_id, sensor_key, name, unit, kind, source, confidence, address, discovered_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (dev, str(s.get("key", "")), s.get("name", ""), s.get("unit", ""),
                         s.get("kind", ""), s.get("source", ""), s.get("confidence", ""),
                         s.get("address"), now),
                    )
                    n += 1
            self._conn.commit()
        return n

    def _discovered_map(self) -> dict:
        """{device_id: [발견 센서 dict...]}"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT device_id, sensor_key, name, unit, kind, source, confidence, address, enabled "
                "FROM discovered ORDER BY device_id, rowid"
            ).fetchall()
        out: dict[str, list[dict]] = {}
        for r in rows:
            out.setdefault(r["device_id"], []).append(dict(r))
        return out

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
                    sensors.append({
                        "sensor_key": s["sensor_key"], "name": s["name"], "unit": s["unit"],
                        "kind": s["kind"], "source": s["source"], "confidence": s["confidence"],
                        "enabled": bool(s.get("enabled", 1)),
                        "value": lv["value"] if lv else None,
                        "ok": lv["ok"] if lv else 0,
                        "ts": lv["ts"] if lv else None,
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
