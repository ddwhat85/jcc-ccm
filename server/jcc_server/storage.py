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
"""


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
            rows = []
            for r in readings:
                rows.append((
                    device_id,
                    str(r.get("key", "")),
                    r.get("name", ""),
                    r.get("unit", ""),
                    r.get("value"),
                    1 if r.get("ok") else 0,
                    float(r.get("ts", now)),
                ))
            cur.executemany(
                """INSERT INTO readings
                   (device_id, sensor_key, name, unit, value, ok, ts)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            self._conn.commit()
            return len(rows)

    # ── 읽기 ────────────────────────────────────────────────
    def list_devices(self) -> list[dict]:
        with self._lock:
            devs = self._conn.execute(
                "SELECT device_id, site, panel, panel_name, first_seen, last_seen "
                "FROM devices ORDER BY device_id"
            ).fetchall()
        out = []
        now = time.time()
        for d in devs:
            latest = self.latest_readings(d["device_id"])
            out.append({
                "device_id": d["device_id"],
                "site": d["site"],
                "panel": d["panel"] or "",
                "panel_name": d["panel_name"] or "",
                "first_seen": d["first_seen"],
                "last_seen": d["last_seen"],
                # 마지막 접속이 3주기(넉넉히 60s) 넘으면 오프라인으로 본다
                "online": (now - (d["last_seen"] or 0)) < 60,
                "latest": latest,
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

    def latest_readings(self, device_id: str) -> list[dict]:
        """장비의 센서별 최신값 1개씩."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT sensor_key, name, unit, value, ok, MAX(ts) AS ts
                   FROM readings WHERE device_id = ?
                   GROUP BY sensor_key ORDER BY sensor_key""",
                (device_id,),
            ).fetchall()
        return [dict(r) for r in rows]

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
