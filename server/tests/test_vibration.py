"""진동 ISO 10816 존 — 존은 경보 기준과 같은 말을 하고, 설비 등급은 표준값을 채운다. 고객 화면 센서에 존이 붙는다.

python -m tests.test_vibration   (server/ 에서)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server import guard
from jcc_server import vibration as V
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run() -> int:
    print("=== 존 계산 ===")
    check("기준 아래 40% 미만 = A", V.zone(0.9, 2.5, 4.0) == "A")
    check("주의 기준까지 = B", V.zone(1.0, 2.5, 4.0) == "B" and V.zone(2.5, 2.5, 4.0) == "B")
    check("주의 넘으면 C(경보 판정 '값 > 주의'와 같은 비교)", V.zone(2.51, 2.5, 4.0) == "C")
    check("위험 기준부터 D", V.zone(4.0, 2.5, 4.0) == "D")
    check("위험 기준이 없으면 주의×2.5", V.zone(5.9, 2.4, None) == "C" and V.zone(6.0, 2.4, None) == "D")
    check("값·기준이 없으면 None", V.zone(None, 2.5, 4.0) is None and V.zone(1.0, None, None) is None)
    for c, (ab, bc, cd) in V.CLASSES.items():   # 표준 표(R10 수열)의 비율이 우리 계산과 4% 안에서 맞는지(A/B ≈ B/C×0.4, C/D ≈ B/C×2.5)
        check(f"등급 {c}: 표 경계가 존 계산과 거의 같음", abs(ab / (bc * V.AB_RATIO) - 1) < 0.04 and abs(cd / (bc * 2.5) - 1) < 0.04, f"{ab} {bc} {cd}")
    check("등급 → 채울 기준값", V.class_thresholds("II") == {"alarm_warn": 2.8, "alarm_max": 7.1})
    check("기준값 → 등급(같을 때만)", V.class_of(4.5, 11.2) == "III" and V.class_of(2.5, 4.0) is None)

    print("\n=== 고객 화면 센서 ===")
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    now = time.time()
    try:
        st.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "A동", "readings": [
            {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 30.0, "ok": True, "ts": now},
            {"key": "vibration", "name": "진동", "unit": "mm/s", "kind": "vibration", "value": 3.1, "ok": True, "ts": now}]})
        with st._lock:
            st._conn.execute("INSERT OR REPLACE INTO discovered (device_id, sensor_key, name, unit, kind, alarm_warn, alarm_max) "
                             "VALUES ('ccm-1', 'vibration', '진동', 'mm/s', 'vibration', 2.5, 4.0)")
            st._conn.execute("INSERT OR REPLACE INTO discovered (device_id, sensor_key, name, unit, kind, alarm_warn, alarm_max) "
                             "VALUES ('ccm-1', 'cabinet_temp', '함내 온도', 'C', 'temp', 38, 45)")
            st._conn.commit()
        lat = guard._latest(st)["p1"]
        vib = next((x for x in lat["sensors"] if x["kind"] == "vibration"), None)
        check("진동 센서에 존 C(주의 2.5 넘음)", vib and vib.get("zone") == "C" and vib["level"] == "warn", str(vib))
        check("진동 아닌 센서엔 존 없음", all("zone" not in x for x in lat["sensors"] if x["kind"] != "vibration"))
    finally:
        st.close()
        os.unlink(db)
    print("\n" + ("✅ 전체 통과" if not _fails else f"❌ 실패 {len(_fails)}건: {_fails}"))
    return 0 if not _fails else 1


if __name__ == "__main__":
    sys.exit(run())
