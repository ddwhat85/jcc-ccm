"""경보 원인 묶기(incidents.group_alarms) 시나리오 테스트.

python -m tests.test_incidents   (server/ 에서)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server.incidents import group_alarms, incident_of

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


SENSORS = {("ccm-2661", "h2_lel"): "h2", ("ccm-2661", "voc_ppm"): "voc", ("ccm-2661", "main_current"): "current",
           ("ccm-2662", "cabinet_temp"): "temp", ("ccm-2662", "cabinet_humidity"): "humidity",
           ("ccm-2662", "door_distance"): "door", ("ccm-2663", "cabinet_temp"): "temp",
           ("ccm-2663", "cabinet_humidity"): "humidity", ("ccm-2663", "vibration"): "vibration",
           ("ccm-2664", "smoke"): "smoke", ("ccm-2664", "ncontact_temp"): "temp",
           ("ccm-9001", "h2_lel"): "h2"}
PANELS = {"ccm-2661": "p1", "ccm-2662": "p1", "ccm-2663": "p1", "ccm-2664": "p1", "ccm-9001": "p2"}
T = 1_790_000_000.0
_n = [0]


def A(dev, key, kind, sev, dt, acked=False):
    _n[0] += 1
    return {"id": _n[0], "device_id": dev, "sensor_key": key, "kind": kind, "severity": sev,
            "raised_at": T + dt, "acked_at": (T + dt + 5) if acked else None, "detail": f"{key or dev} {kind}"}


def run():
    print("=== 경보 원인 묶기 ===")
    # 1) 화재 징조 한 건이 낳은 경보 7개 + 무관한 도어 경고
    fire = A("ccm-2661", "h2_lel", "fire", "crit", 0)
    kids = [A("ccm-2661", "h2_lel", "alarm_warn", "warn", -120), A("ccm-2661", "voc_ppm", "alarm_warn", "warn", -60),
            A("ccm-2661", "h2_lel", "anomaly", "warn", -300), A("ccm-2661", "voc_ppm", "drift", "warn", 30),
            A("ccm-2662", "cabinet_temp", "anomaly", "warn", 40), A("ccm-2664", "smoke", "alarm", "crit", 90)]
    door = A("ccm-2662", "door_distance", "alarm_warn", "warn", 10)
    smoke_fault = A("ccm-2664", "smoke", "silent", "crit", 50)           # 연기 센서 무응답 = 센서 문제
    other_panel = A("ccm-9001", "h2_lel", "alarm_warn", "warn", 20)
    inc = group_alarms([fire, door, other_panel, smoke_fault] + kids, SENSORS, PANELS)
    fi = incident_of(inc, fire["id"])
    check("화재 징조 1건으로 7개 경보 묶음", fi["cause"] == "fire" and fi["count"] == 7 and fi["primary"] == fire["id"],
          f'{fi["count"]}개 · {fi["title"]}')
    check("연기 센서 고장(무응답)은 화재에 묶지 않음", smoke_fault["id"] not in fi["alarms"])
    check("무관한 도어 경고는 따로", incident_of(inc, door["id"])["cause"] == "sensor")
    check("다른 판넬 가스 경보는 섞이지 않음", incident_of(inc, other_panel["id"]) is not fi)
    check("모든 경보는 정확히 한 사건에", sorted(i for x in inc for i in x["alarms"]) ==
          sorted(a["id"] for a in [fire, door, other_panel, smoke_fault] + kids))
    check("위험 사건이 맨 앞", inc[0]["severity"] == "crit" and inc[0]["cause"] == "fire")

    # 2) 화재 징조 뒤 그 CCM이 끊김 — 화재는 통신 문제에 삼켜지면 안 된다
    f2 = A("ccm-2661", "h2_lel", "fire", "crit", 0)
    sil = A("ccm-2661", "", "silent", "crit", 120)
    inc = group_alarms([f2, sil], SENSORS, PANELS)
    check("화재 징조는 CCM 두절에 삼켜지지 않음", incident_of(inc, f2["id"])["cause"] == "fire"
          and incident_of(inc, sil["id"])["cause"] == "ccm")

    # 2-1) 화재 중 벤트 작동 실패 — 원인은 화재, 문제는 벤트: 화재 사건 안에 묶인다
    f3 = A("ccm-2661", "h2_lel", "fire", "crit", 0)
    vf = A("ccm-2661", "vent", "actuator_fault", "crit", 45)
    hf = A("ccm-2661", "heater", "actuator_fault", "crit", 50)
    vf_other = A("ccm-9001", "vent", "actuator_fault", "crit", 60)
    inc = group_alarms([f3, vf, hf, vf_other], SENSORS, PANELS)
    check("화재 중 벤트 작동 실패는 화재 사건에", vf["id"] in incident_of(inc, f3["id"])["alarms"])
    check("히터 작동 실패는 화재에 묶지 않음", incident_of(inc, hf["id"])["cause"] == "sensor")
    check("화재 없는 판넬의 벤트 실패는 따로(위험)", incident_of(inc, vf_other["id"])["cause"] == "sensor"
          and incident_of(inc, vf_other["id"])["severity"] == "crit")

    # 3) CCM 두절: 이후 생긴 그 CCM 경보는 결과, 두절 한참 전 경보는 별개
    sil3 = A("ccm-2663", "", "silent", "crit", 0)
    after = A("ccm-2663", "vibration", "stuck", "warn", 200)
    s_sil = A("ccm-2663", "cabinet_temp", "silent", "crit", -30)          # 판정 지연 직전 센서 침묵
    before = A("ccm-2663", "vibration", "alarm_warn", "warn", -900)       # 15분 전 진동 경고
    inc = group_alarms([sil3, after, s_sil, before], SENSORS, PANELS)
    ci = incident_of(inc, sil3["id"])
    check("CCM 두절 이후·직전 경보는 묶음", after["id"] in ci["alarms"] and s_sil["id"] in ci["alarms"],
          f'{ci["count"]}개')
    check("두절 한참 전 경보는 별개", before["id"] not in ci["alarms"])

    # 4) 접점 발열: 같은 접점온도 센서의 경보만(함내온도 이상은 아님)
    ct = A("ccm-2664", "ncontact_temp", "contact", "crit", 0)
    c1 = A("ccm-2664", "ncontact_temp", "alarm_warn", "warn", 30)
    c2 = A("ccm-2664", "ncontact_temp", "anomaly", "warn", -40)
    amb = A("ccm-2663", "cabinet_temp", "anomaly", "warn", 10)
    inc = group_alarms([ct, c1, c2, amb], SENSORS, PANELS)
    check("접점 발열 묶음(접점온도 경보만)", incident_of(inc, ct["id"])["count"] == 3
          and amb["id"] not in incident_of(inc, ct["id"])["alarms"])

    # 5) 결로: 같은 판넬 두 CCM의 습도 경보·포화 고착까지
    dw = A("ccm-2662", "cabinet_humidity", "dew", "warn", 0)
    h1 = A("ccm-2662", "cabinet_humidity", "alarm", "crit", 20)
    h2 = A("ccm-2663", "cabinet_humidity", "stuck", "warn", 60)
    inc = group_alarms([dw, h1, h2], SENSORS, PANELS)
    di = incident_of(inc, dw["id"])
    check("결로 묶음 + 사건 심각도는 구성원 최댓값", di["count"] == 3 and di["severity"] == "crit", di["severity"])

    # 6) 같은 센서의 여러 경보 → 가장 심각한 것이 대표, 모두 확인되면 확인됨
    v1 = A("ccm-2663", "vibration", "drift", "warn", 0, acked=True)
    v2 = A("ccm-2663", "vibration", "alarm", "crit", 30, acked=True)
    inc = group_alarms([v1, v2], SENSORS, PANELS)
    check("같은 센서 묶음 · 대표=가장 심각", len(inc) == 1 and inc[0]["primary"] == v2["id"] and inc[0]["acked"])

    check("빈 목록", group_alarms([], SENSORS, PANELS) == [])

    # 7) 알림은 사건 단위: 화재 징조로 알렸으면 이어진 수소 위험선 경보는 다시 알리지 않음
    from jcc_server.incidents import should_notify
    f7 = A("ccm-2661", "h2_lel", "fire", "crit", 0)
    h7 = A("ccm-2661", "h2_lel", "alarm", "crit", 300)
    d7 = A("ccm-2663", "vibration", "alarm", "crit", 310)
    inc = group_alarms([f7, h7, d7], SENSORS, PANELS)
    check("첫 경보는 알림", should_notify(f7["id"], inc, set()))
    check("같은 사건 후속 위험 경보는 알림 생략", not should_notify(h7["id"], inc, {f7["id"]}))
    check("다른 사건은 그대로 알림", should_notify(d7["id"], inc, {f7["id"]}))
    # 8) 고착 판정: 접점(열연기)은 늘 0이 정상 → 고착 경보 없음, 진짜 멈춘 온도센서는 고착
    import tempfile
    import time as _t
    from jcc_server.storage import Storage
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    try:
        st.set_discovery({"panel": "p1", "ccms": [{"device_id": "ccm-x", "sensors": [
            {"key": "smoke", "name": "열연기", "kind": "smoke"}, {"key": "t", "name": "온도", "kind": "temp"}]}]})
        base = _t.time() - 20
        for i in range(12):
            st.ingest({"device_id": "ccm-x", "readings": [
                {"key": "smoke", "value": 0, "ok": True, "ts": base + i},
                {"key": "t", "value": 25.0, "ok": True, "ts": base + i}]})
        st.health_scan()
        stuck = {a["sensor_key"] for a in st.list_active_alarms() if a["kind"] == "stuck"}
        check("열연기(늘 0)는 고착 오경보 없음 · 멈춘 온도는 고착", stuck == {"t"}, str(stuck))
    finally:
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
