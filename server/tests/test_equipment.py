"""판넬 설비·냉각 용량 — IEC 60890 겉면적, 필요 냉각 = 발열 − 겉면 방열, 설치 용량과 비교, 저장·화면 경로.

python -m tests.test_equipment   (server/ 에서)
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

os.environ["JCC_DASHBOARD_PW"] = "env-admin-pw"
os.environ["JCC_DASHBOARD_USER"] = "jccops"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server import app as appmod
from jcc_server import equipment as E
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def spec(**kw):
    s = {"width": 800, "height": 2000, "depth": 600, "bays": 1, "mount": "free", "material": "steel", "target_c": 35, "ambient_c": 40,
         "heat": [{"name": "인버터", "loss_w": 300, "qty": 4}], "equipment": [{"kind": "aircon", "capacity_w": 1000, "qty": 1, "position": "side"}]}
    s.update(kw)
    return E._clean(s)


def run():
    print("=== 겉면적(IEC 60890) ===")
    check("단독·사방 트임 800×2000×600 = 5.71㎡", abs(E.area(spec()) - 5.712) < 0.01, str(E.area(spec())))
    check("단독·벽 붙임 = 5.07㎡", abs(E.area(spec(mount="wall")) - 5.072) < 0.01, str(E.area(spec(mount="wall"))))
    a3 = E.area(spec(bays=3))
    end = 1.4 * 0.6 * (2.0 + 0.8) + 1.8 * 0.8 * 2.0
    mid = 1.8 * 0.8 * 2.0 + 1.4 * 0.8 * 0.6 + 0.6 * 2.0
    check("3면 열반 = 끝 2 + 가운데 1", abs(a3 - (2 * end + mid)) < 0.01, f"{a3:.2f}")
    check("크기 모르면 None", E.area(E._clean({"width": 800})) is None)

    print("\n=== 냉각 용량 ===")
    c = E.calc(spec())
    check("발열 1200W, 바깥 40℃ > 목표 35℃면 겉면으로 열이 들어옴", c["heat_w"] == 1200 and c["passive_w"] < 0, str(c["passive_w"]))
    check("필요 = 발열 − 겉면 방열", c["need_w"] == round(1200 - 5.5 * 5.712 * (35 - 40)), str(c["need_w"]))
    check("설치 1000W < 필요 → 부족·모자란 양(여유 10%, 100W 올림)", c["status"] == "short" and c["deficit_w"] == 500, str(c.get("deficit_w")))
    check("제안 문장에 숫자", "1357 W" in E.advice(spec(), c) and "500 W" in E.advice(spec(), c), E.advice(spec(), c))
    check("열반 3면이면 배치 권고", "끝단 측면" in E.advice(spec(bays=3), E.calc(spec(bays=3))))
    ok = E.calc(spec(equipment=[{"kind": "aircon", "capacity_w": 1000, "qty": 2}]))
    check("2000W면 충분", ok["status"] == "ok", ok["status"])
    tight = E.calc(spec(equipment=[{"kind": "aircon", "capacity_w": 1400, "qty": 1}]))
    check("필요 이상이지만 여유 10% 안 → 빠듯", tight["status"] == "tight", tight["status"])
    pas = E.calc(spec(ambient_c=25, heat=[{"loss_w": 200, "qty": 1}], equipment=[]))
    check("바깥이 차고 발열 작으면 공조 없이도", pas["status"] == "passive", str(pas))
    hx = E.calc(spec(equipment=[{"kind": "heat_exchanger", "capacity_w": 3000, "qty": 1}]))
    check("바깥이 더 더우면 열교환기는 못 식힌다", hx["installed_w"] == 0, str(hx["installed_w"]))
    fan = E.calc(spec(ambient_c=25, equipment=[{"kind": "fan_filter", "airflow_m3h": 310, "qty": 1}]))
    check("팬필터 = 풍량 × ΔT / 3.1", fan["installed_w"] == round(310 * 10 / 3.1), str(fan["installed_w"]))
    un = E.calc(E._clean({"width": 800, "height": 2000, "depth": 600}))
    check("발열·온도 모르면 '정보 부족'", un["status"] == "unknown" and "발열 기기" in un["missing"], str(un["missing"]))
    check("이상한 값은 버림", E._clean({"width": -5, "bays": 99, "material": "x", "equipment": [{"kind": "rocket"}]}) ["width"] is None)

    print("\n=== 저장·화면 경로 ===")
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    now = time.time()
    for dev, pnl in (("ccm-1", "p1"), ("ccm-2", "p2")):
        st.ingest({"device_id": dev, "panel": pnl, "panel_name": pnl.upper(), "readings": [
            {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 30, "ok": True, "ts": now}]})
    cid = st.accounts.create_customer("평택")
    st.accounts.assign_panel("p1", cid)
    _, tpw = st.accounts.create_user("view.e", "viewer", cid)
    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    import http.cookiejar

    def client():
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def call(path, body=None):
            req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                         data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            try:
                with op.open(req, timeout=10) as r:
                    return r.status, json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                return e.code, {}
        return call
    try:
        adm = client()
        adm("/api/login", {"user": "jccops", "password": "env-admin-pw"})
        s, j = adm("/api/admin/cooling_calc", {"spec": spec()})
        check("운영자: 저장 전 계산 미리 보기", s == 200 and j["view"]["calc"]["status"] == "short", str(s))
        raw = dict(spec(), equipment=[{"kind": "aircon", "name": "측면 에어컨", "capacity_w": 1000, "qty": 1, "position": "side",
                                        "filter_days": 30, "last_service": now - 40 * 86400}])
        s, j = adm("/api/admin/panel_spec", {"panel": "p1", "spec": raw})
        check("운영자: 판넬 설비 저장", s == 200 and j["view"]["summary"] == "에어컨 1대", str(j)[:120])
        check("없는 판넬은 저장 안 됨", adm("/api/admin/panel_spec", {"panel": "zz", "spec": raw})[0] == 400)
        s, j = adm("/api/admin/panel_spec?panel=p1")
        check("운영자: 다시 불러오기", s == 200 and j["spec"]["equipment"][0]["name"] == "측면 에어컨" and j["spec"]["updated_by"] == "jccops")
        check("필터 청소 기한 지남 표시", j["view"]["units"][0]["service_due"] is True)
        s, _ = adm("/api/admin/serviced", {"panel": "p1", "index": 0})
        j = adm("/api/admin/panel_spec?panel=p1")[1]
        check("필터 청소 함 → 다음 청소일이 앞으로", s == 200 and j["view"]["units"][0]["service_due"] is False
              and j["view"]["units"][0]["next_service"] > now + 29 * 86400)
        check("없는 장치 번호 400", adm("/api/admin/serviced", {"panel": "p1", "index": 5})[0] == 400)
        cu = client()
        cu("/api/login", {"user": "view.e", "password": tpw})
        cu("/api/me/password", {"old": tpw, "new": "view-e-pw-2026"})
        cu("/api/login", {"user": "view.e", "password": "view-e-pw-2026"})
        s, j = cu("/api/guard/panel?panel=p1")
        check("고객: 판넬 상세에 공조 장치·용량 판단", s == 200 and j["equipment"] and j["equipment"]["calc"]["status"] == "short", str(s))
        check("고객: 설비 저장 못 함", cu("/api/admin/panel_spec", {"panel": "p1", "spec": raw})[0] == 403)
        check("고객: 운영자 설비 조회 못 함", cu("/api/admin/panel_spec?panel=p1")[0] == 403)
        check("등록 없는 판넬은 equipment 없음", adm("/api/guard/panel?panel=p2")[1].get("equipment") is None)
    finally:
        srv.shutdown()
        st.close()
        os.unlink(db)
    print("\n" + ("✅ 전체 통과" if not _fails else f"❌ 실패 {len(_fails)}건: {_fails}"))
    return 0 if not _fails else 1


if __name__ == "__main__":
    sys.exit(run())
