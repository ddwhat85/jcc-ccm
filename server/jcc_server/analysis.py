"""기록 분석(직원 화면) — 센서 몇 개의 기간 그래프·통계·피크, 그리고 바로 앞 같은 길이 기간과의 비교.

숫자는 모두 저장된 실제 기록에서:
  - 기간이 2일 이하면 원본 값(readings, 14일 보관)을 5분 칸으로, 그보다 길면 시간별 평균(hourly, 3년 보관).
  - 칸마다 평균·최저·최고. 통계는 칸 평균으로(평균·표준편차), 최저·최고는 칸의 최저·최고로(언제였는지 포함).
  - 피크 = 칸 최고값의 봉우리 중 높은 순(서로 6칸 이상 떨어진 것만) — 같은 봉우리를 여러 번 세지 않게.
  - 변화율 = 첫 칸 평균 → 마지막 칸 평균(%). 지난 기간 = 바로 앞 같은 길이(평균·최고·경보 수).
"""
from __future__ import annotations

import math
import statistics

RAW_MAX_SPAN = 2 * 86400     # 이보다 짧으면 원본 값으로
RAW_BUCKET = 300             # 원본을 5분 칸으로
MAX_SENSORS = 4
MAX_SPAN = 5 * 366 * 86400
PEAKS = 5
PEAK_GAP = 6


def _buckets(storage, dev: str, key: str, t0: float, t1: float) -> tuple[list, int]:
    """[(칸 시작, 평균, 최저, 최고)] 와 칸 길이(초)."""
    if t1 - t0 <= RAW_MAX_SPAN:
        with storage._lock:
            rows = storage._conn.execute(
                f"SELECT CAST(ts / {RAW_BUCKET} AS INTEGER) * {RAW_BUCKET} AS b, AVG(value) AS a, MIN(value) AS lo, MAX(value) AS hi "
                "FROM readings WHERE device_id=? AND sensor_key=? AND ok=1 AND value IS NOT NULL AND ts >= ? AND ts < ? "
                "GROUP BY b ORDER BY b", (dev, key, t0, t1)).fetchall()
        return [(float(r["b"]), r["a"], r["lo"], r["hi"]) for r in rows], RAW_BUCKET
    from .forecast import ensure
    ensure(storage)
    with storage._lock:
        rows = storage._conn.execute(
            "SELECT hour, avg, vmin, vmax FROM hourly WHERE device_id=? AND sensor_key=? AND hour >= ? AND hour < ? AND avg IS NOT NULL "
            "ORDER BY hour", (dev, key, t0, t1)).fetchall()
    return [(float(r["hour"]), r["avg"], r["vmin"] if r["vmin"] is not None else r["avg"],
             r["vmax"] if r["vmax"] is not None else r["avg"]) for r in rows], 3600


def peaks(pts: list, n: int = PEAKS, gap: int = PEAK_GAP) -> list:
    """칸 최고값의 봉우리(양옆보다 높거나 같은 칸) 중 높은 순 n개, 서로 gap칸 이상 떨어진 것만. [(시각, 값)]"""
    cand = [i for i in range(len(pts)) if (i == 0 or pts[i][3] >= pts[i - 1][3]) and (i == len(pts) - 1 or pts[i][3] >= pts[i + 1][3])]
    cand.sort(key=lambda i: -pts[i][3])
    out = []
    for i in cand:
        if all(abs(i - j) >= gap for j in out):
            out.append(i)
        if len(out) >= n:
            break
    return [(pts[i][0], round(pts[i][3], 3)) for i in sorted(out)]


def stats(pts: list) -> dict | None:
    if not pts:
        return None
    avgs = [p[1] for p in pts]
    lo = min(pts, key=lambda p: p[2])
    hi = max(pts, key=lambda p: p[3])
    first, last = avgs[0], avgs[-1]
    return {"n": len(pts), "mean": round(statistics.fmean(avgs), 3), "std": round(statistics.pstdev(avgs), 3) if len(avgs) > 1 else 0.0,
            "min": round(lo[2], 3), "min_ts": lo[0], "max": round(hi[3], 3), "max_ts": hi[0],
            "change_pct": round((last - first) / abs(first) * 100, 1) if first not in (0, None) and len(avgs) > 1 else None}


def _alarm_count(storage, dev: str, key: str, t0: float, t1: float) -> int:
    with storage._lock:
        return storage._conn.execute("SELECT COUNT(*) AS n FROM alarms WHERE device_id=? AND sensor_key=? AND kind != 'silent' "
                                     "AND raised_at >= ? AND raised_at < ?", (dev, key, t0, t1)).fetchone()["n"]


def analyze(storage, ids: list, t0: float, t1: float, compare: bool = True) -> dict:
    """ids = ['기기:센서키', …](최대 4). 센서마다 칸·통계·피크·기준선, compare면 바로 앞 같은 길이 기간도."""
    meta = {(c["device_id"], x["sensor_key"]): (p["panel_name"], x) for p in storage.list_panels() for c in p["ccms"] for x in c.get("latest") or []}
    span = t1 - t0
    out = []
    for sid in ids[:MAX_SENSORS]:
        dev, _, key = sid.partition(":")
        pn, x = meta.get((dev, key), ("", {}))
        pts, step = _buckets(storage, dev, key, t0, t1)
        item = {"id": sid, "panel_name": pn, "name": x.get("name") or key, "unit": x.get("unit") or "", "kind": x.get("kind") or "",
                "warn": x.get("alarm_warn"), "max_limit": x.get("alarm_max"), "step": step,
                "points": [[p[0], round(p[1], 3), round(p[2], 3), round(p[3], 3)] for p in pts],
                "stats": stats(pts), "peaks": peaks(pts), "alarms": _alarm_count(storage, dev, key, t0, t1)}
        if compare:
            pp, _ = _buckets(storage, dev, key, t0 - span, t0)
            item["prev"] = {"points": [[p[0] + span, round(p[1], 3)] for p in pp],      # 지금 기간 위에 겹쳐 그리게 시각을 민다
                            "stats": stats(pp), "alarms": _alarm_count(storage, dev, key, t0 - span, t0)}
        out.append(item)
    return {"from": t0, "to": t1, "sensors": out}


def parse_range(q: dict, now: float):
    """쿼리 → (from, to). days(기본 7) 또는 from/to(초). 잘못되면 ValueError."""
    def num(k):
        v = (q.get(k) or [""])[0]
        return float(v) if v not in ("", None) else None
    t1 = num("to") or now
    t0 = num("from")
    if t0 is None:
        days = num("days") or 7.0
        t0 = t1 - days * 86400
    if not (math.isfinite(t0) and math.isfinite(t1)) or t1 <= t0 or t1 - t0 > MAX_SPAN:
        raise ValueError("기간을 확인하세요")
    return t0, t1
