"""남은 여유 예측 — Theil–Sen·추정 상태·하루 대표값 집계·판넬 보기·기록 한 번만·API 범위.

python -m tests.test_rul   (server/ 에서)
"""
from __future__ import annotations

import os
import random
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from jcc_server import rul
from jcc_server.predict import Predictor
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def days(n, f, end=None):
    """최근 n일 [(day, f(i))] — 마지막이 오늘."""
    end = time.time() if end is None else end
    return [(rul.day_of(end - (n - 1 - i) * 86400), f(i)) for i in range(n)]


def run():
    print("=== Theil–Sen ===")
    pts = [(x, 2 * x + 1) for x in range(10)]
    m, b, lo, hi = rul.theil_sen(pts)
    check("직선: 기울기 2·절편 1", abs(m - 2) < 1e-9 and abs(b - 1) < 1e-9 and lo == hi == 2)
    pts[3] = (3, 80.0)
    pts[7] = (7, -50.0)
    m, *_ = rul.theil_sen(pts)
    check("이상치 2개에도 기울기 유지", abs(m - 2) < 0.3, f"{m:.3f}")

    print("\n=== 추정 상태 ===")
    random.seed(7)
    e = rul.estimate(days(5, lambda i: 4 + 0.1 * i), 11)
    check("5일 → 데이터 부족", e["status"] == "insufficient" and e["n_days"] == 5)
    e = rul.estimate(days(20, lambda i: 4 + 0.1 * i + random.uniform(-0.15, 0.15)), 11)
    check("오르는 잔차 → 남은 날·범위", e["status"] == "ok" and 45 < e["days"] < 70
          and e["days_lo"] <= e["days"] <= e["days_hi"], str({k: e[k] for k in ("days", "days_lo", "days_hi", "current")}))
    e = rul.estimate(days(20, lambda i: 5 + random.uniform(-0.3, 0.3)), 11)
    check("흔들리기만 함 → 추세 없음", e["status"] == "flat", e["status"])
    e = rul.estimate(days(20, lambda i: 8 - 0.2 * i), 11)
    check("좋아지는 중 → away", e["status"] == "away")
    e = rul.estimate(days(20, lambda i: 9 + 0.2 * i), 11)
    check("이미 넘음 → reached", e["status"] == "reached")
    e = rul.estimate(days(20, lambda i: 1 + 0.001 * i), 11)
    check("아주 느림 → 1년 넘게", e["status"] == "far")
    e = rul.estimate(days(15, lambda i: 30 - 0.5 * i), 15, "down")
    check("하한 방향(아래로) 추정", e["status"] == "ok" and abs(e["days"] - 16) < 1.5 and e["slope_per_day"] < 0,
          str(e["days"]))
    e = rul.estimate(days(60, lambda i: 1 + 0.1 * i), 100)
    check("최근 30일만 사용", e["n_days"] == 30)

    print("\n=== 문장 ===")
    check("3주 넘으면 주 단위", rul._say({"status": "ok", "kind": "contact", "days": 50, "days_lo": 40, "days_hi": 70,
                                        "n_days": 20}) == "위험 기준까지 약 7주 (6~10주)")
    check("가스는 드리프트·교정", "드리프트" in rul._say({"status": "ok", "kind": "gas", "days": 10, "days_lo": 8,
                                                     "days_hi": 14, "n_days": 9})
          and "교정" in rul._advice({"status": "ok", "kind": "gas"}))
    check("부족하면 쌓일 날 안내", rul._say({"status": "insufficient", "kind": "sensor", "n_days": 3}).startswith("데이터 3일"))

    print("\n=== 하루 대표값 집계·판넬 보기 ===")
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    st.predictor = Predictor()
    try:
        st.set_discovery({"panel": "p1", "panel_name": "1번 판넬", "ccms": [{"device_id": "ccm-1", "sensors": [
            {"key": "h2_lel", "name": "수소", "unit": "%LEL", "kind": "h2", "alarm_warn": 10, "alarm_max": 25},
            {"key": "door", "name": "도어", "unit": "", "kind": "door"},
            {"key": "temp", "name": "함내 온도", "unit": "°C", "kind": "temperature"}]}]})
        now = time.time()
        rows = []
        for back in range(2):                       # 어제·오늘: 하루 중앙값이 튀는 값에 흔들리지 않나
            base = now - back * 86400
            for k, v in enumerate([1.0, 1.1, 1.2, 50.0, 1.05]):
                rows.append({"key": "h2_lel", "name": "수소", "unit": "%LEL", "value": v + back, "ok": True,
                             "ts": base - k * 60})
        st.ingest({"device_id": "ccm-1", "panel": "p1", "readings": rows})
        n = rul.rollup(st, now)
        ser = rul.series(st, "sensor:ccm-1:h2_lel")
        check("오늘·어제 중앙값(튀는 50 무시)", n >= 2 and len(ser) == 2 and ser[-1][1] == 1.1 and ser[0][1] == 2.1, str(ser))
        rul.put_day(st, "contact:p1", rul.day_of(now), 3.0, 500)
        rul.note_contact(st, "p1", 9.0, now)
        rul.rollup(st, now)
        check("재시작으로 표본 줄면 앞 값 유지", rul.series(st, "contact:p1")[-1][1] == 3.0)

        # 지난 20일: 수소 기준선이 오르고(드리프트), 접점 잔차도 오른다
        for d, v in days(20, lambda i: 2 + 0.4 * i, now):
            rul.put_day(st, "sensor:ccm-1:h2_lel", d, v, 100)
        for d, v in days(20, lambda i: 4 + 0.12 * i, now):
            rul.put_day(st, "contact:p1", d, v, 1000)
        for d, v in days(20, lambda i: i % 2, now):
            rul.put_day(st, "sensor:ccm-1:door", d, v, 100)
        pv = rul.panel_view(st)
        items = pv[0]["items"]
        kinds = [i["kind"] for i in items]
        check("판넬 보기: 가스·접점 항목, 도어는 제외", "gas" in kinds and "contact" in kinds
              and not any(i.get("sensor_key") == "door" for i in items), str(kinds))
        check("가까운 것부터", items[0]["kind"] == "gas" and items[0]["status"] == "ok" and items[0]["days"] < 5,
              items[0]["say"])
        check("접점 기준 = 활성 res_alarm", next(i for i in items if i["kind"] == "contact")["threshold"]
              == st.predictor.contact_cfg.res_alarm)
        before = len([e for e in st.list_events(limit=200) if e["etype"] == "rul_warn"])
        rul.note_due(st, now)
        rul.note_due(st, now + 3600)
        after = [e for e in st.list_events(limit=200) if e["etype"] == "rul_warn"]
        check("14일 이내 → 활동 기록, 같은 항목 7일에 한 번", len(after) - before == 1 and "교정" in after[0]["detail"],
              after[0]["detail"] if after else "")
        rul.note_due(st, now + 8 * 86400)
        check("7일 지나면 다시 한 번", len([e for e in st.list_events(limit=200) if e["etype"] == "rul_warn"]) - before == 2)
        check("범위 필터", rul.panel_view(st, {"other"}) == [])
    finally:
        st.close()
        os.unlink(db)

    print()
    if _fails:
        print(f"❌ {len(_fails)} FAIL: {_fails}")
        return 1
    print("✅ 전체 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
