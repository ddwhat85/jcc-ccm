"""경보 이력 — 지난 경보를 거르고, 오경보가 잦은 센서(기준 검토 후보)를 짚는다.

현장 직원이 확인 때 남긴 원인(cause)이 쌓이면 '어느 센서 기준이 너무 예민한가'가 데이터로 보인다.
범위(고객 계정)는 부르는 쪽이 넘긴 기기·판넬 키로 거른다. 경보는 90일 보관.
"""
from __future__ import annotations

import time

MAX_DAYS = 90
NOISY_MIN = 3            # 오경보·시험 작업이 이만큼 이상이고
NOISY_RATIO = 0.5        # 원인을 적은 경보 중 이 비율 이상이면 '기준 검토'


def _names(storage) -> tuple:
    panel_of, sensor = {}, {}
    for p in storage.list_panels():
        panel_of[p["panel"]] = p["panel_name"]
        for c in p["ccms"]:
            panel_of[c["device_id"]] = p["panel_name"]
            for s in c.get("latest") or []:
                sensor[(c["device_id"], s["sensor_key"])] = s.get("name") or s["sensor_key"]
    return panel_of, sensor


def history(storage, keys=None, days: float = 30, cause: str = "", limit: int = 500) -> dict:
    """keys=None이면 전부. cause: ''(전체) | 'none'(원인 미기록) | real|false|work|other."""
    days = min(MAX_DAYS, max(1.0, float(days)))
    rows = storage.alarms_since(time.time() - days * 86400, keys, 100_000)
    panel_of, sensor = _names(storage)
    if cause == "none":
        rows = [a for a in rows if not a.get("cause")]
    elif cause:
        rows = [a for a in rows if a.get("cause") == cause]
    out = []
    for a in rows[:limit]:
        end = a.get("cleared_at")
        out.append({**a, "panel_name": panel_of.get(a["device_id"], ""),
                    "sensor_name": sensor.get((a["device_id"], a.get("sensor_key") or ""), a.get("sensor_key") or ""),
                    "open": end is None, "minutes": round(((end or time.time()) - a["raised_at"]) / 60, 1)})
    return {"days": days, "total": len(rows), "alarms": out, "noisy": noisy(storage, keys, days)}


def noisy(storage, keys=None, days: float = MAX_DAYS) -> list:
    """같은 센서·종류 경보 중 오경보·시험 작업 기록이 잦은 것 — 경보 기준 조정 검토 후보."""
    rows = storage.alarms_since(time.time() - min(MAX_DAYS, days) * 86400, keys, 100_000)
    panel_of, sensor = _names(storage)
    by: dict = {}
    for a in rows:
        by.setdefault((a["device_id"], a.get("sensor_key") or "", a["kind"]), []).append(a.get("cause") or "")
    out = []
    for (dev, key, kind), cs in by.items():
        fake = sum(1 for c in cs if c in ("false", "work"))
        labeled = sum(1 for c in cs if c)
        if fake >= NOISY_MIN and fake / labeled >= NOISY_RATIO:
            out.append({"device_id": dev, "sensor_key": key, "kind": kind, "panel_name": panel_of.get(dev, ""),
                        "sensor_name": sensor.get((dev, key), key), "total": len(cs), "false": fake,
                        "unlabeled": len(cs) - labeled,
                        "say": f"오경보·시험 작업 {fake}/{labeled}건 — 경보 기준(예지 튜닝 콘솔) 검토"})
    out.sort(key=lambda x: -x["false"])
    return out
