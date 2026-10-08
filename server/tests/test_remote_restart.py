"""원격 재시작 — 실제 CCM에 명령이 가는지, 끊긴 CCM은 거절, 실기 전원 끄기 막음, 다시 켜진 보고로 '확인',
자동 복구(L1·L2)도 실제 명령을 보내고 확인이 있어야 '원격 재시작으로 복구'.

python -m tests.test_remote_restart   (server/ 에서)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ.pop("JCC_DEMO", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def events(st, dev, et):
    with st._lock:
        return [dict(r) for r in st._conn.execute("SELECT * FROM events WHERE device_id=? AND etype=?", (dev, et)).fetchall()]


def run():
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    now = time.time()
    st.ingest({"device_id": "ccm-a", "panel": "p", "panel_name": "판넬", "agent_start": now - 3600,
               "readings": [{"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "value": 30, "ok": True, "ts": now}]})

    print("=== 사람이 누른 재시작 ===")
    ok, why = st.device_command("ccm-a", "restart")
    check("연결된 CCM: 명령 접수", ok and not why)
    cmds = st.take_edge_commands("ccm-a")
    check("다음 보고 응답에 재시작 명령", cmds == [{"system": "restart_agent", "sensor_key": ""}], str(cmds))
    check("가짜로 꺼짐 표시 안 함(last_seen 그대로)", st.device_online("ccm-a"))
    st.note_command_results("ccm-a", {"agent_start": now - 3600})
    check("아직 옛 프로그램이 보고하면 확인 아님", not events(st, "ccm-a", "restart_ok"))
    st.note_command_results("ccm-a", {"agent_start": time.time() + 1})
    check("다시 켜진 보고 → '재시작 확인'", len(events(st, "ccm-a", "restart_ok")) == 1)

    ok, why = st.device_command("ccm-a", "shutdown")
    check("실제 CCM 전원 끄기는 막음", not ok and "다시 켤 방법" in why, why)
    ok, why = st.device_command("ccm-x", "restart")
    check("없는 장비 거절", not ok)
    with st._lock:
        st._conn.execute("UPDATE devices SET last_seen=? WHERE device_id='ccm-a'", (time.time() - 600,))
        st._conn.commit()
    ok, why = st.device_command("ccm-a", "restart")
    check("끊긴 CCM은 명령을 보낼 수 없다고 거절", not ok and "연결이 끊긴" in why, why)

    print("\n=== 자동 복구 ===")
    st.restart_device("ccm-a", note="자동복구 L2: CCM 재시작 1/2차 시도 (CCM 침묵)")
    e = events(st, "ccm-a", "heal2_restart")
    check("끊긴 CCM L2: '닿지 않음'으로 정직하게 기록, 명령은 안 쌓음", e and "닿지 않음" in e[-1]["detail"] and not st.take_edge_commands("ccm-a"), str(e[-1:]))
    with st._lock:
        st._conn.execute("UPDATE devices SET last_seen=? WHERE device_id='ccm-a'", (time.time(),))
        st._conn.commit()
    st.restart_channel("ccm-a", "cabinet_temp", note="자동복구 L1: 채널 재시작 1/3차 시도 (silent)")
    cmds = st.take_edge_commands("ccm-a")
    check("L1: 채널 재초기화 명령이 CCM으로", cmds == [{"system": "restart_channel", "sensor_key": "cabinet_temp"}], str(cmds))
    st.note_command_results("ccm-a", {"cmd_done": [{"system": "restart_channel", "sensor_key": "cabinet_temp", "ts": time.time()}]})
    check("CCM이 재초기화했다고 보고 → '채널 재시작 확인'", len(events(st, "ccm-a", "channel_restart_ok")) == 1)

    print("\n=== 데모 서버는 예전처럼 ===")
    os.environ["JCC_DEMO"] = "1"
    try:
        ok, _ = st.device_command("ccm-a", "shutdown")
        check("데모: 전원 끄기 됨(꺼진 것처럼)", ok and not st.device_online("ccm-a"))
    finally:
        os.environ.pop("JCC_DEMO", None)
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
