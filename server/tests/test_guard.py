"""고객 화면(JCC GUARD) — 안심 지수 감점·상태 문장·판넬 모델·위험 순간·이번 달 장부·고객 범위·경로.

python -m tests.test_guard   (server/ 에서)
"""
from __future__ import annotations

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
os.environ["JCC_SUPPORT_PHONE"] = "02-0000-0000"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from jcc_server import app as appmod
from jcc_server import guard
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def row(name="1번", crit=0, warn=0, on=1, tot=1, fire=None, contact=None, dew=None, life=None, due=None, status="ok", why=None):
    return {"panel": name, "panel_name": name, "status": status, "why": why or [],
            "ccm_online": on, "ccm_total": tot, "alarms": {"crit": crit, "warn": warn, "unacked": crit + warn},
            "predict": {"fire": fire, "contact": contact, "dew": dew, "vent_open": False},
            "life": life, "next_inspection": due}


def unit(now):
    s = guard.score([row()], now)
    check("이상 없으면 100·초록", s["score"] == 100 and s["color"] == "ok" and s["items"] == [])
    s = guard.score([row(life={"label": "필터", "say": "약 7주", "days": 49, "status": "ok"})], now)
    check("관리 항목만 있으면 감점은 해도 초록(상태 문장 '안전'과 맞게)", s["score"] == 97 and s["color"] == "ok", str(s))
    s = guard.score([row(warn=1, dew="warning", status="warn"), row("2번", life={"label": "필터", "say": "약 7주", "days": 49, "status": "ok"})], now)
    check("주의 −5·결로 −3·여유 −3 = 89·호박", s["score"] == 89 and s["color"] == "warn", str(s))
    check("감점 이유에 판넬 이름", any("1번" in i["text"] and i["minus"] == 5 for i in s["items"]))
    s = guard.score([row(crit=1, on=0, tot=2, fire="watch", due=now - 86400, status="crit")], now)
    check("위험 −20·끊김 2대 −20·화재 지켜봄 −5·점검 기한 −5 = 50·빨강", s["score"] == 50 and s["color"] == "crit", str(s))
    s = guard.score([row(crit=6, warn=9)], now)
    check("한 판넬 감점 상한(위험 40·주의 15)", s["score"] == 45, str(s["score"]))
    s = guard.score([row(str(i), crit=3, on=0, tot=3) for i in range(3)], now)
    check("0 아래로 안 내려감", s["score"] == 0)
    check("고정 점검 항목(위험 경보·끊긴 장치·점검 기한)", [c["label"] for c in guard.score([row()], now)["checks"]][:2] == ["위험 경보", "끊긴 감시 장치"])
    check("상태 문장: 안전", guard.state_line([row(), row("2번")])["title"] == "모든 판넬 안전합니다")
    st = guard.state_line([row(status="warn", why=["결로 위험"]), row("2번")])
    check("상태 문장: 지켜보는 중", st["title"] == "판넬 2면 · 1곳 지켜보는 중" and "결로 위험" in st["sub"], str(st))
    st = guard.state_line([row("A동", crit=1, status="crit", why=["화재 징조(FRI 72)"])])
    check("상태 문장: 위험", st["title"].startswith("위험") and "A동" in st["title"], str(st))
    check("상태 문장: 판넬 없음", "아직" in guard.state_line([])["title"])
    check("남은 여유 이름을 고객 말로", guard.life_plain("접점 발열 잔차 → 위험 기준 11°C") == "단자 점검 시기"
          and guard.life_plain("함내 온도 → 경고선 40") == "함내 온도 기준선까지")


def run():
    now = time.time()
    unit(now)
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    for dev, panel, nm in (("ccm-1", "p1", "A동 배터리실"), ("ccm-2", "p2", "B동 수배전")):
        st.ingest({"device_id": dev, "panel": panel, "panel_name": nm, "readings": [
            {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 31.2, "ok": True, "ts": now},
            {"key": "cabinet_humidity", "name": "함내 습도", "unit": "%RH", "kind": "humidity", "value": 78, "ok": True, "ts": now}]})
    cid = st.accounts.create_customer("평택 2공장")
    st.accounts.assign_panel("p1", cid)

    v = guard.guard_view(st, None, "전체")
    names = [p["panel_name"] for p in v["panels"]]
    check("판넬 모델: 온도·습도 값", v["panels"][0]["temp"] == 31.2 and v["panels"][0]["humidity"] == 78, str(v["panels"][0]))
    check("판넬 2면·지수 100", sorted(names) == ["A동 배터리실", "B동 수배전"] and v["index"]["score"] == 100, str(v["index"]))
    check("위험 없으면 incident 없음", v["incident"] is None)
    check("연락처: 환경변수 전화", v["contact"]["phone"] == "02-0000-0000")
    vc = guard.guard_view(st, {"p1"}, "평택 2공장")
    check("고객 범위: 자기 판넬만", [p["panel_name"] for p in vc["panels"]] == ["A동 배터리실"] and vc["site"] == "평택 2공장")

    # 위험 순간: 위험 경보 → 판넬이 스스로 환기(이벤트) → JCC 확인
    st.raise_alarm("ccm-1", "cabinet_temp", "alarm", "함내 온도 61C — 위험(55 초과)")
    st.log_event("ccm-1", "", "vent_open", "화재 징조 → 벤트 자동 개방", source="system")
    for _ in range(450):      # 위험이 오래 이어지면 '열림 유지'가 몇 초마다 수백 줄 쌓인다 — 첫 '자동 개방'이 밀려나면 안 된다
        st.log_event("ccm-1", "", "vent_hold", "극한 위험 유지 — 벤트 개방 유지 (FRI 100)", source="system")
    aid = next(a["id"] for a in st.list_active_alarms())
    st.ack_alarm(aid, "김현장")
    vi = guard.guard_view(st, {"p1"}, "평택 2공장")
    inc = vi["incident"]
    check("위험 순간: 판넬·센서·내용", inc and inc["panel_name"] == "A동 배터리실" and inc["sensor_name"] == "함내 온도", str(inc))
    texts = [s["text"] for s in inc["steps"]]
    check("위험 순간: '열림 유지'가 수백 줄 쌓여도 첫 자동 개방이 남음", any("자동 개방" in t for t in texts), str(texts))
    check("단계: 판넬이 스스로 → JCC 확인", any("스스로" in t for t in texts) and any("김현장" in t for t in texts), str(texts))
    check("위험이면 지수 빨강", vi["index"]["color"] == "crit" and vi["index"]["score"] <= 80)
    check("다른 고객 범위엔 위험 안 보임", guard.guard_view(st, {"p2"}, "x")["incident"] is None)

    m = guard.month_view(st, {"p1"}, time.strftime("%Y-%m", time.localtime(now)))
    check("이번 달: 키 모양", {"hours", "patrols", "precursors", "actions", "remote", "ack_min_avg", "building"} <= set(m), str(m))
    check("이번 달: 스스로 한 조치에 환기 1", m["actions"]["total"] >= 1 and m["actions"]["vent"] >= 1, str(m["actions"]))
    check("이번 달: 순찰 추정은 하루 3회 기준", m["patrols"]["per_day"] == 3)
    d = guard.panel_detail(st, "p1")
    check("판넬 상세: '열림 유지' 수백 줄에도 첫 자동 개방이 보임", any("자동 개방" in t["text"] for t in d["timeline"]), str(d["timeline"][:3]))
    check("없는 판넬은 None", guard.panel_detail(st, "zz") is None)
    check("이번 달: 감시 가동률(끊김 없으면 100%)", m["uptime"] == 100.0 and m["down_min"] == 0, str(m.get("uptime")))

    # 사건 보고서: 감지 → 판넬이 스스로 → JCC 확인 → 정상 회복, 숫자는 실제 기록
    r = guard.incident_report(st, aid)
    kinds = [t["kind"] for t in r["timeline"]]
    check("사건 보고서: 감지·스스로·확인 순서", kinds[0] == "detect" and "self" in kinds and "ack" in kinds, str(kinds))
    check("사건 보고서: 확인까지 걸린 분·감지 때 값", r["ack_min"] is not None and r["first"] and r["first"]["value"] == 31.2, str(r["first"]))
    check("사건 보고서: '열림 유지'는 조치로 안 셈·내부 지수(FRI) 안 보임", r["self_actions"] == 1
          and not any("FRI" in t["text"] or "유지" in t["text"] for t in r["timeline"]), str(r["self_actions"]))
    check("사건 보고서: 진행 중이면 '지켜보고'", r["open"] is True and "지켜보고" in r["summary"], r["summary"])
    st.clear_alarm("ccm-1", "cabinet_temp", "alarm", "alarm_clear", "함내 온도 정상 회복")
    r = guard.incident_report(st, aid)
    check("사건 보고서: 해제되면 회복 시간·정상 회복 줄", r["open"] is False and r["clear_min"] is not None
          and any(t["kind"] == "clear" for t in r["timeline"]) and "정상으로" in r["summary"], r["summary"])
    check("없는 경보는 None", guard.incident_report(st, 99999) is None)
    per = time.strftime("%Y-%m", time.localtime(now))
    li = guard.incidents(st, {"p1"}, per)["items"]
    check("이번 달 사건 목록: 자기 판넬 경보", len(li) == 1 and li[0]["id"] == aid and li[0]["word"] == "위험", str(li))
    check("이번 달 사건 목록: 남의 범위엔 없음", guard.incidents(st, {"p2"}, per)["items"] == [])

    # 알림 문구: 판넬 이름·한국 시간·자세히 링크
    from jcc_server import notify
    os.environ["JCC_PUBLIC_URL"] = "https://jcc.example.test/"
    txt = notify.build_text({"id": 7, "device_id": "ccm-1", "severity": "crit", "detail": "수소 위험",
                             "raised_at": 1790000000, "panel_name": st.panel_label("ccm-1")})
    check("알림 문구: 고객사·판넬 이름이 앞에", txt.startswith("[JCC GUARD] 위험 — 평택 2공장 A동 배터리실"), txt)
    check("알림 문구: 한국 시간·자세히 링크", "09월 21일 23:13" in txt and txt.endswith("자세히 보기: https://jcc.example.test/?alarm=7"), txt)
    os.environ.pop("JCC_PUBLIC_URL")
    check("링크 주소 없으면 링크 줄 없음", "자세히" not in notify.build_text({"id": 7, "device_id": "ccm-1", "severity": "warn"}))
    os.environ["JCC_ALIMTALK_TEXT"] = "[JCC] {고객사} {severity} {panel}"
    try:
        t2 = notify.build_text({"id": 7, "device_id": "ccm-1", "severity": "crit", "panel_name": "1번"})
        check("템플릿에 모르는 치환자가 있어도 알림은 나간다", t2 == "[JCC] {고객사} 위험 1번", t2)
    except Exception as exc:  # noqa: BLE001
        check("템플릿에 모르는 치환자가 있어도 알림은 나간다", False, repr(exc))
    os.environ.pop("JCC_ALIMTALK_TEXT")

    srv = appmod.make_server("127.0.0.1", 0, st)
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
                    raw = r.read()
                    return r.status, (json.loads(raw) if r.headers.get_content_type() == "application/json" else raw)
            except urllib.error.HTTPError as e:
                return e.code, {}
        return call

    try:
        anon = client()
        s, body = anon("/guard")
        check("/guard 화면은 공개", s == 200 and b"JCC GUARD" in body, str(s))
        check("/api/guard 로그인 전 401", anon("/api/guard")[0] == 401)
        s, body = anon("/manifest.webmanifest")
        mf = json.loads(body) if isinstance(body, (bytes, str)) else body
        check("홈 화면 앱: manifest 공개·이름·아이콘", s == 200 and mf["name"] == "JCC GUARD" and len(mf["icons"]) == 3, str(s))
        s, body = anon("/sw.js")
        check("홈 화면 앱: 서비스 워커 공개, /api 는 저장 안 함", s == 200 and b'startsWith("/api/")' in body)
        check("홈 화면 앱: 아이콘", anon("/img/gd-icon-192.png")[0] == 200)
        adm = client()
        adm("/api/login", {"user": "jccops", "password": "env-admin-pw"})
        s, j = adm("/api/guard")
        check("직원: 전체 미리보기", s == 200 and len(j["panels"]) == 2, str(s))
        s, j = adm(f"/api/guard?customer_id={cid}")
        check("직원: 고객사 미리보기", s == 200 and [p["panel"] for p in j["panels"]] == ["p1"] and j["site"] == "평택 2공장")
        _, temp = st.accounts.create_user("view.g", "viewer", cid)
        cu = client()
        cu("/api/login", {"user": "view.g", "password": temp})
        cu("/api/me/password", {"old": temp, "new": "view-g-pw-2026"})
        cu("/api/login", {"user": "view.g", "password": "view-g-pw-2026"})
        s, j = cu("/api/guard?customer_id=999")
        check("고객: customer_id 무시하고 자기 범위", s == 200 and [p["panel"] for p in j["panels"]] == ["p1"])
        check("고객: 남의 판넬 상세 404", cu("/api/guard/panel?panel=p2")[0] == 404)
        check("고객: 자기 판넬 상세 200", cu("/api/guard/panel?panel=p1")[0] == 200)
        check("이번 달 경로", cu("/api/guard/month?period=" + time.strftime("%Y-%m"))[0] == 200)
        s, j = adm("/api/admin/contact", {"customer_id": cid, "engineer": "김현장", "phone": "010-1234-5678"})
        check("직원: 고객사 담당·연락처 저장", s == 200 and j.get("engineer_phone") == "010-1234-5678", str(j))
        check("잘못된 전화번호 400", adm("/api/admin/contact", {"customer_id": cid, "engineer": "x", "phone": "12"})[0] == 400)
        check("고객은 담당 지정 못 함", cu("/api/admin/contact", {"customer_id": cid, "engineer": "x", "phone": ""})[0] == 403)
        s, j = cu("/api/guard")
        check("고객 화면에 이 고객사 담당·번호", j["contact"]["engineer"] == "김현장" and j["contact"]["phone"] == "010-1234-5678"
              and j["contact"]["assigned"] is True, str(j["contact"]))
        al1 = next(a["id"] for a in st.alarms_since(0, {"ccm-1"}, 10))
        s, j = cu(f"/api/guard/alarm?id={al1}")
        check("알림 링크: 자기 판넬 경보 한 건", s == 200 and j["panel_name"] == "A동 배터리실" and j["sensor_name"] == "함내 온도", str(j)[:200])
        st.raise_alarm("ccm-2", "cabinet_temp", "alarm", "남의 판넬")
        al2 = next(a["id"] for a in st.alarms_since(0, {"ccm-2"}, 10))
        check("알림 링크: 남의 판넬 경보 404", cu(f"/api/guard/alarm?id={al2}")[0] == 404)
        check("알림 링크: 잘못된 번호 400", cu("/api/guard/alarm?id=abc")[0] == 400)
        s, j = cu(f"/api/guard/incident?id={al1}")
        check("사건 보고서 경로: 자기 판넬", s == 200 and j["panel_name"] == "A동 배터리실" and j["timeline"], str(s))
        check("사건 보고서 경로: 남의 판넬 404", cu(f"/api/guard/incident?id={al2}")[0] == 404)
        s, j = cu("/api/guard/incidents?period=" + time.strftime("%Y-%m"))
        check("사건 목록 경로: 자기 판넬만", s == 200 and j["items"] and all(i["panel"] == "p1" for i in j["items"]), str(j)[:200])
        check("사건 목록: 잘못된 달 400", cu("/api/guard/incidents?period=x")[0] == 400)
        adm("/api/admin/contact", {"customer_id": cid, "engineer": "", "phone": ""})
        s, j = cu("/api/guard")
        check("비우면 공통 번호로", j["contact"]["phone"] == "02-0000-0000" and j["contact"]["assigned"] is False, str(j["contact"]))
        check("잘못된 달 400", cu("/api/guard/month?period=abc")[0] == 400)
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
