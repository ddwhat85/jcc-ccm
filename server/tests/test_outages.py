"""감시 끊김 기록 — 부팅 감지, 원인 가르기(통신·전원·멈춤·JCC 작업·원격 재시작·센서), 장부·고객 범위.

python -m tests.test_outages   (server/ 에서)
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
from jcc_server import guard
from jcc_server import outages as O
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def pay(dev, boot=None, ts=None, panel="p1"):
    p = {"device_id": dev, "panel": panel, "panel_name": panel.upper(),
         "readings": [{"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "value": 30, "ok": True, "ts": ts or time.time()}]}
    if boot is not None:
        p["boot_ts"] = boot
    return p


def run():
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    now = time.time()

    print("=== 부팅 감지 ===")
    b1 = now - 86400 * 3
    st.ingest(pay("ccm-1", b1))
    st.ingest(pay("ccm-1", b1))
    check("처음 본 부팅은 기록만(재시작 기록 아님)", not any(e["etype"] == "ccm_boot" for e in st.events_since(0, None, 50)))
    b2 = now - 3600
    st.ingest(pay("ccm-1", b2))
    check("부팅 시각이 바뀌면 '다시 켜짐' 기록", any(e["etype"] == "ccm_boot" for e in st.events_since(0, None, 50)))
    st.ingest(pay("ccm-1", b1, ts=now - 7200))       # 큐에 남아 있던 예전 묶음(예전 부팅)이 늦게 도착
    with st._lock:
        bt = st._conn.execute("SELECT boot_ts FROM devices WHERE device_id='ccm-1'").fetchone()["boot_ts"]
        n = st._conn.execute("SELECT COUNT(*) AS n FROM ccm_boots WHERE device_id='ccm-1'").fetchone()["n"]
    check("늦게 온 예전 묶음은 부팅으로 치지 않음", bt == b2 and n == 2, f"{bt} {n}")
    st.ingest(pay("ccm-1", "엉터리"))
    check("이상한 부팅 값은 무시", True)

    print("\n=== 원인 가르기 ===")
    def alarm(dev, key, raised, cleared, secs=90):
        with st._lock:
            cur = st._conn.execute("INSERT INTO alarms (device_id, sensor_key, kind, detail, severity, raised_at, cleared_at) "
                                   "VALUES (?, ?, 'silent', ?, 'crit', ?, ?)",
                                   (dev, key, f"CCM {dev} 응답 없음 — {secs}초 침묵" if not key else f"{key} 90초째 데이터 없음", raised, cleared))
            st._conn.commit()
            return {"id": cur.lastrowid, "device_id": dev, "sensor_key": key, "detail": f"CCM {dev} 응답 없음 — {secs}초 침묵",
                    "raised_at": raised, "cleared_at": cleared}

    def readings(dev, t0, t1, step=60):
        with st._lock:
            st._conn.executemany("INSERT INTO readings (device_id, sensor_key, name, unit, value, ok, ts) VALUES (?, 'cabinet_temp', 'x', 'C', 30, 1, ?)",
                                 [(dev, t) for t in range(int(t0), int(t1), step)])
            st._conn.commit()

    def ev(dev, et, ts):
        with st._lock:
            st._conn.execute("INSERT INTO events (ts, device_id, sensor_key, etype, detail, source) VALUES (?, ?, '', ?, '', 'user')", (ts, dev, et))
            st._conn.commit()

    T = now - 86400 * 2
    for dev in ("n1", "n2", "n3", "n4", "n5"):
        st.ingest(pay(dev, panel="p1"))
    a = alarm("n1", "", T, T + 3600)
    readings("n1", T - 90, T + 3600)                  # 끊긴 동안 값이 나중에 다 올라옴
    check("값이 나중에 올라옴 → 통신만 끊김(판넬이 지킴)", O.classify(st, a, now)[0] == "network", str(O.classify(st, a, now)))
    a = alarm("n2", "", T, T + 3600)
    with st._lock:
        st._conn.execute("INSERT INTO ccm_boots (device_id, boot_ts, seen_at) VALUES ('n2', ?, ?)", (T + 3500, T + 3600))
        st._conn.commit()
    check("값 없고 그 사이 새로 켜짐 → 전원 꺼짐·재시작", O.classify(st, a, now)[0] == "power")
    a = alarm("n3", "", T, T + 3600)
    check("값도 부팅도 없음 → 멈춤(기록 없음)", O.classify(st, a, now)[0] == "stop")
    a = alarm("n4", "", T, T + 600)
    ev("n4", "restart", T - 10)
    check("JCC가 재시작 명령 → JCC 작업", O.classify(st, a, now)[0] == "operator")
    a = alarm("n5", "", T, T + 900)
    ev("n5", "heal_restart", T + 400)
    check("서버가 원격 재시작으로 살림 → 원격 재시작", O.classify(st, a, now)[0] == "heal")
    a = alarm("n1", "cabinet_temp", T + 7200, T + 9000)
    check("센서 하나만 → 센서 데이터 없음", O.classify(st, a, now)[0] == "sensor")
    a = alarm("n3", "", now - 300, None)
    check("아직 안 돌아옴 → 지금 끊겨 있음", O.classify(st, a, now)[0] == "ongoing")

    print("\n=== 원인 굳히기·목록·장부 ===")
    items = O.list_range(st, None, T - 3600, now + 1, now)
    kinds = sorted(o["kind"] for o in items)
    check("목록에 원인·보호 여부·분", {"network", "power", "stop", "operator", "heal", "sensor", "ongoing"} <= set(kinds)
          and all("minutes" in o and "say" in o for o in items), str(kinds))
    net = next(o for o in items if o["kind"] == "network")
    check("통신 끊김은 '판넬이 스스로 지킴'", net["protected"] is True and "스스로 지켰" in net["say"] and net["minutes"] >= 60, str(net["minutes"]))
    pw = next(o for o in items if o["kind"] == "power")
    check("전원 꺼짐은 '보호 없음'", pw["protected"] is False)
    with st._lock:
        saved = st._conn.execute("SELECT COUNT(*) AS n FROM outages").fetchone()["n"]
    check("회복 10분 지난 것은 원인을 굳혀 저장(지금 끊긴 건 저장 안 함)", saved == 6, str(saved))
    with st._lock:   # 굳힌 뒤 원본 값이 지워져도(보존 기간) 원인은 그대로
        st._conn.execute("DELETE FROM readings WHERE device_id='n1'")
        st._conn.commit()
    st.ingest(dict(pay("n3"), backlog=500, ts=now))       # 아직 밀린 묶음 500건을 보내는 중
    b = alarm("n3", "", T + 20000, T + 23600)
    O.list_range(st, None, T - 3600, now + 1, now)
    with st._lock:
        pend = st._conn.execute("SELECT 1 FROM outages WHERE alarm_id=?", (b["id"],)).fetchone()
    check("CCM이 밀린 묶음을 다 보내기 전엔 원인을 확정하지 않음", pend is None)
    st.ingest(dict(pay("n3"), backlog=999, ts=now - 5000))  # 늦게 온 예전 묶음의 숫자로 덮지 않음
    st.ingest(dict(pay("n3"), backlog=0, ts=now + 1))
    O.list_range(st, None, T - 3600, now + 2, now + 2)
    with st._lock:
        done = st._conn.execute("SELECT 1 FROM outages WHERE alarm_id=?", (b["id"],)).fetchone()
    check("다 보낸 뒤(밀린 수 0)에 확정", done is not None)
    check("원인은 굳힌 대로(원본 값이 지워져도)", next(o for o in O.list_range(st, None, T - 3600, now + 1, now) if o["device_id"] == "n1" and not o["sensor"])["kind"] == "network")
    um = O.unprotected_minutes(st, {"n1", "n2", "n3"}, T - 3600, now, now)
    check("원인별 끊긴 분(경보 전 대기 90초 포함, 센서 끊김은 빼고)", um.get("network") == 62 and um.get("power") == 62 and um.get("stop", 0) >= 60, str(um))

    print("\n=== 고객 범위 ===")
    st.ingest(pay("x9", panel="p9"))
    alarm("x9", "", T, T + 600)
    cid = st.accounts.create_customer("평택")
    st.accounts.assign_panel("p1", cid)
    v = guard.outages_view(st, {"p1"}, time.strftime("%Y-%m", time.localtime(T)), now)
    check("자기 판넬 끊김만", v["items"] and all(o["device_id"] != "x9" for o in v["items"]))
    _, tpw = st.accounts.create_user("view.o", "viewer", cid)
    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    import http.cookiejar
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(path, body=None):
        req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                     data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with op.open(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, {}
    try:
        call("/api/login", {"user": "view.o", "password": tpw})
        call("/api/me/password", {"old": tpw, "new": "view-o-pw-2026"})
        call("/api/login", {"user": "view.o", "password": "view-o-pw-2026"})
        s, j = call("/api/guard/outages?period=" + time.strftime("%Y-%m", time.localtime(T)))
        check("고객 경로: 자기 판넬 끊김", s == 200 and j["items"] and all(o["device_id"] != "x9" for o in j["items"]), str(s))
        check("잘못된 달 400", call("/api/guard/outages?period=x")[0] == 400)
    finally:
        srv.shutdown()
        st.close()
        os.unlink(db)
    print("\n" + ("✅ 전체 통과" if not _fails else f"❌ 실패 {len(_fails)}건: {_fails}"))
    return 0 if not _fails else 1


if __name__ == "__main__":
    sys.exit(run())
