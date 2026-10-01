"""현장 목록 — 상태 판정(위험·끊김·주의·정상)·정렬·이유·고객 범위.

python -m tests.test_fleet   (server/ 에서)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from jcc_server import rul
from jcc_server.fleet import fleet
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run():
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    try:
        now = time.time()
        for dev, panel, name in (("c-ok", "p-ok", "가 정상"), ("c-crit", "p-crit", "나 위험"), ("c-warn", "p-warn", "다 주의"),
                                 ("c-off", "p-off", "라 끊김"), ("c-life", "p-life", "마 여유")):
            st.ingest({"device_id": dev, "panel": panel, "panel_name": name, "readings": [
                {"key": "t", "name": "온도", "unit": "C", "value": 30, "ok": True, "ts": now}]})
        st.ingest({"device_id": "c-warn2", "panel": "p-warn", "panel_name": "다 주의", "readings": [
            {"key": "t", "name": "온도", "unit": "C", "value": 30, "ok": True, "ts": now}]})
        with st._lock:                                     # 끊김: 마지막 수신을 옛날로
            st._conn.execute("UPDATE devices SET last_seen=? WHERE device_id IN ('c-off','c-warn2')", (now - 3600,))
            st._conn.commit()
        st.raise_alarm("c-crit", "t", "alarm", "온도 위험")
        st.raise_alarm("c-warn", "t", "alarm_warn", "온도 주의")
        for d, v in [(rul.day_of(now - (19 - i) * 86400), 4 + 0.3 * i) for i in range(20)]:
            rul.put_day(st, "contact:p-life", d, v, 1000)          # 잔차가 빠르게 올라 2주 안에 위험 기준
        cid = st.accounts.create_customer("A 제조")
        st.accounts.assign_panel("p-crit", cid)
        st.add_commission_report("p-ok", "기사", "pass", {"overall": "pass"})

        rows = fleet(st)
        order = [r["panel"] for r in rows]
        st_of = {r["panel"]: r for r in rows}
        check("위험 → 끊김 → 주의 → 정상 순", order[:2] == ["p-crit", "p-off"] and order[-1] == "p-ok", str(order))
        check("위험: 이유·미확인 수·고객사", st_of["p-crit"]["status"] == "crit" and "위험 경보 1" in st_of["p-crit"]["why"]
              and st_of["p-crit"]["alarms"]["unacked"] == 1 and st_of["p-crit"]["customer"] == "A 제조")
        check("끊김", st_of["p-off"]["status"] == "offline" and st_of["p-off"]["ccm_online"] == 0)
        w = st_of["p-warn"]
        check("주의: 주의 경보 + CCM 일부 끊김", w["status"] == "warn" and "주의 경보 1" in w["why"] and "CCM 1대 끊김" in w["why"]
              and (w["ccm_online"], w["ccm_total"]) == (1, 2), str(w["why"]))
        lf = st_of["p-life"]
        check("남은 여유 2주 이내 → 주의", lf["status"] == "warn" and "남은 여유 2주 이내" in lf["why"] and lf["life"]["days"] <= 14,
              str(lf["life"]))
        ok = st_of["p-ok"]
        check("정상 + 마지막 설치 점검", ok["status"] == "ok" and ok["why"] == [] and ok["commission"]["overall"] == "pass")
        check("고객 범위", [r["panel"] for r in fleet(st, {"p-crit"})] == ["p-crit"] and fleet(st, set()) == [])
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
