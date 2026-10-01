"""경보 확인 + 원인·조치 메모 — 저장·나중에 고치기·해제 뒤 메모·잘못된 원인 무시·월간 오경보 수·AI 도구.

python -m tests.test_ack_note   (server/ 에서)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from types import SimpleNamespace as NS

os.environ.pop("JCC_DASHBOARD_PW", None)
os.environ.pop("JCC_API_KEY", None)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from jcc_server import app as appmod
from jcc_server import monthly
from jcc_server.ai import _Tools
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
    cid = st.accounts.create_customer("A")
    st.accounts.assign_panel("p1", cid)
    st.ingest({"device_id": "ccm-1", "panel": "p1", "readings": [
        {"key": "h2", "name": "수소", "unit": "%LEL", "value": 30, "ok": True, "ts": time.time()}]})
    for k in ("a", "b", "c"):
        st.raise_alarm("ccm-1", f"h2{k}", "alarm", f"수소 {k} 경보")
    ids = {a["sensor_key"]: a["id"] for a in st.list_active_alarms()}
    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def ack(body):
        req = urllib.request.Request(base + "/api/alarm/ack", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    try:
        check("원인·메모와 함께 확인", ack({"alarm_id": ids["h2a"], "cause": "false", "note": "용접 작업 중"})["ok"])
        a = next(x for x in st.list_active_alarms() if x["id"] == ids["h2a"])
        check("활성 목록에 원인·메모", a["cause"] == "false" and a["ack_note"] == "용접 작업 중" and a["acked_at"])
        check("메모 없이 확인도 그대로", ack({"alarm_id": ids["h2b"]})["ok"])
        check("잘못된 원인은 무시", ack({"alarm_id": ids["h2c"], "cause": "hack", "note": "x" * 300})["ok"])
        c = next(x for x in st.list_active_alarms() if x["id"] == ids["h2c"])
        check("메모 길이 200자 제한·원인 비움", c["cause"] is None and len(c["ack_note"]) == 200)
        check("확인한 경보에 나중에 메모", ack({"alarm_id": ids["h2b"], "cause": "real", "note": "단자 재조임"})["ok"])
        b = next(x for x in st.list_active_alarms() if x["id"] == ids["h2b"])
        check("나중 메모 반영", b["cause"] == "real" and b["ack_note"] == "단자 재조임")
        st.clear_alarm("ccm-1", "h2b", "alarm", "alarm_clear", "정상 복귀")
        check("해제된 뒤에도 메모 고치기", ack({"alarm_id": ids["h2b"], "note": "M6 단자 2개 재조임"})["ok"])
        row = st.get_alarm(ids["h2b"])
        check("원인은 유지·메모만 바뀜", row["cause"] == "real" and row["ack_note"] == "M6 단자 2개 재조임")
        check("메모도 원인도 없으면 이미 확인된 경보는 그대로(ok=false)", ack({"alarm_id": ids["h2a"]})["ok"] is False)
        ev = [e["detail"] for e in st.list_events(limit=30) if e["etype"] == "ack"]
        check("기록에 원인·메모", any("오경보 — 용접 작업 중" in e for e in ev) and any(e.startswith("경보 메모") for e in ev), str(ev[:3]))

        rep = monthly.build_monthly(st, cid, monthly.prev_period(time.time() + 40 * 86400), time.time() + 40 * 86400)
        check("월간: 오경보·시험 작업 수", rep["summary"]["alarms_false"] == 1, str(rep["summary"].get("alarms_false")))
        out = _Tools(st, {"p1"}).t_alarm_history(days=1)
        al = {x["ref"]: x for x in out["alarms"]}
        check("AI 도구에 원인·조치", al[f"경보#{ids['h2a']}"]["cause"] == "오경보"
              and al[f"경보#{ids['h2b']}"]["action_note"] == "M6 단자 2개 재조임")
    finally:
        srv.shutdown()
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
