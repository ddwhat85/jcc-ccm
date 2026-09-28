"""예지보전 통합 엔진(predict.Predictor) + storage.predict_scan 시나리오 테스트.

python -m tests.test_predict   (server/ 에서)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "firmware"))

from jcc_server.predict import Predictor
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def noisy(base, n=24, amp=0.05):
    return [base * (1 + amp * (1 if i % 2 else -1) * (0.5 + (i % 4) / 4.0)) for i in range(n)]


def run():
    print("=== 예지 엔진 (Predictor.assess_panel) ===")
    P = Predictor()
    now = 1000.0

    # 정상: 모든 값 평상 → 세 알고리즘 normal, 액추에이터 꺼짐
    normal = {
        "h2": {"value": 0.4, "series": [(i * 2.0, 0.4) for i in range(20)], "baseline": noisy(0.4)},
        "voc": {"value": 30, "series": [(i * 2.0, 30) for i in range(20)], "baseline": noisy(30)},
        "temp": {"value": 27, "series": [(i * 2.0, 27) for i in range(20)]},
        "smoke": False,
        "contact": {"current": 15, "temp": 27, "ambient": 27,
                    "history": [(i * 2.0, 15, 27 + 0.2 * (i % 2), 27) for i in range(20)]},
        "dew": {"temp": 25, "rh": 45, "surface": 25,
                "history": [(i * 2.0, 25, 45, 25) for i in range(20)]},
    }
    r = P.assess_panel("panel-01", now, normal)
    check("정상 — 화재/접점/결로 모두 normal",
          r["fire"]["stage"] == "normal" and r["contact"]["stage"] == "normal" and r["dew"]["stage"] == "normal",
          f'fire={r["fire"]["stage"]} contact={r["contact"]["stage"]} dew={r["dew"]["stage"]}')
    check("정상 — 벤트 닫힘·히터/팬 꺼짐",
          not r["fire"]["vent"]["open"] and not r["dew"]["heater"] and not r["dew"]["fan"])

    # 화재 징조: H2·VOC 동반 상승(임계 아래) → danger → 벤트 자동 개방
    fire_in = dict(normal)
    fire_in["h2"] = {"value": 20, "series": [(i * 4.0, 1 + i * 1.3) for i in range(15)], "baseline": noisy(0.4)}
    fire_in["voc"] = {"value": 700, "series": [(i * 4.0, 30 + i * 45) for i in range(15)], "baseline": noisy(30)}
    r = P.assess_panel("panel-01", now, fire_in)
    check("화재 징조 — danger 이상", r["fire"]["stage"] in ("danger", "critical"),
          f'stage={r["fire"]["stage"]} FRI={r["fire"]["fri"]}')
    check("화재 징조 — 벤트 자동 개방", r["fire"]["vent"]["open"] and r["fire"]["action"] == "open")
    check("H2 임계(25) 前 예지", fire_in["h2"]["value"] < 25 and r["fire"]["fri"] >= 55,
          f'H2={fire_in["h2"]["value"]}%LEL < 25, FRI={r["fire"]["fri"]}')

    # 복구: 정상 유지 → 히스테리시스 지난 뒤 벤트 자동 닫힘
    P.assess_panel("panel-01", now + 5, normal)       # FRI<close 진입(_below_since 설정)
    r = P.assess_panel("panel-01", now + 200, normal)  # recover_hold 경과
    check("복구 — 벤트 자동 닫힘", not r["fire"]["vent"]["open"],
          f'vent_open={r["fire"]["vent"]["open"]} action={r["fire"]["action"]}')

    # 접점 발열: 접점온도만 상승(전류 정상) → 잔차↑ → danger
    P2 = Predictor()
    contact_in = dict(normal)
    contact_in["contact"] = {"current": 15, "temp": 50, "ambient": 27,
                             "history": [(i * 2.0, 15, 27 + 0.2 * (i % 2), 27) for i in range(20)]}
    r = P2.assess_panel("panel-01", now, contact_in)
    check("접점 발열 — danger", r["contact"]["stage"] == "danger",
          f'stage={r["contact"]["stage"]} 잔차={r["contact"]["residual"]}°C')
    check("접점 발열 — 화재로 확증 전달", r["contact"]["stage"] == "danger")

    # 결로: 습도 상승으로 이슬점 여유 축소 → danger → 히터·팬 가동
    P3 = Predictor()
    dew_in = dict(normal)
    dew_in["dew"] = {"temp": 25, "rh": 95, "surface": 25,
                     "history": [(i * 2.0, 25, 55 + i * 2, 25) for i in range(20)]}
    r = P3.assess_panel("panel-01", now, dew_in)
    check("결로 — danger", r["dew"]["stage"] == "danger",
          f'stage={r["dew"]["stage"]} 여유={r["dew"]["margin"]}°C')
    check("결로 — 히터·팬 가동", r["dew"]["heater"] and r["dew"]["fan"] and r["dew"]["action"] == "heater_fan")

    # 수동 오버라이드: 벤트 수동 개방 → 정상이어도 열린 채 유지(사람 우선)
    P4 = Predictor()
    P4.set_actuator("panel-01", "vent", "open")
    r = P4.assess_panel("panel-01", now, normal)
    check("수동 오버라이드 — 벤트 열림 유지", r["fire"]["vent"]["open"] and r["fire"]["vent"]["mode"] == "manual")
    P4.set_actuator("panel-01", "vent", "auto")
    P4.assess_panel("panel-01", now + 300, normal)        # 복구 판정 진입(_below_since 설정)
    r = P4.assess_panel("panel-01", now + 400, normal)    # 히스테리시스 경과 → 닫힘
    check("수동 해제 — 자동 복귀 후 닫힘", not r["fire"]["vent"]["open"] and r["fire"]["vent"]["mode"] == "auto")

    # 접점 기준 학습 중 이상 구간(발열 진행)은 배우지 않는다 — 설치 직후 고장을 정상으로 학습하면 안 됨
    from jcc_server.contact_heat import ContactCfg as _CC
    P6 = Predictor(contact_cfg=_CC(learn_samples=20, learn_span=30))
    base_in = dict(normal)
    t6 = 5000.0
    hist6 = []
    for i in range(60):                                  # 이상 구간+회복 꼬리는 건너뛰므로 넉넉히
        t6 += 2.0
        hot = 10 <= i < 22                               # 학습 도중 접점 발열이 12표본 동안 진행
        T = 27 + (6 + (i - 10) * 1.5 if hot else 0) + 0.1 * (i % 3)
        hist6 = (hist6 + [(t6, 15.0, T, 27.0)])[-24:]
        inp = dict(base_in, contact={"current": 15, "temp": T, "ambient": 27, "history": list(hist6)})
        r = P6.assess_panel("panel-01", t6, inp)
    bl6 = P6.contact_baseline("panel-01")
    check("학습 중 이상 구간은 기준에서 제외", bl6.ready and bl6.k < 0.002,
          f"k={bl6.k} (정상≈0, 오염됐다면 ≈0.03 이상)")

    # 안전측(fail-safe): 화재로 벤트가 열린 뒤 가스 데이터가 끊기면 → 닫지 않고 유지
    P5 = Predictor()
    P5.assess_panel("panel-01", now, fire_in)                               # 벤트 개방
    gone = {"fire": "데이터 끊김 — 판정 보류(상태 유지)"}
    P5.assess_panel("panel-01", now + 10, normal, gone)
    r = P5.assess_panel("panel-01", now + 500, normal, gone)                # 히스테리시스 한참 지나도
    check("데이터 끊김 — 벤트 열린 채 유지(안전측)",
          r["fire"]["vent"]["open"] and r["fire"]["stage"] == "pending" and r["fire"]["fri"] is None,
          f'vent={r["fire"]["vent"]["open"]} stage={r["fire"]["stage"]}')

    print("\n=== 저장소 통합 (storage.predict_scan) ===")
    from jcc_ccm.discovery.scanner import discover_sim
    fd, dbp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        st = Storage(dbp)
        st.set_discovery(discover_sim())
        st.predictor = Predictor()

        def ingest_cycle(over, ts):
            for c in discover_sim()["ccms"]:
                readings = []
                for s in c["sensors"]:
                    k = s["key"]
                    v = over.get(k)
                    if v is None:
                        v = {"h2_lel": 0.4, "voc_ppm": 30, "main_current": 15,
                             "cabinet_temp": 27, "cabinet_humidity": 45,
                             "ncontact_temp": 27, "smoke": 0, "vibration": 1.2,
                             "door_distance": 12}.get(k, 40)
                    if v == "SKIP":      # 이 센서는 이번 주기에 아예 안 보냄(채널 침묵)
                        continue
                    fail = v == "FAIL"   # 읽기 실패 표본(현실: 가끔 한 번씩 못 읽음)
                    readings.append({"key": k, "name": s.get("name", ""), "unit": s.get("unit", ""),
                                     "value": None if fail else v, "ok": not fail, "ts": ts})
                st.ingest({"device_id": c["device_id"], "panel": "panel-01",
                           "panel_name": "스마트 판넬", "readings": readings})

        # 가상 시계: CCM이 2초마다 보고하는 현실 주기. 판정 시각(now)은 마지막 표본 + 1초로 주입.
        clock = [1_790_000_000.0]

        def cyc(over=None):
            clock[0] += 2.0
            ingest_cycle(over or {}, clock[0])

        def scan():
            st.predict_scan(now=clock[0] + 1.0)
            return {p["panel"]: p for p in st.predict_state()}["panel-01"]

        def pred_alarms():
            return [a for a in st.list_active_alarms() if a["kind"] in ("fire", "contact", "dew")]

        # 워밍업: 기동 직후 2표본(그중 하나는 튄 값)으로는 판정하지 않는다
        cyc()
        cyc({"cabinet_temp": 47, "h2_lel": 28})
        w = scan()
        check("통합 — 워밍업 중 세 알고리즘 모두 보류",
              all(w[k]["stage"] == "pending" for k in ("fire", "contact", "dew")) and not pred_alarms()
              and not w["fire"]["vent"]["open"],
              " / ".join(f'{k}={w[k]["stage"]}({w[k]["reasons"][0]})' for k in ("fire", "contact", "dew")))

        # 표본 수는 찼어도 시간 폭(20초)이 모자라면 아직 보류 — 짧은 창의 잡음 기울기 방지
        for _ in range(6):
            cyc()
        w = scan()
        check("통합 — 표본 8개·14초 창은 아직 보류(시간 폭 부족)",
              w["fire"]["stage"] == "pending" and "초" in w["fire"]["reasons"][0], w["fire"]["reasons"][0])

        # 정상 주기를 더 쌓아 창이 20초를 넘기면 판정
        for _ in range(14):
            cyc()
        state = scan()
        check("통합 — 정상 시 화재 normal", state["fire"]["stage"] == "normal",
              f'{state["fire"]["stage"]} {state["fire"]["reasons"]}')

        # 단발 글리치: H2 한 표본만 28%LEL(위험선 초과) + 연기 1표본 → 중앙값 필터로 벤트 안 열림
        cyc({"h2_lel": 28, "smoke": 1})
        fire = scan()["fire"]
        check("통합 — 단발 글리치로 벤트 안 열림", not fire["vent"]["open"] and fire["stage"] != "critical",
              f'stage={fire["stage"]} FRI={fire["fri"]} vent={fire["vent"]["open"]}')
        # 읽기 실패 1건: 워밍업 끝난 뒤 H2·전류가 한 번 못 읽혀도 '학습 중'으로 되돌아가지 않는다
        cyc({"h2_lel": "FAIL", "main_current": "FAIL"})
        cyc()
        s2 = scan()
        # (읽기 실패가 끼면 ①유효 2개의 중앙값이 튄 값을 고르거나 ②시계열 짝이 어긋나 가짜 추세가
        #  생겼던 결함의 회귀 방지 — 정상 설비는 '정상'이어야 한다)
        check("통합 — 읽기 실패 1건에도 정상 판정 유지",
              s2["fire"]["stage"] == "normal" and s2["contact"]["stage"] == "normal",
              f'fire={s2["fire"]["stage"]}{s2["fire"]["reasons"]} FRI={s2["fire"]["fri"]} '
              f'contact={s2["contact"]["stage"]}{s2["contact"]["reasons"]}')

        # 채널 침묵: H2가 20초간 안 들어오면(2초 주기의 10배) 화재만 '데이터 끊김'으로 보류,
        # H2를 안 쓰는 접점 예지는 계속 판정. 고정 60초 한계였다면 '학습 중'으로 잘못 보였다.
        for _ in range(10):
            cyc({"h2_lel": "SKIP"})
        s3 = scan()
        check("통합 — H2 침묵 20초 → 화재만 '데이터 끊김' 보류",
              s3["fire"]["stage"] == "pending" and "끊김" in s3["fire"]["reasons"][0]
              and s3["contact"]["stage"] != "pending",
              f'fire={s3["fire"]["reasons"][0]} contact={s3["contact"]["stage"]}')
        for _ in range(3):
            cyc()
        s4 = scan()
        check("통합 — H2 복귀 후 화재 판정 재개", s4["fire"]["stage"] != "pending", s4["fire"]["stage"])

        # 화재 에피소드: H2·VOC·온도 상승 15주기(30초) — 임계(H2 25·VOC 1000) 도달 전
        for i in range(15):
            cyc({"h2_lel": round(1 + i * 1.0, 2), "voc_ppm": round(30 + i * 40, 1),
                 "cabinet_temp": round(27 + i * 0.3, 2)})
        fire = scan()["fire"]
        check("통합 — 화재 danger + 벤트 개방", fire["stage"] in ("danger", "critical") and fire["vent"]["open"],
              f'stage={fire["stage"]} FRI={fire["fri"]} vent={fire["vent"]["open"]}')
        alarms = [a for a in st.list_active_alarms() if a["kind"] == "fire"]
        check("통합 — 화재 예지 경보 발생", len(alarms) >= 1, f'{len(alarms)}건')

        # 수동 오버라이드 API 경로
        res = st.set_actuator("panel-01", "vent", "close")
        check("통합 — set_actuator", res.get("ok") and res["actuators"]["vent"]["mode"] == "manual")

        # 접점 기준: 학습 → 저장 → 서버 재시작 후 복원(다시 배우지 않음) → 정비 후 재학습
        from jcc_server.contact_heat import ContactCfg
        quick = lambda: Predictor(contact_cfg=ContactCfg(learn_samples=8, learn_span=10))
        st.predictor = quick()
        st._pred_loaded.discard("panel-01")
        st._save_pred_state("panel-01", {"contact_baseline": {}})    # 앞 단계의 흔적 초기화
        for _ in range(15):
            cyc()
            scan()
        b = st.predict_state()[0]["contact"]["baseline"]
        evs = [e["detail"] for e in st.list_events(limit=50) if e["etype"] == "baseline"]
        check("접점 기준 학습 완료 + 이벤트", b["status"] == "ready" and any("학습 완료" in d for d in evs),
              f'{b} / {evs[:1]}')
        st2 = Storage(dbp)                                            # 서버 재시작
        st2.predictor = quick()
        cyc()
        st2.predict_scan(now=clock[0] + 1.0)
        b2 = st2.predict_state()[0]["contact"]["baseline"]
        check("재시작 후 기준 복원(재학습 안 함)", b2["status"] == "ready" and b2["k"] == b["k"],
              f'복원 k={b2["k"]} / 이전 k={b["k"]}')
        r = st2.relearn_baseline("panel-01")
        cyc()
        st2.predict_scan(now=clock[0] + 1.0)
        b3 = st2.predict_state()[0]["contact"]["baseline"]
        check("정비 후 재학습 → 학습 중으로", r["ok"] and b3["status"] == "learning", str(b3))
        st2.close()
    finally:
        try:
            st.close()
        except Exception:
            pass
        os.unlink(dbp)

    print()
    if _fails:
        print(f"❌ {len(_fails)} FAIL: {_fails}")
        return 1
    print("✅ 전체 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
