"""에어컨 원격 조작(틀만) — 범위 검사·대기·되돌리기·통신 끊김 잠금·적용은 '보류'로 기록(장비로 보내지 않음).

python -m tests.test_aircon_ctl   (server/ 에서)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server import aircon as AC
from jcc_server import aircon_ctl as C
from jcc_server import equipment as E
from jcc_server import manual_sensors as MS
from jcc_server.storage import Storage
from tests.test_aircon import ITEMS

_fails = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def bad(fn, *a):
    try:
        fn(*a)
        return ""
    except ValueError as e:
        return str(e)


def run() -> int:
    print("=== 범위 ===")
    check("설정 온도 20~50", "20~50" in bad(C.check_value, "setpoint", 55) and C.check_value("setpoint", 35.5) == 35.5)
    check("0.5℃ 단위", "0.5" in bad(C.check_value, "setpoint", 35.3))
    check("운전 켜기/끄기", C.check_value("run", "off") == 0.0 and C.check_value("run", 1) == 1.0 and bad(C.check_value, "run", 5))
    check("없는 항목 거절", bad(C.check_value, "fan_speed", 1))

    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    now = time.time()
    try:
        st.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "P1", "readings": [
            {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 30, "ok": True, "ts": now}]})
        AC.save_profile(st, "예시 기종", ITEMS, "t")
        E.save(st, "p1", {"width": 800, "height": 2000, "depth": 600, "target_c": 35, "ambient_c": 35, "heat": [{"loss_w": 900, "qty": 1}],
                          "equipment": [{"kind": "aircon", "name": "측면 에어컨", "capacity_w": 1000, "qty": 1}]}, "t")
        AC.link(st, "p1", 0, "ccm-1", "192.168.1.60", 502, 1, "예시 기종", "t")

        print("\n=== 통신 끊김 잠금 ===")
        check("연결 전(반영 대기)이면 잠금", "조작 잠금" in bad(C.stage, st, "p1", 0, "setpoint", 30, "jcc"))
        MS.note_report(st, "ccm-1", {"version": MS.version_of(st, "ccm-1"), "status": "ok",
                                     "active": [s["key"] for s in MS.specs_of(st, "ccm-1")], "errors": {}})
        st.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "P1", "readings": [
            {"key": "ac1_temp", "name": "t", "unit": "C", "value": 33, "ok": True, "ts": time.time()},
            {"key": "ac1_run", "name": "r", "unit": "", "value": 1, "ok": True, "ts": time.time()}]})
        check("연결됨이면 풀림", AC.panel_status(st, "p1")[0]["state"] == "live")

        print("\n=== 대기·되돌리기·적용 ===")
        v = C.stage(st, "p1", 0, "setpoint", 30, "jcc")
        v = C.stage(st, "p1", 0, "setpoint", 29.5, "jcc")
        check("같은 항목은 새 값으로 바꿈(대기 1건)", [p["value"] for p in v["units"]["0"]["pending"]] == [29.5])
        C.stage(st, "p1", 0, "run", "off", "jcc")
        check("되돌리기: 대기 모두 취소", C.revert(st, "p1", 0, "jcc") == 2 and not C.pending_view(st, "p1")["units"]["0"]["pending"])
        check("대기 없으면 적용 거절", "없습니다" in bad(C.apply, st, "p1", 0, "jcc"))
        C.stage(st, "p1", 0, "setpoint", 28, "jcc")
        r = C.apply(st, "p1", 0, "jcc")
        check("적용 = 보류(장비로 보내지 않음) + 이유", r["results"][0]["status"] == "held" and "보내지 않았습니다" in r["results"][0]["note"], str(r["results"]))
        check("기록이 남음", any(e["etype"] == "aircon_cmd" and "보류" in e["detail"] for e in st.events_since(0, None, 50)))
        check("최근 처리에 보임", C.pending_view(st, "p1")["units"]["0"]["recent"][0]["status"] == "held")
        check("화면에 '보낼 수 없음' 알림", C.pending_view(st, "p1")["can_send"] is False)
        check("연결 안 된 장치 거절", "연결된 에어컨이 아닙니다" in bad(C.stage, st, "p1", 3, "setpoint", 30, "jcc"))
    finally:
        st.close()
        os.unlink(db)
    print("\n" + ("✅ 전체 통과" if not _fails else f"❌ 실패 {len(_fails)}건: {_fails}"))
    return 0 if not _fails else 1


if __name__ == "__main__":
    sys.exit(run())
