"""주간 안전 요약 — 월요일 오전 한 번, 켠 고객사만, 실제 기록 숫자로. (실제 발송 없이 send를 바꿔 끼워 시험)

python -m tests.test_weekly   (server/ 에서)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

os.environ["JCC_DASHBOARD_PW"] = "env-admin-pw"
os.environ["JCC_DASHBOARD_USER"] = "jccops"
for k in ("JCC_ALIGO_KEY", "JCC_ALIGO_USER", "JCC_ALIGO_SENDER"):   # 혹시라도 실제 문자가 나가지 않게
    os.environ.pop(k, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server import app as appmod
from jcc_server import weekly
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def next_monday(ts: float, hour: int) -> float:
    d = datetime.fromtimestamp(ts, weekly.KST)
    mon = (d - timedelta(days=d.weekday())).replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=7)
    return mon.timestamp()


def run():
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    now = time.time()
    st.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "A동", "readings": [
        {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 30, "ok": True, "ts": now}]})
    cid = st.accounts.create_customer("평택 2공장")
    other = st.accounts.create_customer("빈 고객사")
    st.accounts.assign_panel("p1", cid)
    mon10 = next_monday(now, 10)          # 다음 주 월요일 10시 → '지난주'에 지금이 들어간다

    print("=== 주간 안전 요약 ===")
    r = weekly.build(st, cid, mon10)
    check("조용한 주: '이상 없었습니다'·가동률", r and r["quiet"] and "이상 없었습니다" in r["text"] and "감시 가동률 100%" in r["text"], r and r["text"])
    check("문구 머리: 고객사·기간", r["text"].startswith("[JCC GUARD] 평택 2공장 주간 안전 요약 ("), r["text"].split("\n")[0])
    check("판넬 없는 고객사는 None", weekly.build(st, other, mon10) is None)
    st.raise_alarm("ccm-1", "cabinet_temp", "alarm", "함내 온도 61C — 위험(55 초과)")
    r = weekly.build(st, cid, mon10)
    check("경보가 있으면 건수를 그대로", not r["quiet"] and r["crit"] == 1 and "위험 경보 1건" in r["text"], r["text"])

    sent = []
    send = lambda text, nums: sent.append((text, list(nums)))    # noqa: E731
    check("꺼져 있으면 안 보냄", weekly.generate_due(st, mon10, send) == 0 and not sent)
    st.accounts.set_weekly_notify(cid, True)
    check("켜도 알림 번호가 없으면 안 보냄", weekly.generate_due(st, mon10 + 60, send) == 0 and not sent)
    st.accounts.set_receivers(cid, ["010-1111-2222"])
    nxt = next_monday(mon10, 10)
    check("월요일 9시 전엔 안 보냄", weekly.generate_due(st, nxt - 3 * 3600, send) == 0 and not sent)
    check("월요일 오전: 한 통", weekly.generate_due(st, nxt, send) == 1 and len(sent) == 1 and sent[0][1] == ["01011112222"], str(sent))
    check("같은 주에 다시 안 보냄", weekly.generate_due(st, nxt + 3600, send) == 0 and len(sent) == 1)
    check("화요일엔 안 보냄", weekly.generate_due(st, nxt + 86400 + 3600, send) == 0)
    check("다음 주 월요일엔 또", weekly.generate_due(st, nxt + 7 * 86400, send) == 1 and len(sent) == 2)
    check("발송 기록이 남음", any(e["etype"] == "weekly" for e in st.events_since(0, None, 50)))
    wk3 = nxt + 14 * 86400
    check("발송 실패면 표시 안 하고 다음 점검에 다시", weekly.generate_due(st, wk3, lambda t, n: {"ok": False}) == 0
          and weekly.generate_due(st, wk3 + 3600, send) == 1, str(len(sent)))
    wk4 = nxt + 21 * 86400
    check("문자 설정이 없으면 '발송'으로 세지 않음", weekly.generate_due(st, wk4, lambda t, n: {"skipped": True}) == 0
          and any("문자 설정 없음" in e["detail"] for e in st.events_since(0, None, 50)))
    st.raise_alarm("ccm-1", "", "silent", "CCM 응답 없음")
    r = weekly.build(st, cid, mon10)
    check("감시 장치 끊김(침묵)은 '위험 경보'로 세지 않음", r["crit"] == 1, str(r["crit"]))

    # 장문 제목: 주간 요약이 '경보' 제목으로 가면 안 된다 — 실제 전송 함수를 가짜로 바꿔 필드만 본다(네트워크 없음)
    from jcc_server import notify
    got = {}
    real_post = notify._post_form
    notify._post_form = lambda url, fields, timeout=8: (got.update(fields), {"result_code": "1"})[1]
    try:
        assert notify._post_form is not real_post
        os.environ.update({"JCC_ALIGO_KEY": "test-key", "JCC_ALIGO_USER": "test-user", "JCC_ALIGO_SENDER": "0200000000"})
        weekly.generate_due(st, nxt + 28 * 86400)
    finally:
        notify._post_form = real_post
        for k in ("JCC_ALIGO_KEY", "JCC_ALIGO_USER", "JCC_ALIGO_SENDER"):
            os.environ.pop(k, None)
    check("주간 요약 장문 제목은 '경보'가 아님", got.get("msg_type") == "LMS" and got.get("title") == "JCC GUARD 주간 안전 요약", str(got.get("title")))

    print("--- 운영자 화면 ---")
    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    import http.cookiejar

    def client():
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def call(path, body=None):
            req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                with op.open(req, timeout=10) as res:
                    return res.status, json.loads(res.read() or b"{}")
            except urllib.error.HTTPError as e:
                return e.code, {}
        return call
    try:
        adm = client()
        adm("/api/login", {"user": "jccops", "password": "env-admin-pw"})
        s, j = adm("/api/admin/weekly_notify", {"customer_id": cid, "on": False})
        acc = adm("/api/admin/accounts")[1]
        check("운영자: 주간 요약 끄기", s == 200 and not next(c for c in acc["customers"] if c["id"] == cid)["weekly_notify"])
        s, j = adm(f"/api/admin/weekly_preview?customer_id={cid}")
        check("운영자: 미리 보기(보내지 않음)", s == 200 and "주간 안전 요약" in j.get("text", ""), str(j)[:120])
        check("미리 보기: 잘못된 고객사 400", adm("/api/admin/weekly_preview?customer_id=x")[0] == 400)
        check("로그인 안 하면 미리 보기 못 봄", client()("/api/admin/weekly_preview?customer_id=1")[0] in (401, 403))
    finally:
        srv.shutdown()
        st.close()
        os.unlink(db)
    print("\n" + ("✅ 전체 통과" if not _fails else f"❌ 실패 {len(_fails)}건: {_fails}"))
    return 0 if not _fails else 1


if __name__ == "__main__":
    sys.exit(run())
