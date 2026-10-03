"""회원가입 — 가입 코드로 바로 가입(보기 전용), 코드 없으면 가입 신청 → JCC 승인.

python -m tests.test_signup   (server/ 에서)
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

os.environ["JCC_DASHBOARD_PW"] = "s3cret-pw"       # app import 전에(모듈 로드 때 읽는다) — 인증 켬
os.environ["JCC_DASHBOARD_USER"] = "ops"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server import app as appmod
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
    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def client():
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(o, path, body=None):
        req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with o.open(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"raw": raw}

    def signup(**kw):
        appmod._signup_hits.clear()                # 남용 방지 카운터는 마지막에 따로 시험
        body = dict(agree=True, name="김현장", phone="010-1234-5678", password="field-pass-01")
        body.update(kw)
        return call(client(), "/api/signup", body)

    def login(user, pw):
        appmod._login_fails.clear()
        o = client()
        s, j = call(o, "/api/login", {"user": user, "password": pw})
        return s, j, o

    try:
        print("=== 회원가입 ===")
        s, j = call(client(), "/api/auth/status")
        check("상태: 회원가입 켜짐·비번 최소 길이 알려 줌", j.get("signup") is True and j.get("min_pw") == 10, str(j))

        s, _, ops = login("ops", "s3cret-pw")
        s, j = call(ops, "/api/admin/customer", {"name": "평택 2공장"})
        cid = j["id"]
        s, j = call(ops, "/api/admin/signup_code", {"customer_id": cid, "on": True})
        code = j.get("code", "")
        check("가입 코드 발급(XXXX-XXXX)", s == 200 and len(code) == 9 and code[4] == "-", code)

        s, j = signup(code=code, username="kim.a", agree=False)
        check("동의 안 하면 거부", s == 400 and "동의" in j.get("error", ""))
        s, j = signup(code="ZZZZ-ZZZZ", username="kim.a")
        check("틀린 코드는 거부", s == 400 and "가입 코드" in j.get("error", ""))
        s, j = signup(code=code, username="kim.a", password="short")
        check("짧은 비번 거부", s == 400 and "10자" in j.get("error", ""))
        s, j = signup(code=code, username="kim.a", phone="12")
        check("휴대폰 번호 검사", s == 400 and "휴대폰" in j.get("error", ""))
        s, j = signup(code=code, username="ops")
        check("비상 관리자 아이디는 못 씀", s == 400)

        s, j = signup(code=code.lower().replace("-", ""), username="kim.a")
        check("코드로 가입(소문자·하이픈 없어도) → 바로 계정", s == 200 and j.get("mode") == "created"
              and j.get("customer") == "평택 2공장", str(j))
        s, j, me = login("kim.a", "field-pass-01")
        check("가입한 비번으로 바로 로그인", s == 200 and j["user"]["role"] == "viewer"
              and j["user"]["customer"] == "평택 2공장" and j["user"]["must_change"] is False, str(j))
        s, _ = call(me, "/api/admin/accounts")
        check("코드 가입 계정은 관리 화면 못 봄", s == 403)
        s, j = signup(code=code, username="kim.a")
        check("같은 아이디 두 번은 거부", s == 400 and "이미" in j.get("error", ""))

        print("--- 코드 없이 가입 신청 ---")
        s, j = signup(username="lee.b")
        check("회사명 없으면 거부", s == 400 and "회사명" in j.get("error", ""))
        s, j = signup(username="lee.b", company="평택 2공장", name="이담당", password="request-pw-02")
        check("가입 신청 접수", s == 200 and j.get("mode") == "requested", str(j))
        s, j = signup(username="lee.b", company="다른 회사")
        check("대기 중 신청과 같은 아이디는 거부", s == 400)
        s, j, _ = login("lee.b", "request-pw-02")
        check("승인 전 로그인 → '확인 중' 안내", s == 403 and j.get("pending") is True, str(j))
        s, j, _ = login("lee.b", "wrong-password")
        check("비번이 틀리면 신청 여부를 알려 주지 않음", s == 401 and "pending" not in j)

        s, j = call(ops, "/api/admin/accounts")
        reqs = j.get("signup_requests", [])
        check("관리 화면에 신청이 보임(비번 해시는 없음)", len(reqs) == 1 and reqs[0]["username"] == "lee.b"
              and reqs[0]["company"] == "평택 2공장" and "pw_hash" not in reqs[0], str(reqs))
        check("관리 화면 고객사에 가입 코드", any(c.get("signup_code") == code for c in j["customers"]))
        rid = reqs[0]["id"]
        s, j = call(ops, "/api/admin/signup/approve", {"request_id": rid, "customer_id": cid, "role": "admin"})
        check("승인 등급은 담당자·보기 전용만", s == 400)
        s, j = call(ops, "/api/admin/signup/approve", {"request_id": rid, "customer_id": cid, "role": "manager"})
        check("승인", s == 200 and j.get("username") == "lee.b", str(j))
        s, j, _ = login("lee.b", "request-pw-02")
        check("승인 뒤 신청한 비번으로 로그인(담당자)", s == 200 and j["user"]["role"] == "manager", str(j))
        s, j = call(ops, "/api/admin/signup/approve", {"request_id": rid, "customer_id": cid, "role": "viewer"})
        check("같은 신청 두 번 승인 안 됨", s == 400)
        s, j = call(ops, "/api/admin/accounts")
        u = next(x for x in j["users"] if x["username"] == "lee.b")
        check("계정에 이름·휴대폰이 남음", u["full_name"] == "이담당" and u["phone"] == "010-1234-5678", str(u))

        signup(username="park.c", company="모르는 회사", password="request-pw-03")
        rid2 = call(ops, "/api/admin/accounts")[1]["signup_requests"][0]["id"]
        s, _ = call(ops, "/api/admin/signup/reject", {"request_id": rid2})
        s2, j2, _ = login("park.c", "request-pw-03")
        check("거절하면 로그인 안 되고 목록에서 빠짐", s == 200 and s2 == 401
              and not call(ops, "/api/admin/accounts")[1]["signup_requests"])
        s, j = signup(username="park.c", company="모르는 회사", password="request-pw-03")
        check("거절된 아이디로 다시 신청 가능", s == 200)

        print("--- 코드 관리 ---")
        s, j = call(ops, "/api/admin/signup_code", {"customer_id": cid, "on": True})
        new = j["code"]
        s, j = signup(code=code, username="old.code")
        s2, j2 = signup(code=new, username="new.code")
        check("새로 발급하면 예전 코드는 못 씀", s == 400 and s2 == 200 and new != code)
        call(ops, "/api/admin/signup_code", {"customer_id": cid, "on": False})
        s, j = signup(code=new, username="off.code")
        check("코드를 끄면 코드 가입 안 됨", s == 400)
        s, _ = call(client(), "/api/admin/signup_code", {"customer_id": cid, "on": True})
        check("코드 발급은 관리자만", s in (401, 403))

        print("--- 끄기·남용 방지 ---")
        os.environ["JCC_SIGNUP"] = "off"
        s, j = signup(username="x.off", company="회사")
        st_on = call(client(), "/api/auth/status")[1].get("signup")
        os.environ.pop("JCC_SIGNUP")
        check("JCC_SIGNUP=off면 가입 안 받음", s == 403 and st_on is False)
        appmod._signup_hits.clear()
        codes = []
        for i in range(appmod.SIGNUP_MAX + 1):
            codes.append(call(client(), "/api/signup", {"agree": True, "username": f"spam{i}", "password": "x"})[0])
        check("같은 곳에서 연달아 가입 시도하면 잠시 막음", codes[-1] == 429 and 429 not in codes[:-1], str(codes))
        appmod._signup_hits.clear()
        code3 = call(ops, "/api/admin/signup_code", {"customer_id": cid, "on": True})[1]["code"]
        many = [call(client(), "/api/signup", {"agree": True, "code": code3, "username": f"staff{i}", "name": "직원",
                                                "phone": "010-1111-2222", "password": "staff-pass-01"})[0]
                for i in range(appmod.SIGNUP_MAX + 3)]
        check("한 사무실(같은 IP) 직원 여럿이 코드로 가입해도 막히지 않음", many == [200] * (appmod.SIGNUP_MAX + 3), str(many))
    finally:
        srv.shutdown()
        try:
            os.remove(db)
        except OSError:
            pass

    print("\n" + ("✅ 전체 통과" if not _fails else f"❌ 실패 {len(_fails)}건: {_fails}"))
    return 0 if not _fails else 1


if __name__ == "__main__":
    sys.exit(run())
