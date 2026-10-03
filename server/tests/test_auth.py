"""운영 로그인 — 터미널 화면 하나로 서버 인증(아이디·비번, 쿠키 세션, 연속 실패 잠금).

python -m tests.test_auth   (server/ 에서)
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

os.environ["JCC_DASHBOARD_PW"] = "s3cret-pw"       # app import 전에(모듈 로드 때 읽는다)
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
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(path, body=None, use_jar=True):
        req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        o = opener if use_jar else urllib.request.build_opener()
        try:
            with o.open(req, timeout=5) as r:
                raw = r.read().decode("utf-8")
                return r.status, raw
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    try:
        print("=== 운영 로그인 ===")
        s, html = call("/")
        check("첫 화면(/)은 고객 화면 JCC GUARD", s == 200 and "JCC GUARD" in html and 'id="gd-login"' in html)
        s, html = call("/ops")
        check("운영자 화면(/ops) 껍데기(터미널 로그인 포함)는 받음", s == 200 and 'id="boot"' in html)
        check("예전 별도 로그인 페이지는 없음", "접근 인증" not in html)
        s, _ = call("/api/panels")
        check("미로그인 데이터 API는 401", s == 401)
        s, body = call("/api/auth/status")
        j = json.loads(body)
        check("인증 상태: 켜짐·미로그인", j["enabled"] is True and j["authed"] is False and j.get("user") is None, body)
        check("상태 확인·이미지 경로는 열림", call("/health")[0] == 200)

        s, body = call("/api/login", {"user": "ops", "password": "wrong"})
        s2, body2 = call("/api/login", {"user": "nobody", "password": "s3cret-pw"})
        check("틀린 비번·틀린 아이디 → 같은 401 메시지",
              s == 401 and s2 == 401 and json.loads(body)["error"].split(" (")[0] == json.loads(body2)["error"].split(" (")[0],
              json.loads(body)["error"])
        s, body = call("/api/login", {"user": "ops", "password": "s3cret-pw"})
        check("맞는 아이디·비번 → 로그인", s == 200 and any(c.name == "jcc_session" for c in jar))
        check("로그인 후 데이터 API 200", call("/api/panels")[0] == 200)
        import http.client as hc

        def raw_login(remember, https=False):
            c = hc.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
            hdr = {"Content-Type": "application/json"}
            if https:
                hdr["X-Forwarded-Proto"] = "https"
            c.request("POST", "/api/login", json.dumps({"user": "ops", "password": "s3cret-pw", "remember": remember}), hdr)
            r = c.getresponse()
            r.read()
            ck = r.getheader("Set-Cookie") or ""
            c.close()
            return ck
        ck = raw_login(False)
        check("로그인 상태 유지 끄면 브라우저 닫을 때까지(Max-Age 없음)", "jcc_session=" in ck and "Max-Age" not in ck, ck[:90])
        ck = raw_login(True, https=True)
        check("유지 켜면 7일·https(프록시 뒤)면 Secure", "Max-Age=604800" in ck and "Secure" in ck, ck[-60:])
        check("상태 응답에 공통 문의 번호 칸(로그인 화면 안내용)", "support_phone" in json.loads(call("/api/auth/status")[1]))
        check("인증 상태: 로그인됨", json.loads(call("/api/auth/status")[1])["authed"] is True)
        check("쿠키 없는 요청은 여전히 401", call("/api/panels", use_jar=False)[0] == 401)

        call("/api/logout", {})
        check("로그아웃 후 401", call("/api/panels")[0] == 401)

        # 무차별 대입: 5번 틀리면 잠김 → 맞는 비번도 잠시 거부(429)
        codes = [call("/api/login", {"user": "ops", "password": f"guess{i}"})[0] for i in range(5)]
        s, body = call("/api/login", {"user": "ops", "password": "s3cret-pw"})
        check("연속 5회 실패 → 잠금(맞는 비번도 429)", codes == [401] * 5 and s == 429 and "retry_after" in json.loads(body),
              json.loads(body).get("error"))
        appmod._login_fails.clear()               # 잠금 창이 지난 것으로
        check("잠금 해제 후 로그인", call("/api/login", {"user": "ops", "password": "s3cret-pw"})[0] == 200)

        # 프록시(Render) 뒤: 소켓 주소는 모두 프록시 — 손님 IP는 X-Forwarded-For로. 한 손님이 틀려도 남은 안 잠긴다
        def xlogin(ip, user, pw):
            c = hc.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
            c.request("POST", "/api/login", json.dumps({"user": user, "password": pw}),
                      {"Content-Type": "application/json", "X-Forwarded-For": ip})
            r = c.getresponse()
            r.read()
            c.close()
            return r.status
        os.environ["JCC_TRUST_PROXY"] = "1"
        try:
            appmod._login_fails.clear()
            bad = [xlogin("203.0.113.7", "nobody", f"g{i}") for i in range(5)]
            check("프록시 뒤: 틀린 손님 IP만 잠김", bad == [401] * 5 and xlogin("203.0.113.7", "ops", "s3cret-pw") == 429
                  and xlogin("198.51.100.9", "ops", "s3cret-pw") == 200)
            appmod._login_fails.clear()
            spread = [xlogin(f"192.0.2.{i}", "ops", f"g{i}") for i in range(appmod.LOGIN_USER_MAX)]
            check("IP를 바꿔 가며 틀려도 같은 아이디는 잠김", spread == [401] * appmod.LOGIN_USER_MAX
                  and xlogin("192.0.2.200", "ops", "s3cret-pw") == 429)
        finally:
            os.environ.pop("JCC_TRUST_PROXY", None)
            appmod._login_fails.clear()

        # 기기 텔레메트리는 대시보드 로그인과 별개(Bearer 키) — 로그인 없이도 수집은 된다
        s, _ = call("/v1/telemetry", {"device_id": "ccm-x", "readings": []}, use_jar=False)
        check("CCM 텔레메트리는 대시보드 로그인과 무관", s == 200)
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
