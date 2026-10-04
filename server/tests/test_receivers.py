"""알림 받는 사람 — 이름·받는 시간(항상/근무시간/야간·주말)·정기 문자, 아무도 없으면 전원, 권한(고객 관리자만), 번호 가림.

python -m tests.test_receivers   (server/ 에서)
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

os.environ["JCC_DASHBOARD_PW"] = "rc-admin-pw"
os.environ["JCC_DASHBOARD_USER"] = "jccops"
os.environ.pop("JCC_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server.accounts import is_work_hours
from jcc_server.app import make_server
from jcc_server.storage import Storage

_fails = []
KST = timezone(timedelta(hours=9))


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run():
    mon10 = datetime(2026, 10, 5, 10, 0, tzinfo=KST).timestamp()     # 월요일 오전 10시
    mon22 = datetime(2026, 10, 5, 22, 0, tzinfo=KST).timestamp()     # 월요일 밤 10시
    sat10 = datetime(2026, 10, 10, 10, 0, tzinfo=KST).timestamp()    # 토요일 오전
    check("근무시간 판정", is_work_hours(mon10) and not is_work_hours(mon22) and not is_work_hours(sat10))

    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    ac = st.accounts
    ca, cb = ac.create_customer("A 제조"), ac.create_customer("B 물류")
    rows = ac.set_receiver_list(ca, [
        {"number": "010-1111-2222", "name": "김반장", "hours": "always", "reports": True},
        {"number": "010-3333-4444", "name": "설비팀", "hours": "day", "reports": False},
        {"number": "010-5555-6666", "name": "야간 당직", "hours": "night", "reports": False},
        {"number": "010-1111-2222", "name": "중복"}, {"number": "123", "name": "이상한 번호"},
        {"number": "010-7777-8888", "hours": "weird"}])
    check("정리: 중복·이상한 번호 버림, 모르는 시간은 '항상'", [r["number"] for r in rows] == ["01011112222", "01033334444", "01055556666", "01077778888"]
          and rows[3]["hours"] == "always", str(rows))
    check("근무시간 알림: 항상 + 근무시간", ac.receivers_for(ca, "alarm", mon10) == ["01011112222", "01033334444", "01077778888"])
    check("밤 알림: 항상 + 야간 당직", ac.receivers_for(ca, "alarm", mon22) == ["01011112222", "01055556666", "01077778888"])
    check("주말 낮도 당직", "01055556666" in ac.receivers_for(ca, "alarm", sat10) and "01033334444" not in ac.receivers_for(ca, "alarm", sat10))
    check("정기 문자는 받기로 한 사람만", ac.receivers_for(ca, "report") == ["01011112222", "01077778888"])
    ac.set_receiver_list(cb, [{"number": "010-9999-0000", "hours": "day"}])
    check("그 시각에 받을 사람이 없으면 전원(위험 알림을 아무도 못 받게 두지 않음)", ac.receivers_for(cb, "alarm", mon22) == ["01099990000"])
    ac.set_receivers(ca, ["010-1111-2222", "010-2222-3333"])
    kept = {r["number"]: r for r in ac.list_receivers(ca)}
    check("직원 화면(번호만)으로 고쳐도 남는 사람의 설정은 그대로", kept["01011112222"]["name"] == "김반장" and kept["01022223333"]["hours"] == "always")

    print("\n=== API 권한 ===")
    st.ingest({"device_id": "ccm-a", "panel": "pa", "panel_name": "A판넬", "readings": []})
    ac.assign_panel("pa", ca)
    srv = make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def client():
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def call(path, body=None):
            req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                with op.open(req, timeout=10) as r:
                    return r.status, json.loads(r.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as ex:
                return ex.code, json.loads(ex.read().decode("utf-8") or "{}")
        return call

    def login(name, role, cid):
        _, temp = ac.create_user(name, role, cid)
        c = client()
        c("/api/login", {"user": name, "password": temp})
        c("/api/me/password", {"old": temp, "new": name + "-pw-2026x"})
        c("/api/login", {"user": name, "password": name + "-pw-2026x"})
        return c
    try:
        m = login("park.a", "manager", ca)
        s1, j = m("/api/guard/receivers")
        check("고객 관리자: 자기 고객사 목록·고칠 수 있음", s1 == 200 and j["can_edit"] and j["receivers"][0]["number"] == "01011112222", str(j)[:160])
        s1, j = m("/api/guard/receivers", {"receivers": [{"number": "010-1111-2222", "name": "김반장", "hours": "night", "reports": True}],
                                           "customer_id": cb})
        check("고객 관리자: 저장(다른 고객사 번호를 넣어도 자기 고객사만)", s1 == 200 and ac.list_receivers(ca)[0]["hours"] == "night"
              and ac.list_receivers(cb)[0]["number"] == "01099990000", str(j)[:160])
        v = login("kim.a", "viewer", ca)
        s1, j = v("/api/guard/receivers")
        check("보기 전용: 번호는 가림·못 고침", s1 == 200 and not j["can_edit"] and "****" in j["receivers"][0]["number"], str(j)[:160])
        check("보기 전용: 저장 403", v("/api/guard/receivers", {"receivers": []})[0] == 403)
        a = client()
        a("/api/login", {"user": "jccops", "password": "rc-admin-pw"})
        check("JCC 관리자: 고객사 지정해 저장", a("/api/guard/receivers", {"customer_id": cb, "receivers": [{"number": "010-1234-5678"}]})[0] == 200
              and ac.receivers_for(cb) == ["01012345678"])
        check("JCC 관리자: 고객사 없으면 400", a("/api/guard/receivers", {"receivers": []})[0] == 400)
        s1, j = a(f"/api/guard/receivers?customer_id={cb}")
        check("JCC 관리자: 고객사 미리보기 목록", s1 == 200 and j["receivers"][0]["number"] == "01012345678", str(j)[:160])
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
