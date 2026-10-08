"""외부 게이트웨이 HTTP 수신 — 다른 회사 게이트웨이(Milesight·Banner DXM 등)가 HTTP로 보내는 값을 받는다.

JCC CCM이 아닌 장비도 같은 판넬 감시에 넣기 위한 입구. 직원이 게이트웨이를 등록하면(판넬 지정)
비밀 토큰이 한 번만 보이고, 게이트웨이는 POST /v1/gateway/<번호> 에 'Authorization: Bearer <토큰>'으로 보낸다.

받는 모양(셋 다 됨):
  1) JCC 형식     {"readings": [{"key", "name", "unit", "kind", "value", "ts"}]}
  2) 평평한 값    {"devEUI": "…", "temperature": 25.1, "humidity": 51}   ← Milesight 등 디코딩된 페이로드
  3) 2)의 목록    [{…}, {…}]
장치 구분: devEUI·deviceId·device_id·eui·serial 중 있는 것으로 'gw<번호>-<그 값>', 없으면 'gw<번호>'.
처음 보는 측정 항목은 이름으로 종류를 짐작해(온도·습도·CO2…) 기본 경보 기준과 함께 등록한다 — 모르는 항목은
'기타'(경보 기준 없음)로 두고 직원이 정한다. 토큰은 해시로만 저장한다.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

_SCHEMA = """CREATE TABLE IF NOT EXISTS gateways (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, token_hash TEXT NOT NULL,
    panel TEXT NOT NULL, panel_name TEXT DEFAULT '', site TEXT DEFAULT '', enabled INTEGER DEFAULT 1,
    created_at REAL, last_seen REAL, received INTEGER DEFAULT 0);"""

# 이름(소문자, 밑줄·하이픈 무시) → (종류, 단위, 이름, 주의, 위험) — 기준은 JCC 판넬 센서 제품 기본값과 같은 수준
KNOWN = {
    "temperature": ("temp", "C", "온도", 38, 45), "temp": ("temp", "C", "온도", 38, 45),
    "humidity": ("humidity", "%RH", "습도", 70, 80), "hum": ("humidity", "%RH", "습도", 70, 80),
    "co2": ("co2", "ppm", "CO2", 1000, 2000),
    "tvoc": ("voc", "ppm", "VOC", 200, 1000), "voc": ("voc", "ppm", "VOC", 200, 1000),
    "co": ("co", "ppm", "CO", 50, 200), "h2": ("h2", "%LEL", "수소", 10, 25),
    "current": ("current", "A", "전류", None, None),
    "vibration": ("vibration", "mm/s", "진동", 2.5, 4.0), "velocity": ("vibration", "mm/s", "진동", 2.5, 4.0),
    "pressure": ("other", "hPa", "기압", None, None), "illumination": ("other", "lux", "조도", None, None),
    "distance": ("other", "mm", "거리", None, None), "battery": ("other", "%", "배터리", None, None),
}
_ID_KEYS = ("devEUI", "deveui", "dev_eui", "deviceId", "device_id", "eui", "serial", "sn")
_SKIP = {"ts", "time", "timestamp", "rssi", "snr", "fport", "fcnt", "frequency", "port"} | {k.lower() for k in _ID_KEYS}
MAX_ITEMS = 200


def ensure(storage) -> None:
    with storage._lock:
        storage._conn.execute(_SCHEMA)
        storage._conn.commit()


def _hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def create(storage, name: str, panel: str) -> tuple[int, str]:
    """게이트웨이 등록 → (번호, 토큰). 토큰은 지금만 돌려준다."""
    ensure(storage)
    name, panel = (name or "").strip()[:60], (panel or "").strip()[:80]
    if not name or not panel:
        raise ValueError("게이트웨이 이름과 붙일 판넬이 필요합니다")
    pinfo = next((p for p in storage.list_panels() if p["panel"] == panel), None)
    tok = secrets.token_urlsafe(32)
    with storage._lock:
        cur = storage._conn.execute("INSERT INTO gateways (name, token_hash, panel, panel_name, site, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                                    (name, _hash(tok), panel, (pinfo or {}).get("panel_name") or panel, (pinfo or {}).get("site") or "", time.time()))
        storage._conn.commit()
        return cur.lastrowid, tok


def rotate(storage, gid: int) -> str:
    tok = secrets.token_urlsafe(32)
    with storage._lock:
        if not storage._conn.execute("UPDATE gateways SET token_hash=? WHERE id=?", (_hash(tok), gid)).rowcount:
            raise ValueError("없는 게이트웨이입니다")
        storage._conn.commit()
    return tok


def set_enabled(storage, gid: int, on: bool) -> None:
    with storage._lock:
        if not storage._conn.execute("UPDATE gateways SET enabled=? WHERE id=?", (1 if on else 0, gid)).rowcount:
            raise ValueError("없는 게이트웨이입니다")
        storage._conn.commit()


def list_all(storage) -> list:
    ensure(storage)
    with storage._lock:
        rows = storage._conn.execute("SELECT id, name, panel, panel_name, enabled, created_at, last_seen, received FROM gateways ORDER BY id").fetchall()
    return [dict(r, enabled=bool(r["enabled"])) for r in rows]


def check(storage, gid: int, token: str):
    """토큰이 맞고 켜져 있으면 게이트웨이 행, 아니면 None."""
    ensure(storage)
    with storage._lock:
        r = storage._conn.execute("SELECT * FROM gateways WHERE id=?", (gid,)).fetchone()
    if not r or not r["enabled"] or not token or not hmac.compare_digest(r["token_hash"], _hash(token)):
        return None
    return dict(r)


def _norm(k: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def _known(k: str):
    n = _norm(k)
    if n in KNOWN:
        return KNOWN[n]
    for key, v in KNOWN.items():          # 'temperature_1', 'ambient_temp' 같은 이름
        if len(key) > 3 and key in n:
            return v
    return None


def to_batches(gw: dict, body, now: float) -> list:
    """받은 본문 → storage.ingest에 넣을 묶음들([{device_id, panel, …, readings}]). 숫자 아닌 값은 버린다."""
    items = body if isinstance(body, list) else [body]
    out: dict = {}
    for it in items[:MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        ident = next((str(it[k]) for k in _ID_KEYS if it.get(k) not in (None, "")), "")
        dev = f"gw{gw['id']}" + (f"-{re.sub(r'[^A-Za-z0-9]', '', ident)[:24]}" if ident else "")
        ts = it.get("ts") or it.get("timestamp") or it.get("time")
        ts = float(ts) / (1000.0 if isinstance(ts, (int, float)) and ts > 1e12 else 1.0) if isinstance(ts, (int, float)) else now
        if not (now - 7 * 86400 < ts < now + 300):
            ts = now
        rd = out.setdefault(dev, [])
        if isinstance(it.get("readings"), list):                     # 1) JCC 형식
            for r in it["readings"][:MAX_ITEMS]:
                if isinstance(r, dict) and isinstance(r.get("value"), (int, float)) and not isinstance(r.get("value"), bool) and r.get("key"):
                    k = re.sub(r"[^A-Za-z0-9_]", "_", str(r["key"]))[:40]
                    kn = _known(k)
                    rd.append({"key": k, "name": str(r.get("name") or (kn[2] if kn else k))[:40], "unit": str(r.get("unit") or (kn[1] if kn else ""))[:12],
                               "kind": str(r.get("kind") or (kn[0] if kn else "other"))[:20], "value": float(r["value"]), "ok": True,
                               "ts": float(r["ts"]) if isinstance(r.get("ts"), (int, float)) and now - 7 * 86400 < r["ts"] < now + 300 else ts})
            continue
        for k, v in it.items():                                       # 2) 평평한 값
            if _norm(k) in _SKIP or isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            kn = _known(k)
            key = re.sub(r"[^A-Za-z0-9_]", "_", str(k))[:40]
            rd.append({"key": key, "name": kn[2] if kn else key, "unit": kn[1] if kn else "", "kind": kn[0] if kn else "other",
                       "value": float(v), "ok": True, "ts": ts})
    return [{"device_id": d, "site": gw.get("site") or "", "panel": gw["panel"], "panel_name": gw.get("panel_name") or gw["panel"],
             "source": "gateway", "readings": r} for d, r in out.items() if r]


def register_new(storage, batch: dict) -> int:
    """처음 보는 측정 항목을 탐색 결과(discovered)에 더한다 — 이미 있는 항목·직원이 바꾼 기준은 그대로. 더한 수."""
    dev = batch["device_id"]
    with storage._lock:
        have = {r["sensor_key"] for r in storage._conn.execute("SELECT sensor_key FROM discovered WHERE device_id=?", (dev,))}
        n = 0
        for r in batch["readings"]:
            if r["key"] in have:
                continue
            kn = _known(r["key"])
            storage._conn.execute(
                "INSERT INTO discovered (device_id, sensor_key, name, unit, kind, source, confidence, discovered_at, product, alarm_warn, alarm_max) "
                "VALUES (?, ?, ?, ?, ?, 'gateway', ?, ?, ?, ?, ?)",
                (dev, r["key"], r["name"], r["unit"], r["kind"], "추정" if kn else "미확인", time.time(), "외부 게이트웨이 센서",
                 kn[3] if kn else None, kn[4] if kn else None))
            have.add(r["key"])
            n += 1
        storage._conn.commit()
    return n


def receive(storage, gw: dict, body, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    batches = to_batches(gw, body, now)
    stored = added = 0
    for b in batches:
        added += register_new(storage, b)       # 기준을 먼저 붙여야 첫 묶음부터 경보 판정이 된다
        storage.ingest(b)
        stored += len(b["readings"])
    with storage._lock:
        storage._conn.execute("UPDATE gateways SET last_seen=?, received=received+? WHERE id=?", (now, stored, gw["id"]))
        storage._conn.commit()
    if added:
        storage.log_event(batches[0]["device_id"], "", "gateway", f"외부 게이트웨이 '{gw['name']}' 새 측정 항목 {added}개 등록 — 기준을 확인하세요", source="system")
    return {"ok": True, "devices": len(batches), "stored": stored, "new_sensors": added}
