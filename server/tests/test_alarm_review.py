"""경보 이력 — 원인 거르기·해제된 경보에 나중 메모·오경보 잦은 센서.

python -m tests.test_alarm_review   (server/ 에서)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from jcc_server import alarm_review
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
    now = time.time()
    st.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "1번", "readings": [
        {"key": "h2", "name": "수소", "unit": "%LEL", "value": 0.1, "ok": True, "ts": now}]})
    with st._lock:
        for i, c in enumerate(["false", "false", "work", "real", None]):
            st._conn.execute("INSERT INTO alarms (device_id, sensor_key, kind, detail, severity, raised_at, cleared_at, cause) "
                             "VALUES ('ccm-1','h2','alarm','x','crit',?,?,?)", (now - 3600 + i, now - 3000 + i, c))
        st._conn.commit()
    unlabeled = next(a for a in st.alarms_since(0, None, 100) if not a.get("cause"))
    ok = st.ack_alarm(unlabeled["id"], "kim", "센서 청소", "false")
    a = st.get_alarm(unlabeled["id"])
    check("확인 없이 해제된 경보에도 나중에 원인·메모", ok and a["cause"] == "false" and a["ack_note"] == "센서 청소"
          and a["acked_at"] is None)
    h = alarm_review.history(st, None, 7)
    check("이력: 이름·지속시간", h["total"] == 5 and h["alarms"][0]["sensor_name"] == "수소" and h["alarms"][0]["minutes"] == 10.0)
    check("원인으로 거르기", alarm_review.history(st, None, 7, "false")["total"] == 3
          and alarm_review.history(st, None, 7, "none")["total"] == 0)
    n = h["noisy"]
    check("오경보 잦은 센서 짚음", len(n) == 1 and n[0]["false"] == 4 and n[0]["sensor_name"] == "수소", str(n))
    check("고객 범위 밖이면 비어 있음", alarm_review.history(st, {"ccm-x"}, 7)["total"] == 0)
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
