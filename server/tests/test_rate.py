"""변화율 규칙 — 10분 새 한도 넘게 오르면 '급상승' 주의, 내려오면 해제. 기록이 5분 안 되면 판정 안 함.

python -m tests.test_rate   (server/ 에서)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def send(st, ts, v, key="cabinet_temp"):
    st.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "A동", "readings": [
        {"key": key, "name": "함내 온도", "unit": "C", "kind": "temp", "value": v, "ok": True, "ts": ts}]})


def open_rate(st):
    with st._lock:
        return st._conn.execute("SELECT detail, severity FROM alarms WHERE kind='alarm_rate' AND cleared_at IS NULL").fetchall()


def run() -> int:
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    t0 = time.time() - 3600
    try:
        send(st, t0, 25.0)
        send(st, t0 + 240, 32.0)
        check("규칙이 없으면 판정 안 함", not open_rate(st))
        new = st.set_setting("ccm-1", "cabinet_temp", rate_up=5)
        check("설정에 저장(10분 한도 +5)", new["rate_up"] == 5.0)
        send(st, t0 + 250, 32.5)
        check("기록이 5분 안 되면 판정 안 함", not open_rate(st))
        send(st, t0 + 360, 31.0)
        a = open_rate(st)
        check("10분 새 +6℃ → 급상승 주의", len(a) == 1 and a[0]["severity"] == "warn" and "급상승" in a[0]["detail"], a and a[0]["detail"])
        send(st, t0 + 420, 33.0)
        check("열려 있으면 다시 열지 않음", len(open_rate(st)) == 1)
        send(st, t0 + 1200, 33.2)
        check("상승이 멈추면(한도의 60% 아래) 해제", not open_rate(st))
        ev = [e["etype"] for e in st.events_since(0, None, 50)]
        check("열림·해제 기록", "alarm_rate" in ev and "alarm_clear" in ev, str(ev))
        st.set_setting("ccm-1", "cabinet_temp", rate_up="")
        check("빈칸이면 규칙 끔", st._settings_map()[("ccm-1", "cabinet_temp")]["rate_up"] is None)
        st.set_setting("ccm-1", "cabinet_temp", rate_up=3)
        st2 = Storage(db)
        with st._lock:
            st._conn.execute("INSERT INTO alarms (device_id, sensor_key, kind, detail, severity, raised_at) VALUES ('ccm-1','cabinet_temp','alarm_rate','x','warn',?)", (t0,))
            st._conn.commit()
        st2.close()
        st3 = Storage(db)
        check("다시 켜져도 열린 급상승 경보를 기억", st3._rate_state.get(("ccm-1", "cabinet_temp")) is True)
        st3.close()
    finally:
        st.close()
        os.unlink(db)
    print("\n" + ("✅ 전체 통과" if not _fails else f"❌ 실패 {len(_fails)}건: {_fails}"))
    return 0 if not _fails else 1


if __name__ == "__main__":
    sys.exit(run())
