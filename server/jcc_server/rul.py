"""남은 수명(여유) 예측 — "이대로 가면 언제 기준선에 닿나".

지금 예지보전은 '지금 위험한가'를 본다. 여기서는 하루 대표값(중앙값)을 쌓아
천천히 나빠지는 것(접점 잔차 상승·센서 기준선 드리프트)이 기준선에 닿을 날을 범위로 추정한다.

원칙
  - 하루 대표값이 MIN_DAYS일 미만이면 예측하지 않는다("데이터 N일").
  - 추세가 분명하지 않으면(기울기 25~75 백분위가 0을 사이에 둠) "뚜렷한 추세 없음".
  - 한 점이 아니라 범위로 말한다. 경보가 아니다 — 문자를 보내지 않고 출력 결정에도 쓰지 않는다.
"""
from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta, timezone

MIN_DAYS = 7
WINDOW_DAYS = 30
HORIZON_DAYS = 365
NOTE_WITHIN = 14            # 이 안으로 들어오면 활동 기록에 남긴다(같은 항목 7일에 한 번)
KEEP_DAYS = 400
_KST = timezone(timedelta(hours=9))
_GAS = ("h2", "voc", "co")

SCHEMA = """
CREATE TABLE IF NOT EXISTS rul_daily (
    metric TEXT, day TEXT, value REAL, n INTEGER, PRIMARY KEY (metric, day));
CREATE TABLE IF NOT EXISTS rul_note (metric TEXT PRIMARY KEY, noted_at REAL);
"""


def day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, _KST).strftime("%Y-%m-%d")


def day_bounds(day: str) -> tuple:
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_KST)
    return d.timestamp(), (d + timedelta(days=1)).timestamp()


def _day_index(day: str) -> int:
    return datetime.strptime(day, "%Y-%m-%d").toordinal()


def _pct(sorted_vals, q):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def theil_sen(points: list) -> tuple:
    """[(x, y)] → (기울기, 절편, 기울기 25백분위, 75백분위). 이상치 몇 개에 흔들리지 않는 직선."""
    slopes = sorted((y2 - y1) / (x2 - x1) for i, (x1, y1) in enumerate(points)
                    for x2, y2 in points[i + 1:] if x2 != x1)
    if not slopes:
        return 0.0, points[0][1] if points else 0.0, 0.0, 0.0
    m = statistics.median(slopes)
    b = statistics.median(y - m * x for x, y in points)
    return m, b, _pct(slopes, 0.25), _pct(slopes, 0.75)


def estimate(series: list, threshold: float, direction: str = "up") -> dict:
    """series = [(day 'YYYY-MM-DD', value)] (오래된→최신). 기준선까지 남은 날을 범위로.

    status: insufficient | flat | away | reached | far | ok
    """
    series = series[-WINDOW_DAYS:]
    n = len(series)
    out = {"status": "insufficient", "n_days": n, "threshold": threshold, "direction": direction,
           "current": round(series[-1][1], 3) if series else None,
           "days": None, "days_lo": None, "days_hi": None, "slope_per_day": None}
    if n < MIN_DAYS or threshold is None:
        return out
    # 아래 방향(하한)이면 부호를 뒤집어 '나빠짐 = 오름'으로 같은 계산을 한다
    sgn = 1.0 if direction == "up" else -1.0
    x0 = _day_index(series[0][0])
    pts = [(_day_index(d) - x0, v * sgn) for d, v in series]
    m, b, lo, hi = theil_sen(pts)
    cur = b + m * pts[-1][0]                             # 오늘 추세값
    gap = threshold * sgn - cur                          # 기준까지 남은 거리(+면 아직)
    out.update(current=round(cur * sgn, 3), slope_per_day=round(m * sgn, 5))
    if gap <= 0:
        out["status"] = "reached"
        return out
    if lo <= 0:                                          # 나빠지는 쪽이 분명하지 않음
        out["status"] = "away" if hi < 0 else "flat"
        return out
    days = gap / m
    if days > HORIZON_DAYS:
        out["status"] = "far"
        return out
    out.update(status="ok", days=round(days, 1), days_lo=round(gap / hi, 1),
               days_hi=round(min(gap / lo, HORIZON_DAYS * 2), 1))
    return out


# ── 저장·집계 ─────────────────────────────────────────────
def ensure(storage) -> None:
    if getattr(storage, "_rul_ready", False):
        return
    with storage._lock:
        storage._conn.executescript(SCHEMA)
        storage._conn.commit()
    storage._rul_ready = True


def put_day(storage, metric: str, day: str, value: float, n: int, keep_larger: bool = False) -> None:
    ensure(storage)
    with storage._lock:
        if keep_larger:     # 재시작으로 표본이 줄었으면 앞서 저장한(표본 많은) 값을 지킨다
            r = storage._conn.execute("SELECT n FROM rul_daily WHERE metric=? AND day=?", (metric, day)).fetchone()
            if r and (r["n"] or 0) > n:
                return
        storage._conn.execute("INSERT INTO rul_daily (metric, day, value, n) VALUES (?, ?, ?, ?) "
                              "ON CONFLICT(metric, day) DO UPDATE SET value=excluded.value, n=excluded.n",
                              (metric, day, float(value), int(n)))
        storage._conn.commit()


def series(storage, metric: str) -> list:
    ensure(storage)
    with storage._lock:
        rows = storage._conn.execute("SELECT day, value FROM rul_daily WHERE metric=? ORDER BY day DESC LIMIT ?",
                                     (metric, WINDOW_DAYS)).fetchall()
    return [(r["day"], r["value"]) for r in reversed(rows)]


def note_contact(storage, panel: str, residual, now: float) -> None:
    """예지 판정 주기마다 접점 잔차 표본을 모은다(시간마다 rollup이 그날 중앙값으로 저장)."""
    if residual is None:
        return
    buf = storage.__dict__.setdefault("_rul_buf", {})
    lst = buf.setdefault((panel, day_of(now)), [])
    if len(lst) < 20000:
        lst.append(float(residual))


def rollup(storage, now: float | None = None) -> int:
    """오늘·어제의 하루 중앙값을 저장(센서는 readings에서, 접점은 모은 표본에서). 저장한 항목 수."""
    ensure(storage)
    now = time.time() if now is None else now
    n = 0
    days = [day_of(now - 86400), day_of(now)]
    final = storage.__dict__.setdefault("_rul_final", set())   # 다 지난 날은 한 번 굳히면 다시 안 센다
    for day in days:
        if day in final:
            continue
        t0, t1 = day_bounds(day)
        with storage._lock:
            rows = storage._conn.execute(
                "SELECT device_id, sensor_key, value FROM readings WHERE ts >= ? AND ts < ? AND ok=1 "
                "AND value IS NOT NULL", (t0, t1)).fetchall()
        vals: dict = {}
        for r in rows:
            vals.setdefault((r["device_id"], r["sensor_key"]), []).append(r["value"])
        for (dev, key), vs in vals.items():
            if len(vs) >= 3:
                put_day(storage, f"sensor:{dev}:{key}", day, statistics.median(vs), len(vs))
                n += 1
        if t1 + 3600 < now:
            final.add(day)
    buf = storage.__dict__.get("_rul_buf", {})
    for (panel, day), vs in list(buf.items()):
        if vs:
            put_day(storage, f"contact:{panel}", day, statistics.median(vs), len(vs), keep_larger=True)
            n += 1
        if day < days[0]:
            buf.pop((panel, day), None)
    with storage._lock:
        storage._conn.execute("DELETE FROM rul_daily WHERE day < ?", (day_of(now - KEEP_DAYS * 86400),))
        storage._conn.commit()
    note_due(storage, now)
    return n


# ── 판넬별 보기 ───────────────────────────────────────────
def _sensor_rule(s: dict):
    """(기준값, 방향) 또는 None(예측 안 함)."""
    if s.get("enabled") is False or s.get("kind") in ("smoke", "door"):
        return None
    if s.get("alarm_warn") is not None:
        return float(s["alarm_warn"]), "up"
    if s.get("alarm_max") is not None:
        return float(s["alarm_max"]), "up"
    if s.get("alarm_min") is not None:
        return float(s["alarm_min"]), "down"
    return None


def _say(item: dict) -> str:
    st, kind = item["status"], item["kind"]
    if st == "insufficient":
        return f"데이터 {item['n_days']}일 — {MIN_DAYS}일 쌓이면 예측"
    if st == "reached":
        return "이미 기준선 이상"
    if st in ("flat", "away"):
        return "뚜렷하게 나빠지는 추세 없음"
    if st == "far":
        return "1년 넘게 여유"
    d, lo, hi = item["days"], item["days_lo"], item["days_hi"]
    unit, k = ("주", 7) if d >= 21 else ("일", 1)
    a, z = max(1, round(lo / k)), (max(1, round(hi / k)) if hi else None)
    span = "하루 안" if d < 1 else f"약 {max(1, round(d / k))}{unit}"
    rng = "" if d < 1 or a == z else (f"{a}~{z}{unit}" if z else f"{a}{unit} 이상")
    what = {"contact": "위험 기준까지", "gas": "경고선까지(기준선 드리프트)"}.get(kind, "경고선까지")
    return f"{what} {span}" + (f" ({rng})" if rng else "")


def _advice(item: dict) -> str:
    if item["status"] not in ("ok", "reached"):
        return ""
    return {"contact": "접점 조임·청소 점검을 계획하세요", "gas": "센서 교정이 필요합니다"}.get(
        item["kind"], "원인(냉각·환기·부하)을 점검하세요")


def panel_view(storage, panels: set | None = None) -> list:
    """판넬별 남은 여유 목록. panels=None이면 전부. 항목은 가까운 것부터."""
    ensure(storage)
    pred = storage.predictor
    res_alarm = getattr(getattr(pred, "contact_cfg", None), "res_alarm", 11.0) if pred else 11.0
    out = []
    for p in storage.list_panels():
        if panels is not None and p["panel"] not in panels:
            continue
        items = []
        cser = series(storage, f"contact:{p['panel']}")
        if cser:
            e = estimate(cser, res_alarm, "up")
            items.append(dict(e, kind="contact", metric=f"contact:{p['panel']}", unit="°C",
                              label=f"접점 발열 잔차 → 위험 기준 {res_alarm:g}°C"))
        for c in p["ccms"]:
            for s in c.get("latest") or []:
                rule = _sensor_rule(s)
                if rule is None:
                    continue
                ser = series(storage, f"sensor:{c['device_id']}:{s['sensor_key']}")
                if not ser or all(v in (0, 1) for _, v in ser):
                    continue
                e = estimate(ser, rule[0], rule[1])
                kind = "gas" if s.get("kind") in _GAS else "sensor"
                items.append(dict(e, kind=kind, metric=f"sensor:{c['device_id']}:{s['sensor_key']}",
                                  unit=s.get("unit") or "", device=c["device_id"], sensor_key=s["sensor_key"],
                                  label=f"{s.get('name') or s['sensor_key']} → {'경고선' if s.get('alarm_warn') is not None else '경보선'} "
                                        f"{rule[0]:g}{s.get('unit') or ''}"))
        order = {"reached": 0, "ok": 1, "insufficient": 3, "flat": 4, "far": 5, "away": 6}
        items.sort(key=lambda i: (order.get(i["status"], 9), i["days"] if i["days"] is not None else 1e9))
        for i in items:
            i["say"], i["advice"] = _say(i), _advice(i)
        out.append({"panel": p["panel"], "panel_name": p["panel_name"], "items": items})
    return out


def note_due(storage, now: float) -> int:
    """NOTE_WITHIN일 안으로 들어온 항목을 활동 기록에 남긴다(같은 항목 7일에 한 번). 문자는 보내지 않는다."""
    n = 0
    for p in panel_view(storage):
        for i in p["items"]:
            if i["status"] != "ok" or i["days"] > NOTE_WITHIN:
                continue
            with storage._lock:
                r = storage._conn.execute("SELECT noted_at FROM rul_note WHERE metric=?", (i["metric"],)).fetchone()
            if r and now - (r["noted_at"] or 0) < 7 * 86400:
                continue
            with storage._lock:
                storage._conn.execute("INSERT INTO rul_note (metric, noted_at) VALUES (?, ?) "
                                      "ON CONFLICT(metric) DO UPDATE SET noted_at=excluded.noted_at", (i["metric"], now))
                storage._conn.commit()
            dev = i.get("device") or p["panel"]
            storage.log_event(dev, i.get("sensor_key") or "", "rul_warn",
                              f"{p['panel_name']} {i['label']}: {i['say']} — {i['advice']}", source="system")
            n += 1
    return n
