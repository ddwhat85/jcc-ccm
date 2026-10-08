"""판넬 센서 예측 — 내일 시간별 값을, 그 센서가 남긴 기록의 패턴만으로(온도·습도·전류·진동·가스 각각).

바깥 날씨(기상청)는 아직 쓰지 않는다(나중에 붙인다). 예측 = 같은 요일·같은 시각의 지난 8주 값(중앙값)
+ 최근 3일이 그 패턴보다 얼마나 높았나(흐름, 0.6배로 눌러 반영). 계절은 이 '흐름'이 천천히 따라간다.

고객 화면 원칙(실제 기록 숫자만, 가정은 밝힌다)에 맞게:
  - 예측은 언제나 '예측'·'범위'로 보이고, 지난 30일 동안 실제로 얼마나 맞았는지(평균 오차)를 같이 낸다.
  - 기록이 MIN_DAYS보다 적으면 예측하지 않는다(building).

원본 값(readings)은 14일만 남기므로, 시간별 평균을 hourly 표에 따로 오래 남긴다(rollup, 기본 3년).
판넬 하나의 모든 센서(panel_forecasts): 센서마다 내일 시간별 예측 + 경고선까지 남은 여유(rul.py — 하루 대표값의 추세).
문·연기처럼 켜짐/꺼짐 신호는 값을 예측하지 않는다(실시간 감시로 지킨다).
"""
from __future__ import annotations

import os
import re
import statistics
import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
H, D = 3600, 86400
MIN_DAYS = 7            # 이보다 기록이 적으면 예측하지 않는다
WEEKS = 8               # 같은 요일·시각을 몇 주 거슬러 보나
FLOW_H = 72             # 최근 흐름을 볼 시간
FLOW_DAMP = 0.6         # 최근 흐름을 얼마나 이어 갈까(1이면 그대로)
BACKTEST_DAYS = 30      # 지난 며칠로 오차를 재나
BAND_Q = 0.8            # 지난 오차의 80%가 들어오는 폭을 범위로

_SCHEMA = """CREATE TABLE IF NOT EXISTS hourly (
    device_id TEXT NOT NULL, sensor_key TEXT NOT NULL, hour REAL NOT NULL,
    avg REAL, vmin REAL, vmax REAL, n INTEGER, PRIMARY KEY (device_id, sensor_key, hour));"""


def ensure(storage) -> None:
    with storage._lock:
        storage._conn.execute(_SCHEMA)
        storage._conn.commit()


# ── 시간별 평균 쌓기 ───────────────────────────────────────
def rollup(storage, now: float | None = None, hours: int = 48) -> int:
    """지난 hours 시간의 원본 값을 시간별 평균으로(다 지난 시간만). 숫자로 재는 센서 전부(온도·습도·전류·진동·가스 —
    센서마다 내일을 예측하려면 각자의 시간별 기록이 있어야 한다). 같은 시간은 다시 계산해 덮는다."""
    ensure(storage)
    now = time.time() if now is None else now
    end = (now // H) * H
    start = end - hours * H
    with storage._lock:
        rows = storage._conn.execute(
            "SELECT device_id, sensor_key, CAST(ts / 3600 AS INTEGER) * 3600 AS h, AVG(value) AS a, MIN(value) AS lo, "
            "MAX(value) AS hi, COUNT(*) AS n FROM readings WHERE ok=1 AND value IS NOT NULL AND ts >= ? AND ts < ? "
            "GROUP BY device_id, sensor_key, h", (start, end)).fetchall()
        storage._conn.executemany(
            "INSERT OR REPLACE INTO hourly (device_id, sensor_key, hour, avg, vmin, vmax, n) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(r["device_id"], r["sensor_key"], float(r["h"]), r["a"], r["lo"], r["hi"], r["n"]) for r in rows])
        storage._conn.commit()
    return len(rows)


def prune(storage, now: float | None = None, long: dict | None = None) -> int:
    """long = {기기 키: 보관 일수} — 장기 보관 고객사(retention.long_keep)의 시간별 값은 그 일수까지 남긴다."""
    ensure(storage)
    now = time.time() if now is None else now
    days = float(os.environ.get("JCC_KEEP_HOURLY_DAYS") or 1100)      # 기본 3년 — 작년 같은 날과 비교하려면 1년 넘게
    long = {k: d for k, d in (long or {}).items() if d > days}
    with storage._lock:
        ex = (" AND device_id NOT IN (" + ",".join("?" * len(long)) + ")") if long else ""
        n = storage._conn.execute("DELETE FROM hourly WHERE hour < ?" + ex, (now - days * D, *long)).rowcount
        groups: dict = {}
        for k, d in long.items():
            groups.setdefault(d, []).append(k)
        for d, keys in groups.items():
            n += storage._conn.execute("DELETE FROM hourly WHERE hour < ? AND device_id IN (" + ",".join("?" * len(keys)) + ")",
                                       (now - d * D, *keys)).rowcount
        storage._conn.commit()
    return n


def series(storage, device_id: str, sensor_key: str, since: float) -> dict:
    ensure(storage)
    with storage._lock:
        rows = storage._conn.execute("SELECT hour, avg FROM hourly WHERE device_id=? AND sensor_key=? AND hour >= ? "
                                     "ORDER BY hour", (device_id, sensor_key, since)).fetchall()
    return {float(r["hour"]): r["avg"] for r in rows if r["avg"] is not None}


# ── 예측(순수 계산 — 시험·시연판이 같은 규칙) ─────────────────────
def _profile(s: dict, t: float, upto: float):
    """t 시각의 '보통 값': 같은 요일·시각 지난 8주 중앙값(2개 이상), 모자라면 지난 14일 같은 시각 중앙값."""
    same = [s[t - k * 7 * D] for k in range(1, WEEKS + 1) if t - k * 7 * D < upto and (t - k * 7 * D) in s]
    if len(same) >= 2:
        return statistics.median(same)
    near = [s[t - k * D] for k in range(1, 15) if t - k * D < upto and (t - k * D) in s]
    return statistics.median(near) if near else None


def _flow(s: dict, upto: float) -> float:
    """최근 72시간이 보통 값보다 평균 얼마나 높았나(낮으면 음수)."""
    diffs = []
    for k in range(1, FLOW_H + 1):
        t = upto - k * H
        if t in s:
            p = _profile(s, t, t)
            if p is not None:
                diffs.append(s[t] - p)
    return statistics.mean(diffs) if diffs else 0.0


def predict(s: dict, upto: float, hours: list) -> dict:
    """upto 시각까지의 기록으로 hours(정시 타임스탬프들)를 예측. {시각: 값 또는 None}."""
    fl = _flow(s, upto) * FLOW_DAMP
    out = {}
    for t in hours:
        p = _profile(s, t, upto)
        out[t] = None if p is None else round(p + fl, 2)
    return out


def _day0(t: float) -> float:
    d = datetime.fromtimestamp(t, KST)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def backtest(s: dict, today0: float, days: int = BACKTEST_DAYS):
    """지난 며칠 각각을, 그 날 0시까지의 기록만으로 예측해 실제와의 차이(절댓값). (오차 목록, 잰 날 수)"""
    errs, n = [], 0
    for k in range(1, days + 1):
        d0 = today0 - k * D
        hrs = [d0 + i * H for i in range(24)]
        pr = predict(s, d0, hrs)
        e = [abs(pr[t] - s[t]) for t in hrs if pr[t] is not None and t in s]
        if e:
            errs += e
            n += 1
    return errs, n


def build(s: dict, now: float, warn=None) -> dict:
    """화면용: 어제·오늘 실제, 오늘 남은 시간·내일 예측(범위), 작년 같은 날, 지난 30일 평균 오차."""
    today0 = _day0(now)
    first = min(s) if s else None
    days = 0 if first is None else (now - first) / D
    out = {"days": round(days, 1), "building": days < MIN_DAYS, "warn": warn,
           "yesterday": [], "today": [], "forecast": [], "last_year": [], "mae": None, "band": None, "tested_days": 0}
    out["yesterday"] = [{"ts": t, "v": round(s[t], 2)} for t in (today0 - D + i * H for i in range(24)) if t in s]
    out["today"] = [{"ts": t, "v": round(s[t], 2)} for t in (today0 + i * H for i in range(24)) if t in s and t < now]
    if out["building"]:
        return out
    errs, tested = backtest(s, today0)
    band = None
    if tested >= 3:                         # 3일도 안 재 봤으면 오차·범위를 지어내지 않는다
        errs.sort()
        band = round(errs[min(len(errs) - 1, int(len(errs) * BAND_Q))], 2)
        out.update(mae=round(statistics.mean(errs), 2), band=band, tested_days=tested)
    start = max(now // H * H + H, today0)
    hrs = [t for t in (start + i * H for i in range(48)) if t < today0 + 2 * D]
    pr = predict(s, now, hrs)
    out["forecast"] = [{"ts": t, "v": pr[t], "lo": None if band is None else round(pr[t] - band, 2),
                        "hi": None if band is None else round(pr[t] + band, 2)} for t in hrs if pr[t] is not None]
    tm0 = today0 + D
    out["last_year"] = [{"ts": t, "v": round(s[t - 364 * D], 2)} for t in (tm0 + i * H for i in range(24)) if (t - 364 * D) in s]
    tomorrow = [f for f in out["forecast"] if f["ts"] >= tm0]
    if tomorrow:
        pk = max(tomorrow, key=lambda f: f["v"])
        out["peak"] = pk
        out["low"] = min(tomorrow, key=lambda f: f["v"])
        out["near_warn"] = bool(warn is not None and (pk["hi"] if pk["hi"] is not None else pk["v"]) >= warn)
    return out


# ── 판넬 하나 ─────────────────────────────────────────────
def panel_forecast_source(storage, panel: str):
    """판넬의 대표 온도 센서(함내 온도 우선)와 주의 기준 → (기기, 센서 키, 이름, 주의 기준). 없으면 None."""
    ccms = next((p["ccms"] for p in storage.list_panels() if p["panel"] == panel), None)
    if ccms is None:
        return None
    pick = None
    for c in ccms:
        for x in c.get("latest") or []:
            unit, key = (x.get("unit") or "").upper(), x["sensor_key"].lower()
            is_temp = ((x.get("kind") == "temp" or unit in ("C", "°C", "DEGC") or "temp" in key) and "ncontact" not in key
                       and not str(x.get("kind") or "").startswith("aircon_") and not re.match(r"^ac[0-9]+_", key))   # 에어컨이 잰 값은 판넬 온도가 아니다
            if is_temp and (pick is None or ("cabinet" in key and "cabinet" not in pick[1].lower())):
                pick = (c["device_id"], x["sensor_key"], x.get("name") or x["sensor_key"])
    if pick is None:
        return None
    dev, key, name = pick
    with storage._lock:
        thr = storage._conn.execute("SELECT alarm_warn, alarm_max FROM discovered WHERE device_id=? AND sensor_key=?", (dev, key)).fetchone()
        st = storage._conn.execute("SELECT alarm_warn, alarm_max FROM settings WHERE device_id=? AND sensor_key=?", (dev, key)).fetchone()
    # 주의 기준: 운영자 설정 → 제품 기본 순으로 '주의'를 찾고, 어디에도 없을 때만 '위험' 기준을 쓴다
    rows = [r for r in (st, thr) if r is not None]
    warn = next((r["alarm_warn"] for r in rows if r["alarm_warn"] is not None), None)
    if warn is None:
        warn = next((r["alarm_max"] for r in rows if r["alarm_max"] is not None), None)
    return dev, key, name, warn


def panel_forecast(storage, panel: str, now: float | None = None) -> dict | None:
    """판넬의 대표 온도 센서로 예측. 판넬·온도 센서가 없으면 None."""
    now = time.time() if now is None else now
    src = panel_forecast_source(storage, panel)
    if src is None:
        return None
    dev, key, name, warn = src
    s = series(storage, dev, key, now - 400 * D)
    out = build(s, now, warn)
    out.update(panel=panel, sensor_name=name, unit="℃", now=now)
    return out


# ── 판넬의 센서 전부 ──────────────────────────────────────
_SIGNAL_KINDS = {"door", "smoke"}          # 켜짐·꺼짐 신호 — 값 예측 대신 실시간 감시
_UNIT = {"C": "℃", "°C": "℃", "DEGC": "℃", "%RH": "%", "RH": "%"}
LIFE_SOON = 60                             # 경고선까지 이 날수 안이면 '보전 계획' 대상으로 표시
# 경고선에 다가가는 센서별 보전 할 일(가스는 rul.py 그대로 '센서 교정')
_LIFE_ADV = {"temp": "원인(냉각·환기·부하)을 점검하세요", "humidity": "히터·제습·밀폐 상태 점검을 계획하세요",
             "current": "부하와 단자 체결 점검을 계획하세요", "vibration": "팬·베어링·고정 상태 점검을 계획하세요"}


def _concern(f: dict) -> int:
    """2 = 내일 주의 기준에 닿을 수 있음 · 1 = 경고선까지 여유가 60일 안(또는 이미 닿음) · 0 = 평소 범위."""
    if f.get("near_warn"):
        return 2
    lf = f.get("life") or {}
    if lf.get("status") == "reached" or (lf.get("status") == "ok" and (lf.get("days") or 1e9) <= LIFE_SOON):
        return 1
    return 0


def panel_forecasts(storage, panel: str, now: float | None = None) -> dict | None:
    """판넬에 달린 센서 각각의 내일 예측 + 경고선까지 남은 여유. 위쪽 필드는 대표 온도(예전 모양 그대로),
    sensors = 센서 전부(걱정되는 것부터, 같으면 대표 온도 먼저), signals = 예측하지 않는 켜짐/꺼짐 신호."""
    from . import guard, rul
    now = time.time() if now is None else now
    ccms = next((p["ccms"] for p in storage.list_panels() if p["panel"] == panel), None)
    if ccms is None:
        return None
    src = panel_forecast_source(storage, panel)
    primary = (src[0], src[1]) if src else None
    sensors, signals, seen = [], [], {}
    for c in ccms:
        for x in c.get("latest") or []:
            kind = guard._kind(x) or guard._guess(x)
            key = x["sensor_key"]
            if (kind in guard._SKIP_KINDS or kind.startswith("aircon") or re.match(r"^ac[0-9]+_", key.lower())
                    or (x.get("unit") or "") == "?" or x.get("enabled") is False):
                continue
            name = x.get("name") or key
            seen[name] = seen.get(name, 0) + 1
            name = name + (f" {seen[name]}" if seen[name] > 1 else "")
            if kind in _SIGNAL_KINDS:
                signals.append({"name": name, "kind": kind})
                continue
            is_primary = primary == (c["device_id"], key)
            warn = src[3] if is_primary else (x.get("alarm_warn") if x.get("alarm_warn") is not None else x.get("alarm_max"))
            f = build(series(storage, c["device_id"], key, now - 400 * D), now, warn)
            unit = _UNIT.get((x.get("unit") or "").upper(), x.get("unit") or "")
            f.update(id=f"{c['device_id']}:{key}", name=name, kind=kind, unit=unit, primary=is_primary)
            rule = rul._sensor_rule(x)
            ser = rul.series(storage, f"sensor:{c['device_id']}:{key}") if rule else []
            if ser and not all(v in (0, 1) for _, v in ser):
                e = rul.estimate(ser, rule[0], rule[1])
                item = dict(e, kind="gas" if kind in rul._GAS else "sensor")
                f["life"] = {"status": e["status"], "days": e["days"], "days_lo": e["days_lo"], "days_hi": e["days_hi"],
                             "threshold": rule[0], "direction": rule[1], "say": rul._say(item),
                             "advice": rul._advice(item) and (_LIFE_ADV.get(kind) or rul._advice(item))}
            else:
                f["life"] = None
            f["concern"] = _concern(f)
            sensors.append(f)
    if not sensors:
        return None
    sensors.sort(key=lambda f: (-f["concern"], not f["primary"]))
    top = next((f for f in sensors if f["primary"]), None)
    out = dict(top) if top else {"panel": panel, "building": True, "days": 0, "forecast": []}
    out.update(panel=panel, now=now, sensors=sensors, signals=signals)
    if top:
        out.update(sensor_name=top["name"], unit="℃")
    return out
