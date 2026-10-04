"""감시 끊김 기록 — 언제부터 언제까지, 왜(통신·전원·센서), 그동안 판넬이 스스로 지켰는지.

끊김 = '침묵' 경보(alarms.kind='silent'). sensor_key가 비면 CCM 전체, 있으면 그 센서 하나.
CCM 전체 끊김의 원인은 기록으로 가린다(지어내지 않는다):
  - JCC가 재시작·전원 명령을 보냈다            → operator('JCC 작업')
  - 응답이 없어 서버가 원격 재시작으로 살렸다   → heal
  - 끊긴 동안의 값이 나중에 대부분 올라왔다     → network: CCM은 켜져 있었다 = 판넬이 현장에서 스스로 지켰다
    (CCM은 통신이 끊기면 값을 큐에 쌓았다가 다시 연결되면 올린다)
  - 그 사이 CCM이 새로 켜졌다(부팅 시각이 바뀜) → power: 전원이 꺼졌다 켜짐/재시작 = 그동안 감시·보호 없음
  - 둘 다 아님                                 → stop: 기록 없음(전원 또는 장치 확인)
다시 연결된 직후엔 쌓인 값이 아직 올라오는 중일 수 있어, 회복 후 10분이 지나야 원인을 굳혀 저장한다.
"""
from __future__ import annotations

import re
import time

BUCKET = 300           # 5분 칸마다 값이 하나라도 있으면 '기록 있음'
COVER = 0.8            # 끊긴 동안 칸의 80% 이상이 채워졌으면 통신만 끊긴 것
SETTLE = 600           # 회복 후 이만큼 지나야 원인을 굳힌다
_SCHEMA = """CREATE TABLE IF NOT EXISTS outages (alarm_id INTEGER PRIMARY KEY, kind TEXT, covered REAL, decided_at REAL);"""

WORD = {
    "network": ("통신 끊김", True, "감시 장치는 켜져 있어 판넬이 현장에서 스스로 지켰고, 그동안의 기록은 다시 연결된 뒤 받았습니다."),
    "power": ("감시 장치 전원 꺼짐·재시작", False, "그동안 감시와 판넬 자동 보호가 없었습니다. 전원 차단·정전 여부를 확인해 주세요."),
    "stop": ("감시 장치 멈춤", False, "그동안의 기록이 없습니다. 전원이나 장치 상태를 JCC가 확인합니다."),
    "operator": ("JCC 작업(원격 재시작·점검)", None, "JCC가 작업하느라 잠시 끊겼습니다."),
    "heal": ("응답 없음 → 원격 재시작으로 복구", None, "응답이 없어 JCC 서버가 감시 장치를 원격으로 다시 켰습니다."),
    "sensor": ("센서 데이터 없음", None, "이 센서만 값이 오지 않았습니다. 케이블이 빠졌거나 센서 고장일 수 있습니다."),
    "ongoing": ("지금 끊겨 있음", None, "다시 연결되면 원인을 알려 드립니다."),
}


def ensure(storage) -> None:
    with storage._lock:
        storage._conn.execute(_SCHEMA)
        storage._conn.commit()


def _gap_start(a) -> float:
    """CCM 침묵 경보는 마지막 수신 + 대기 시간 뒤에 열린다 — 실제로 끊긴 때는 그만큼 앞."""
    m = re.search(r"(\d+)초 침묵", a["detail"] or "")
    return a["raised_at"] - (int(m.group(1)) if m else 60)


def classify(storage, a: dict, now: float | None = None) -> tuple:
    """(kind, covered) — 경보 한 건의 원인."""
    now = time.time() if now is None else now
    if a.get("sensor_key"):
        return "sensor", None
    if not a.get("cleared_at"):
        return "ongoing", None
    dev, start, end = a["device_id"], _gap_start(a), a["cleared_at"]
    with storage._lock:
        ev = {r["etype"] for r in storage._conn.execute(
            "SELECT etype FROM events WHERE device_id=? AND ts >= ? AND ts <= ? AND etype IN ('restart', 'shutdown', 'heal_restart')",
            (dev, start - 300, end + 60))}
        n = storage._conn.execute("SELECT COUNT(DISTINCT CAST(ts / ? AS INTEGER)) AS n FROM readings WHERE device_id=? AND ts >= ? AND ts < ?",
                                  (BUCKET, dev, start, end)).fetchone()["n"]
        boot = storage._conn.execute("SELECT 1 FROM ccm_boots WHERE device_id=? AND boot_ts >= ? AND boot_ts <= ?",
                                     (dev, start - 60, end + 300)).fetchone() is not None
    total = max(1, int((end - start) // BUCKET))
    covered = round(min(1.0, n / total), 2)
    if ev & {"restart", "shutdown"}:
        return "operator", covered
    if "heal_restart" in ev:
        return "heal", covered
    if covered >= COVER:
        return "network", covered
    if boot:
        return "power", covered
    return "stop", covered


def _kind(storage, a: dict, now: float) -> tuple:
    """굳힌 원인이 있으면 그것, 없으면 계산(회복 후 SETTLE이 지났으면 저장)."""
    with storage._lock:
        r = storage._conn.execute("SELECT kind, covered FROM outages WHERE alarm_id=?", (a["id"],)).fetchone()
    if r:
        return r["kind"], r["covered"]
    k, cov = classify(storage, a, now)
    backlog = (getattr(storage, "_backlog", {}).get(a["device_id"]) or (0, 0))[1]
    if a.get("cleared_at") and now - a["cleared_at"] >= SETTLE and not backlog:   # CCM이 밀린 것을 다 보낸 뒤에만 확정
        with storage._lock:
            storage._conn.execute("INSERT OR REPLACE INTO outages (alarm_id, kind, covered, decided_at) VALUES (?, ?, ?, ?)",
                                  (a["id"], k, cov, now))
            storage._conn.commit()
    return k, cov


def list_range(storage, keys: set | None, start: float, end: float, now: float | None = None) -> list:
    """그 기간에 걸친 끊김(최신순). keys = 볼 수 있는 기기·판넬(None이면 전부)."""
    now = time.time() if now is None else now
    ensure(storage)
    q = ("SELECT id, device_id, sensor_key, detail, raised_at, cleared_at FROM alarms WHERE kind='silent' "
         "AND raised_at < ? AND (cleared_at IS NULL OR cleared_at > ?)")
    args: list = [end, start]
    if keys is not None:
        if not keys:
            return []
        frag, fa = storage._in_devices(keys)
        q += " AND " + frag
        args.extend(fa)
    with storage._lock:
        rows = [dict(r) for r in storage._conn.execute(q + " ORDER BY raised_at DESC LIMIT 200", args)]
    names = {}
    for p in storage.list_panels():
        for c in p["ccms"]:
            names[c["device_id"]] = (p["panel"], p["panel_name"], {x["sensor_key"]: x.get("name") or x["sensor_key"] for x in c.get("latest") or []})
    out = []
    for a in rows:
        k, cov = _kind(storage, a, now)
        word, protected, say = WORD[k]
        pid, pname, sn = names.get(a["device_id"], (a["device_id"], a["device_id"], {}))
        s0 = a["raised_at"] if a["sensor_key"] else _gap_start(a)
        e0 = a["cleared_at"] or now
        out.append({"id": a["id"], "panel": pid, "panel_name": pname, "device_id": a["device_id"],
                    "sensor": sn.get(a["sensor_key"], a["sensor_key"]) if a["sensor_key"] else "",
                    "kind": k, "word": word, "protected": protected, "say": say, "covered": cov,
                    "start": s0, "end": a["cleared_at"], "minutes": int(round((e0 - s0) / 60))})
    return out


def unprotected_minutes(storage, devs: set, start: float, end: float, now: float | None = None) -> dict:
    """그 기간 CCM 전체 끊김을 원인별 분(기간에 겹친 만큼)으로: {'network': .., 'power': .., …}."""
    out: dict = {}
    for o in list_range(storage, devs, start, end, now):
        if o["sensor"]:
            continue
        s0, e0 = max(start, o["start"]), min(end, o["end"] or (time.time() if now is None else now))
        out[o["kind"]] = out.get(o["kind"], 0) + max(0, int(round((e0 - s0) / 60)))
    return out
