"""고객사 범위·권한 표 — 고객 A 계정으로 B의 데이터가 한 줄도 안 보이는가, 등급별 조작 권한.

python -m tests.test_scope   (server/ 에서)
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

os.environ["JCC_DASHBOARD_PW"] = "env-admin-pw"      # app import 전에
os.environ["JCC_DASHBOARD_USER"] = "jccops"
os.environ.pop("JCC_API_KEY", None)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from jcc_server import app as appmod
from jcc_server.predict import Predictor
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run():
    print("=== 권한 표 커버리지 ===")
    src = open(os.path.join(os.path.dirname(HERE), "jcc_server", "app.py"), encoding="utf-8").read()
    lits = set(re.findall(r'(?:path|parsed\.path)\s*==\s*"(/[^"]*)"', src))
    lits |= {p for grp in re.findall(r'(?:path|parsed\.path)\s+in\s+\(([^)]*)\)', src) for p in re.findall(r'"(/[^"]*)"', grp)}
    missing = sorted(p for p in lits if appmod.route_policy("GET", p) is None and appmod.route_policy("POST", p) is None)
    check(f"코드의 모든 경로({len(lits)}개)가 권한 표에 있음", not missing, str(missing))
    check("표에 없는 경로는 정책 없음(→ 거부)", appmod.route_policy("GET", "/api/secret-new") is None)
    check("이력 경로(정규식)도 분류", appmod.route_policy("GET", "/api/devices/ccm-1/history") == "read")

    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    st.predictor = Predictor()
    ac = st.accounts
    ca, cb = ac.create_customer("A 제조"), ac.create_customer("B 물류")
    ac.assign_panel("panel-A", ca)
    ac.assign_panel("panel-B", cb)
    now = time.time()
    for dev, panel in (("ccm-a1", "panel-A"), ("ccm-b1", "panel-B"), ("ccm-x1", "panel-X")):   # X = 미배정
        st.ingest({"device_id": dev, "panel": panel, "panel_name": panel, "readings": [
            {"key": "h2_lel", "name": "수소", "unit": "%LEL", "value": 0.5, "ok": True, "ts": now}]})
        st.raise_alarm(dev, "h2_lel", "alarm", f"{dev} 경보")
        st.log_event(dev, "h2_lel", "note", f"{dev} 이벤트")
    st.log_event("", "", "tuning_apply", "시스템 이벤트")
    alarm_ids = {a["device_id"]: a["id"] for a in st.list_active_alarms()}
    mid, mtemp = ac.create_user("kim.a", "manager", ca)
    vid, vtemp = ac.create_user("lee.a", "viewer", ca)
    ac.create_user("park.b", "manager", cb)

    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def client():
        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

        def call(path, body=None):
            req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                with op.open(req, timeout=10) as r:
                    return r.status, json.loads(r.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8")
                try:
                    return e.code, json.loads(raw)
                except ValueError:
                    return e.code, {"raw": raw}
        return call

    def login(call, user, pw):
        return call("/api/login", {"user": user, "password": pw})

    try:
        print("\n=== 로그인·비번 변경 강제 ===")
        m = client()
        s, j = login(m, "kim.a", mtemp)
        check("DB 계정 로그인", s == 200 and j.get("user", {}).get("role") == "manager", str(j)[:120])
        s, j = m("/api/panels")
        check("임시 비번 상태에선 데이터 API 막힘", s == 403 and j.get("must_change"), str(j)[:120])
        s, j = m("/api/auth/status")
        check("상태에 변경 필요 표시", j.get("user", {}).get("must_change") is True and j.get("role") == "manager")
        s, j = m("/api/me/password", {"old": mtemp, "new": "manager-pw-2026"})
        check("비번 변경", s == 200, str(j))
        s, j = login(m, "kim.a", "manager-pw-2026")
        check("새 비번으로 다시 로그인", s == 200)

        print("\n=== 고객 A 담당자: A만 보인다 ===")
        s, j = m("/api/panels")
        check("판넬: A만", s == 200 and [p["panel"] for p in j["panels"]] == ["panel-A"], str([p["panel"] for p in j["panels"]]))
        s, j = m("/api/devices")
        check("기기: A만", [d["device_id"] for d in j["devices"]] == ["ccm-a1"])
        s, j = m("/api/alarms")
        check("경보: A만", [a["device_id"] for a in j["alarms"]] == ["ccm-a1"])
        s, j = m("/api/incidents")
        check("사건: A만", all(set(x.get("device_id") for x in j["alarms"]) <= {"ccm-a1"} for _ in [0])
              and all(i["device_id"] in ("ccm-a1", "") for i in j["incidents"]))
        s, j = m("/api/events?limit=200")
        devs = {e["device_id"] for e in j["events"]}
        check("이벤트: A와 A 판넬만(시스템 이벤트 없음)", devs <= {"ccm-a1", "panel-A"} and "ccm-a1" in devs, str(devs))
        s, j = m("/api/events?device_id=ccm-b1")
        check("이벤트: B를 콕 집어도 안 보임", j.get("events") == [], str(j)[:100])
        s, j = m("/api/devices/ccm-a1/history?sensor=h2_lel")
        check("이력: A 기기 됨", s == 200 and j["points"])
        s, j = m("/api/devices/ccm-b1/history?sensor=h2_lel")
        check("이력: B 기기 403", s == 403)
        s, j = m("/api/report?days=1")
        check("리포트: A 센서만", s == 200 and {x["device_id"] for x in j["sensors"]} == {"ccm-a1"}
              and j["summary"]["devices"]["total"] == 1, str({x["device_id"] for x in j["sensors"]}))
        s, j = m("/api/predict")
        check("예지: A 판넬만", s == 200 and all(p["panel"] == "panel-A" for p in j["panels"]))

        print("\n=== 담당자 조작 ===")
        s, j = m("/api/alarm/ack", {"alarm_id": alarm_ids["ccm-a1"]})
        check("A 경보 확인 됨", s == 200 and j.get("ok"))
        s, j = m("/api/alarm/ack", {"alarm_id": alarm_ids["ccm-b1"]})
        check("B 경보 확인 403", s == 403)
        s, j = m("/api/predict/actuator", {"panel": "panel-A", "actuator": "vent", "action": "open"})
        check("A 벤트 수동 조작 됨", s == 200 and j.get("ok"), str(j)[:100])
        s, j = m("/api/predict/actuator", {"panel": "panel-B", "actuator": "vent", "action": "open"})
        check("B 벤트 조작 403", s == 403)
        evs = [e["detail"] for e in st.list_events(limit=50) if e["etype"] in ("actuator", "ack")]
        check("조작 기록에 사용자 이름", any("kim.a" in d for d in evs), str(evs[:3]))
        for path, body in (("/api/tuning/config", None), ("/api/heal/config", None), ("/api/healthcheck", None),
                           ("/api/admin/accounts", None), ("/api/discover", {}), ("/api/panel/name", {"panel": "panel-A", "name": "x"}),
                           ("/api/predict/baseline", {"panel": "panel-A", "action": "relearn"}), ("/api/setting", {})):
            s, _ = m(path, body)
            check(f"JCC 전용 {path} → 403", s == 403, str(s))

        print("\n=== 보기 전용 ===")
        v = client()
        login(v, "lee.a", vtemp)
        v("/api/me/password", {"old": vtemp, "new": "viewer-pw-2026"})
        login(v, "lee.a", "viewer-pw-2026")
        s, j = v("/api/panels")
        check("보기: A 판넬 보임", s == 200 and [p["panel"] for p in j["panels"]] == ["panel-A"])
        s, _ = v("/api/predict/actuator", {"panel": "panel-A", "actuator": "vent", "action": "close"})
        check("보기 전용은 조작 403", s == 403)
        s, _ = v("/api/alarm/ack", {"alarm_id": alarm_ids["ccm-a1"]})
        check("보기 전용은 경보 확인 403", s == 403)

        print("\n=== JCC(환경변수 비상 계정) ===")
        a = client()
        s, j = login(a, "jccops", "env-admin-pw")
        check("비상 admin 로그인", s == 200 and j.get("user", {}).get("role") == "admin")
        s, j = a("/api/panels")
        check("admin은 전부(미배정 포함)", {p["panel"] for p in j["panels"]} == {"panel-A", "panel-B", "panel-X"})
        s, j = a("/api/tuning/config")
        check("admin은 JCC 전용 기능 됨", s == 200)

        print("\n=== 계정 관리 API (admin) ===")
        s, j = a("/api/admin/customer", {"name": "C 병원"})
        cc = j.get("id")
        check("고객사 추가", s == 200 and isinstance(cc, int))
        s, _ = a("/api/admin/panel", {"panel": "panel-X", "customer_id": cc})
        check("미배정 판넬을 C에 배정", s == 200 and st.accounts.panel_owner_map().get("panel-X") == cc)
        s, j = a("/api/admin/receivers", {"customer_id": cc, "numbers": ["010-5555-6666"]})
        check("C 알림 번호", s == 200 and j["numbers"] == ["01055556666"])
        s, j = a("/api/admin/user", {"username": "choi.c", "role": "viewer", "customer_id": cc})
        temp_c = j.get("temp_password", "")
        check("C 보기 계정 추가 → 임시 비번 한 번", s == 200 and len(temp_c) >= 12)
        s, j = a("/api/admin/user", {"username": "jccops", "role": "admin", "customer_id": None})
        check("비상 관리자 아이디와 같은 계정 거부", s == 400)
        s, j = a("/api/admin/user", {"username": "x1", "role": "manager", "customer_id": 99999})
        check("없는 고객사 계정 거부", s == 400)
        s, j = a("/api/admin/accounts")
        cust = {c["name"]: c for c in j["customers"]}
        check("관리 조회: 고객사·판넬·번호·계정", s == 200 and cust["C 병원"]["panels"] == ["panel-X"]
              and cust["C 병원"]["receivers"] == ["01055556666"] and any(u["username"] == "choi.c" for u in j["users"])
              and all("pw_hash" not in u for u in j["users"]))
        cu = next(u for u in j["users"] if u["username"] == "choi.c")
        c = client()
        login(c, "choi.c", temp_c)
        c("/api/me/password", {"old": temp_c, "new": "choi-pw-2026x"})
        login(c, "choi.c", "choi-pw-2026x")
        check("C 계정은 X 판넬만", [p["panel"] for p in c("/api/panels")[1]["panels"]] == ["panel-X"])
        s, j = a("/api/admin/user/reset", {"user_id": cu["id"]})
        check("비번 초기화 → C 세션 끊김", s == 200 and c("/api/panels")[0] == 401)
        s, _ = a("/api/admin/user/disable", {"user_id": cu["id"], "disabled": True})
        check("계정 끄기 → 로그인 불가", s == 200 and login(c, "choi.c", j["temp_password"])[0] == 401)
        s, _ = m("/api/admin/customer", {"name": "침입"})
        check("담당자는 관리 API 403", s == 403)
        blob = json.dumps(st.list_events(limit=200), ensure_ascii=False)
        check("기록에 임시 비번이 남지 않음", temp_c not in blob and j["temp_password"] not in blob)
        check("관리 작업 기록(누가)", "고객사 추가: C 병원 (jccops)" in blob)

        print("\n=== 세션 ===")
        other = client()
        login(other, "kim.a", "manager-pw-2026")
        check("다른 기기 세션 유효", other("/api/panels")[0] == 200)
        m("/api/me/password", {"old": "manager-pw-2026", "new": "manager-pw-2027"})
        check("비번 바꾸면 다른 세션 즉시 끊김", other("/api/panels")[0] == 401)
        s, _ = m("/api/logout", {})
        check("로그아웃 뒤 401", m("/api/panels")[0] == 401)
        anon = client()
        check("미로그인 401", anon("/api/devices")[0] == 401 and anon("/api/tuning/config")[0] == 401)
        check("표에 없는 경로 403", anon("/api/secret-new")[0] in (401, 403))
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
