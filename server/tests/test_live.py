"""모니터링 모드 데이터 — 판넬별 지난 24시간 실제 시간별 온도, 최근 활동 피드(고객 범위·잡음 제외).

python -m tests.test_live   (server/ 에서)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server import forecast, guard
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
    now = time.time()
    for dev, panel in (("ccm-1", "p1"), ("ccm-2", "p2")):
        st.ingest({"device_id": dev, "panel": panel, "panel_name": panel + " 판넬", "readings": [
            {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 30, "ok": True, "ts": now}]})
    forecast.ensure(st)
    h0 = int(now // 3600) * 3600
    with st._lock:
        c = st._conn
        for i in range(30):                                   # 30시간치 — 24시간만 나와야
            c.execute("INSERT INTO hourly (device_id, sensor_key, hour, avg, vmin, vmax, n) VALUES ('ccm-1', 'cabinet_temp', ?, ?, 0, 0, 60)",
                      (h0 - i * 3600, 28 + i * 0.1))
        c.executemany("INSERT INTO events (ts, device_id, sensor_key, etype, detail, source) VALUES (?, ?, ?, ?, ?, 'system')",
                      [(now - 60, "ccm-1", "", "vent_open", "화재 징조 (FRI 72) → 벤트 자동 개방"),
                       (now - 50, "ccm-1", "", "vent_hold", "벤트 개방 유지"),                    # 잡음 — 빠져야
                       (now - 40, "ccm-1", "", "setting", "셋팅=1"),                              # 고객에게 뜻 없음 — 빠져야
                       (now - 30, "ccm-2", "", "vent_open", "남의 판넬"),
                       (now - 20, "ccm-1", "", "restart_ok", "재시작 확인 — CCM 프로그램이 다시 켜져 보고했습니다")])
        c.commit()
    v = guard.live(st, {"p1"}, now)
    tr = v["trends"].get("p1", {})
    check("24시간 시간별 실제 온도(25개 이하, 시간순)", tr and 23 <= len(tr["points"]) <= 25 and tr["points"] == sorted(tr["points"]), str(len(tr.get("points", []))))
    check("고객 범위: 남의 판넬 흐름 없음", "p2" not in v["trends"])
    kinds = [f["kind"] for f in v["feed"]]
    check("피드: 최신순·자동 조치·자동 복구, 잡음·남의 것 제외", kinds == ["restart_ok", "vent_open"], str(kinds))
    check("피드 문구는 고객 말로(FRI 뺌)·판넬 이름", "FRI" not in v["feed"][1]["text"] and v["feed"][1]["panel_name"] == "p1 판넬"
          and v["feed"][0]["who"] == "자동 복구", str(v["feed"]))
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
