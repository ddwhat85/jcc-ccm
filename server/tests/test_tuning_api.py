"""튜닝 설정 API — 로그인·조회·평가·적용 관문·버전 충돌·되돌리기·재시작 복원·CCM 전달.

python -m tests.test_tuning_api   (server/ 에서)
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

os.environ["JCC_DASHBOARD_PW"] = "tune-pw"          # app import 전에(모듈 로드 때 읽는다)
os.environ["JCC_DASHBOARD_USER"] = "ops"
os.environ.pop("JCC_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server import app as appmod
from jcc_server import params as P
from jcc_server.predict import Predictor
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def with_(**kv):
    d = P.defaults()
    for k, v in kv.items():
        d[k.replace("__", ".")] = v
    return d


def run():
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    st.predictor = Predictor()
    st.load_active_tuning()
    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(path, body=None, use_jar=True, timeout=30):
        req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        o = opener if use_jar else urllib.request.build_opener()
        try:
            with o.open(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"raw": raw}

    def events(kind):
        return [e["detail"] for e in st.list_events(limit=200) if e["etype"] == kind]

    try:
        print("=== 튜닝 API ===")
        check("미로그인 조회 401", call("/api/tuning/config", use_jar=False)[0] == 401)
        check("미로그인 적용 401", call("/api/tuning/apply", {"params": P.defaults()}, use_jar=False)[0] == 401)
        s, _ = call("/api/login", {"user": "ops", "password": "tune-pw"})
        check("로그인", s == 200)

        s, j = call("/api/tuning/params")
        check("손잡이 목록 38개", s == 200 and len(j["params"]) == 38 and j["relations"])
        s, j = call("/api/tuning/scenarios")
        check("시나리오 11편", s == 200 and len(j["scenarios"]) == 11)
        s, j = call("/api/tuning/config")
        check("처음엔 v0 = 코드 기본값", j["active"]["version"] == 0 and j["active"]["params"] == P.defaults()
              and j["history"] == [])

        s, j = call("/api/tuning/evaluate", {"params": P.defaults()})
        check("평가: 기본값 사고 6/6", s == 200 and j["scorecard"]["caught"] == 6 and j["scorecard"]["missed"] == 0)
        check("평가는 저장하지 않음", call("/api/tuning/config")[1]["active"]["version"] == 0)

        # 적용 전 벤트를 연 상태로 둔다 — 기준만 바뀌어야지 열린 벤트가 닫히면 안 된다
        st.predictor.set_actuator("p1", "vent", "open")
        st.predictor.set_actuator("p1", "vent", "auto")
        vent_before = st.predictor.actuators("p1")["vent"]["open"]
        new = with_(contact__res_warn=5.5)
        s, j = call("/api/tuning/apply", {"params": new, "base_version": 0, "note": "주의 조금 둔하게"})
        check("정상 적용 → v1", s == 200 and j.get("version") == 1, str(j)[:200])
        check("서버 예지 엔진에 즉시 반영", st.predictor.contact_cfg.res_warn == 5.5)
        check("판넬 상태 유지(열린 벤트 그대로)", vent_before and st.predictor.actuators("p1")["vent"]["open"])
        check("적용 이벤트(작성자·메모)", any("v1 적용" in d and "ops" in d and "주의 조금" in d
                                     for d in events("tuning_apply")))

        s, j = call("/api/tuning/apply", {"params": with_(fire__open_fri=71.0), "base_version": 1})
        check("절대 한계 밖 → 400", s == 400 and "한계" in j["error"], j.get("error", ""))
        s, j = call("/api/tuning/apply", {"params": with_(fire__close_fri=28.0, fire__watch_fri=25.0),
                                          "base_version": 1})
        check("관계 위반 → 400", s == 400 and "<" in j["error"], j.get("error", ""))
        s, j = call("/api/tuning/apply", {"params": dict(P.defaults(), bogus=1), "base_version": 1})
        check("모르는 키 → 400", s == 400)

        s, j = call("/api/tuning/apply", {"params": with_(contact__res_alarm=20.0), "base_version": 1})
        check("사고를 놓치는 설정 → 409(관문)", s == 409 and j["scorecard"]["missed"] >= 1, j.get("error", ""))
        check("거부 이벤트에 놓친 시나리오", any("contact_jump" in d for d in events("tuning_reject")))
        check("거부된 설정은 반영 안 됨", st.predictor.contact_cfg.res_alarm == 11.0
              and call("/api/tuning/config")[1]["active"]["version"] == 1)

        s, j = call("/api/tuning/apply", {"params": with_(contact__res_warn=6.0), "base_version": 0})
        check("옛 버전 기준 적용 → 409 충돌 + 지금 설정 돌려줌", s == 409 and j["active"]["version"] == 1)

        s, j = call("/api/tuning/rollback", {"version": "default", "base_version": 1})
        check("기본값으로 되돌리기 → v2", s == 200 and j["version"] == 2)
        act = call("/api/tuning/config")[1]
        check("v2 = 기본값·되돌림 기록", act["active"]["params"] == P.defaults() and act["active"]["kind"] == "rollback"
              and [h["version"] for h in act["history"]] == [2, 1] and "params" not in act["history"][0])
        check("되돌림 이벤트", any("v2 되돌림" in d for d in events("tuning_rollback")))
        check("없는 버전 되돌리기 → 400", call("/api/tuning/rollback", {"version": 99})[0] == 400)
        check("버전에 불리언 → 400", call("/api/tuning/rollback", {"version": True})[0] == 400)

        print("\n=== CCM 전달 ===")
        def tele(tuning):
            body = {"device_id": "ccm-t", "ts": time.time(), "readings": []}
            if tuning is not None:
                body["tuning"] = tuning
            return call("/v1/telemetry", body, use_jar=False)[1]
        r = tele({"version": 0})
        check("옛 버전 CCM에 활성 설정 전달", r.get("tuning", {}).get("version") == 2
              and r["tuning"]["params"] == P.defaults())
        check("최신 버전이면 안 보냄", "tuning" not in tele({"version": 2, "last": {"version": 2, "status": "ok"}}))
        check("적용 보고 → 이벤트", any("v2 적용" in d for d in events("tuning_edge")))
        r = tele(None)
        check("설정 보고가 없는 CCM(옛 펌웨어)에도 활성 설정을 실어 보냄(모르면 무시)", r.get("tuning", {}).get("version") == 2)
        rej = {"version": 1, "last": {"version": 2, "status": "rejected", "error": "벤트 기준 한계 밖"}}
        check("거부한 버전은 다시 안 보냄", "tuning" not in tele(rej))
        check("거부 보고 → 이벤트(사유)", any("거부" in d and "한계 밖" in d for d in events("tuning_edge")))
        edge = call("/api/tuning/config")[1]["edge"]
        check("CCM별 상태 조회", edge.get("ccm-t", {}).get("status") == "rejected")
        r = tele("garbage")
        check("형식 틀린 보고에도 수집은 정상 + 상태는 안 바뀜", r.get("ok") is True
              and call("/api/tuning/config")[1]["edge"]["ccm-t"]["status"] == "rejected")

        print("\n=== 재시작 ===")
        st2 = Storage(db)
        st2.predictor = Predictor()
        ver = st2.load_active_tuning()
        check("재시작 후 활성 버전 복원", ver == 2 and st2.predictor.params() == P.defaults())
        st2.close()
        s, j = call("/api/tuning/apply", {"params": with_(dew__rh_high=82.0), "base_version": 2})
        st3 = Storage(db)
        st3.predictor = Predictor()
        st3.load_active_tuning()
        check("재시작 후 바뀐 기준도 엔진에 실림", s == 200 and st3.predictor.dew_cfg.rh_high == 82.0)
        st3.close()
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
