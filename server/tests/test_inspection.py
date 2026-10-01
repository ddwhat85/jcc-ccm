"""정기 점검 일지 — 볼 곳 뽑기·초안 저장·사진·완료 검사·서명·다음 점검·현장 목록 기한·고객 범위.

python -m tests.test_inspection   (server/ 에서)
"""
from __future__ import annotations

import base64
import http.cookiejar
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

os.environ["JCC_DASHBOARD_PW"] = "env-admin-pw"
os.environ["JCC_DASHBOARD_USER"] = "jccops"
os.environ.pop("JCC_API_KEY", None)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from jcc_server import app as appmod
from jcc_server import inspection as insp
from jcc_server import rul
from jcc_server.fleet import fleet
from jcc_server.storage import Storage

_fails = []
JPG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 2000).decode()
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"1" * 500).decode()


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run():
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    now = time.time()
    st.set_discovery({"panel": "p1", "panel_name": "1번 판넬", "ccms": [{"device_id": "ccm-1", "sensors": [
        {"key": "h2_lel", "name": "수소", "unit": "%LEL", "kind": "h2"},
        {"key": "main_current", "name": "전류", "unit": "A", "kind": "current"}]}]})
    st.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "1번 판넬", "readings": [
        {"key": "h2_lel", "name": "수소", "unit": "%LEL", "value": 0.3, "ok": True, "ts": now}]})
    st.ingest({"device_id": "ccm-2", "panel": "p2", "panel_name": "2번", "readings": [
        {"key": "t", "name": "온도", "unit": "C", "value": 30, "ok": True, "ts": now}]})
    st.raise_alarm("ccm-1", "", "actuator_fault", "벤트 열림 실패")
    st.raise_alarm("ccm-1", "h2_lel", "drift", "수소 드리프트")
    st.raise_alarm("ccm-1", "main_current", "alarm", "전류 위험")
    aid = next(a["id"] for a in st.list_active_alarms() if a["kind"] == "alarm")
    st.ack_alarm(aid, "kim", "용접 작업", "work")
    for d, v in [(rul.day_of(now - (19 - i) * 86400), 4 + 0.3 * i) for i in range(20)]:
        rul.put_day(st, "contact:p1", d, v, 1000)
    cid = st.accounts.create_customer("A")
    st.accounts.assign_panel("p1", cid)
    uid, temp = st.accounts.create_user("kim.a", "manager", cid)

    print("=== 볼 곳 ===")
    f = insp.focus(st, "p1", now)
    texts = [i["text"] for i in f["items"]]
    check("출력 작동 실패가 맨 위", texts and texts[0].startswith("출력 작동 실패"), str(texts[:2]))
    check("드리프트 → 센서 점검", any("값이 한쪽으로 이동" in t for t in texts))
    check("남은 여유(접점) 반영", any("위험 기준까지" in t for t in texts))
    check("가스 센서 기능시험 권장(첫 점검)", any("기능시험" in t for t in texts))
    check("시험·작업으로 표시한 경보는 오경보 줄로", f["false_alarms"] == 1 and not any("전류: 위험" in t for t in texts)
          and any("오경보" in t for t in texts))

    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def client():
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def call(path, body=None, raw=False):
            req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                with op.open(req, timeout=10) as r:
                    data = r.read()
                    return r.status, (data if raw else json.loads(data.decode("utf-8") or "{}"))
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read().decode("utf-8") or "{}")
        return call

    try:
        adm = client()
        adm("/api/login", {"user": "jccops", "password": "env-admin-pw"})
        print("\n=== 시작·저장·사진 ===")
        s, j = adm("/api/inspection?panel=p1")
        check("준비 화면: 볼 곳·초안 없음", s == 200 and j["focus"]["items"] and j["draft"] is None)
        s, d = adm("/api/inspection/start", {"panel": "p1"})
        iid = d["id"]
        groups = {g["group"]: g for g in d["data"]["checklist"]}
        items = {it["key"]: it for g in d["data"]["checklist"] for it in g["items"]}
        check("초안 생성·체크리스트(가스 시험 해당, 히터·팬은 해당 없음)", s == 200 and d["status"] == "draft"
              and items["gas_test"]["applies"] and not items["heater_fan"]["applies"] and "전기" in groups)
        check("다시 시작하면 같은 초안", adm("/api/inspection/start", {"panel": "p1"})[1]["id"] == iid)
        s, d = adm("/api/inspection/save", {"id": iid, "results": {"clean": "ok", "terminal": "fix", "zzz": "ok", "door": "weird"},
                                           "notes": {"terminal": "M6 2개 재조임"}, "summary": "단자 재조임", "interval": 180})
        check("저장(모르는 항목·값은 무시)", s == 200 and d["data"]["results"] == {"clean": "ok", "terminal": "fix"}
              and d["data"]["notes"]["terminal"] == "M6 2개 재조임" and d["data"]["interval"] == 180)
        check("엉뚱한 모양 요청은 400(연결 끊김 아님)",
              adm("/api/inspection/save", {"id": iid, "results": ["ok"]})[0] == 400
              and adm("/api/inspection/save", {"id": iid, "notes": "x"})[0] == 400
              and adm("/api/inspection/photo_delete", {"id": iid, "photo_id": {"a": 1}})[0] == 400)
        s, j = adm("/api/inspection/photo", {"id": iid, "data": JPG, "caption": "단자대"})
        check("사진 올리기", s == 200 and j["id"])
        pid = j["id"]
        bad = "data:image/jpeg;base64," + base64.b64encode(b"GIF89a" + b"0" * 100).decode()
        check("내용이 JPEG 아니면 거부", adm("/api/inspection/photo", {"id": iid, "data": bad})[0] == 400)
        s, raw = adm(f"/api/inspection/photo/{pid}", raw=True)
        check("사진 보기", s == 200 and raw.startswith(b"\xff\xd8"))

        print("\n=== 완료 ===")
        s, j = adm("/api/inspection/complete", {"id": iid, "signer": "박과장", "signature": PNG})
        check("남은 항목 있으면 완료 안 됨", s == 400 and "남았습니다" in j["error"], j.get("error"))
        res = {k: "ok" for k, it in items.items() if it["applies"]}
        res.update({"terminal": "fix", "filter": "bad"})
        adm("/api/inspection/save", {"id": iid, "results": res})
        check("서명자 없으면 거부", adm("/api/inspection/complete", {"id": iid, "signer": " ", "signature": PNG})[0] == 400)
        check("서명 PNG 아니면 거부", adm("/api/inspection/complete", {"id": iid, "signer": "박과장", "signature": JPG})[0] == 400)
        s, d = adm("/api/inspection/complete", {"id": iid, "signer": "박과장", "signature": PNG})
        r = d.get("report") or {}
        check("완료: 결과 '조치 필요'·다음 점검 180일·사진 포함", s == 200 and d["status"] == "done" and r["overall"] == "bad"
              and abs(r["next_due"] - (r["done_at"] + 180 * 86400)) < 5 and len(r["photos"]) == 1 and r["customer"] == "A")
        check("완료 후 수정 불가", adm("/api/inspection/save", {"id": iid, "summary": "x"})[0] == 400
              and adm("/api/inspection/photo", {"id": iid, "data": JPG})[0] == 400)
        ev = [e["detail"] for e in st.list_events(limit=20) if e["etype"] == "inspection"]
        check("기록", any("정기 점검 완료" in e and "박과장" in e for e in ev))
        f2 = insp.focus(st, "p1")
        check("다음 점검의 볼 곳은 이번 점검 이후부터", f2["since"] >= r["done_at"] - 1 and "지난 점검" in f2["since_label"])

        print("\n=== 현장 목록 기한·고객 범위 ===")
        row = next(x for x in fleet(st) if x["panel"] == "p1")
        check("다음 점검 예정일", abs(row["next_inspection"] - r["next_due"]) < 1)
        with st._lock:
            st._conn.execute("UPDATE inspections SET next_due=? WHERE id=?", (now - 3 * 86400, iid))
            st._conn.commit()
        row = next(x for x in fleet(st) if x["panel"] == "p1")
        check("기한 지나면 이유에 표시(위험 판넬에도)", any("정기 점검 기한 3일 지남" in w for w in row["why"]), str(row["why"]))
        adm("/api/inspection/start", {"panel": "p2"})
        m = client()
        m("/api/login", {"user": "kim.a", "password": temp})
        m("/api/me/password", {"old": temp, "new": "manager-pw-2026"})
        m("/api/login", {"user": "kim.a", "password": "manager-pw-2026"})
        s, j = m("/api/inspections")
        check("고객: 자기 판넬의 완료 점검만", s == 200 and [x["id"] for x in j["inspections"]] == [iid], str(j))
        check("고객: 보고서 보기", m(f"/api/inspection/{iid}")[0] == 200)
        check("고객: 사진 보기", m(f"/api/inspection/photo/{pid}", raw=True)[0] == 200)
        check("고객: 쓰기·준비 화면 막힘", m("/api/inspection/start", {"panel": "p1"})[0] == 403 and m("/api/inspection?panel=p1")[0] == 403)
        other = adm("/api/inspections")[1]["inspections"]
        p2_draft = next(x["id"] for x in other if x["panel"] == "p2")
        check("고객: 다른 판넬 점검 404", m(f"/api/inspection/{p2_draft}")[0] == 404)
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
