"""판넬 온도 예측 — 내일 시간별 온도를, 그 판넬이 남긴 기록의 패턴만으로.

바깥 날씨(기상청)는 아직 쓰지 않는다(나중에 붙인다). 예측 = 같은 요일·같은 시각의 지난 8주 값(중앙값)
+ 최근 3일이 그 패턴보다 얼마나 높았나(흐름, 0.6배로 눌러 반영). 계절은 이 '흐름'이 천천히 따라간다.

고객 화면 원칙(실제 기록 숫자만, 가정은 밝힌다)에 맞게:
  - 예측은 언제나 '예측'·'범위'로 보이고, 지난 30일 동안 실제로 얼마나 맞았는지(평균 오차)를 같이 낸다.
  - 기록이 MIN_DAYS보다 적으면 예측하지 않는다(building).

원본 값(readings)은 14일만 남기므로, 시간별 평균을 hourly 표에 따로 오래 남긴다(rollup, 기본 3년).
"""
from __future__ import annotations

import os
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
    """지난 hours 시간의 원본 값을 시간별 평균으로(다 지난 시간만). 온도·습도만. 같은 시간은 다시 계산해 덮는다."""
    ensure(storage)
    now = time.time() if now is None else now
    end = (now // H) * H
    start = end - hours * H
    with storage._lock:
        rows = storage._conn.execute(
            "SELECT device_id, sensor_key, CAST(ts / 3600 AS INTEGER) * 3600 AS h, AVG(value) AS a, MIN(value) AS lo, "
            "MAX(value) AS hi, COUNT(*) AS n FROM readings WHERE ok=1 AND value IS NOT NULL AND ts >= ? AND ts < ? "
            "AND (UPPER(unit) IN ('C', '°C', 'DEGC', '%RH', 'RH') OR sensor_key LIKE '%temp%' OR sensor_key LIKE '%humid%') "
            "GROUP BY device_id, sensor_key, h", (start, end)).fetchall()
        storage._conn.executemany(
            "INSERT OR REPLACE INTO hourly (device_id, sensor_key, hour, avg, vmin, vmax, n) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(r["device_id"], r["sensor_key"], float(r["h"]), r["a"], r["lo"], r["hi"], r["n"]) for r in rows])
        storage._conn.commit()
    return len(rows)


def prune(storage, now: float | None = None) -> int:
    ensure(storage)
    now = time.time() if now is None else now
    days = float(os.environ.get("JCC_KEEP_HOURLY_DAYS") or 1100)      # 기본 3년 — 작년 같은 날과 비교하려면 1년 넘게
    with storage._lock:
        n = storage._conn.execute("DELETE FROM hourly WHERE hour < ?", (now - days * D,)).rowcount
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
            is_temp = (x.get("kind") == "temp" or unit in ("C", "°C", "DEGC") or "temp" in key) and "ncontact" not in key
            if is_temp and (pick is None or ("cabinet" in key and "cabinet" not in pick[1].lower())):
                pick = (c["device_id"], x["sensor_key"], x.get("name") or x["sensor_key"])
    if pick is None:
        return None
    dev, key, name = pick
    with storage._lock:
        thr = storage._conn.execute("SELECT alarm_warn, alarm_max FROM discovered WHERE device_id=? AND sensor_key=?", (dev, key)).fetchone()
        st = storage._conn.execute("SELECT alarm_warn, alarm_max FROM settings WHERE device_id=? AND sensor_key=?", (dev, key)).fetchone()
    warn = None
    for row in (st, thr):
        if row is not None and warn is None:
            warn = row["alarm_warn"] if row["alarm_warn"] is not None else row["alarm_max"]
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
