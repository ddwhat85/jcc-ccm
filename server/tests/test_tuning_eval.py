"""튜닝 시나리오·평가기 테스트 — 결정성·형식·기본값 기준선(CI)·관문이 잡아야 할 나쁜 설정.

pytest 없이 단독 실행: python -m tests.test_tuning_eval  (server/ 에서)
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server import params as P
from jcc_server import scenarios as S
from jcc_server import tuning_eval as E

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run():
    print("=== 시나리오 ===")
    check("11종·순서 고정", S.NAMES == ("cable_overheat", "co_char", "contact_loosen", "contact_jump",
                                    "monsoon_dew", "night_chill", "spray", "welding", "motor_start",
                                    "summer_noon", "sensor_glitch"))
    a = json.dumps(S._BUILDERS["spray"](), sort_keys=True)
    b = json.dumps(S._BUILDERS["spray"](), sort_keys=True)
    check("같은 시나리오는 늘 같은 시계열(시드 고정)", a == b)
    kinds = {n: S.build(n)["kind"] for n in S.NAMES}
    check("사고 6·함정 5", sum(k == "accident" for k in kinds.values()) == 6
          and sum(k == "trap" for k in kinds.values()) == 5)
    bad = []
    for n in S.NAMES:
        s = S.build(n)
        ok = (s["algo"] in ("fire", "contact", "dew") and s["roles"] and s["t0"] < s["t_end"]
              and (s["kind"] == "trap" or (s["t_start"] < s["t_ref"] <= s["t_end"] and s["expect"]["need"]))
              and (s["kind"] == "accident" or s["expect"]["forbid"])
              and all(pts == sorted(pts) for pts in s["roles"].values()))
        if not ok:
            bad.append(n)
    check("형식(역할·시각 정렬·기준 시각)", not bad, str(bad))
    check("JSON 직렬화 가능", len(json.dumps(S.export_all())) > 1000)

    print("\n=== 기본값 기준선 (CI) ===")
    t0 = time.time()
    sc = E.scorecard(P.defaults())
    dt = time.time() - t0
    check("사고 6/6 잡음", sc["caught"] == 6 and sc["missed"] == 0,
          " · ".join(f"{n}:{v['why']}" for n, v in sc["per"].items() if v["kind"] == "accident" and not v["ok"]))
    fa = sorted(n for n, v in sc["per"].items() if v["kind"] == "trap" and not v["ok"])
    check("오경보는 알려진 한계(용접)뿐", fa == ["welding"], str(fa))
    check("알려진 한계 표시", "알려진 한계" in sc["per"]["welding"]["known"])
    check("재시작 직후 헛 '주의' 없음(센서 튐 함정)", sc["per"]["sensor_glitch"]["watch_hits"] == 0,
          str(sc["per"]["sensor_glitch"]["watch_hits"]))
    check("가장 아슬아슬한 선행시간 ≥ 3분", (sc["lead_worst"] or 0) >= 3.0, str(sc["lead_worst"]))
    check("전체 평가 10초 이내", dt < 10.0, f"{dt:.1f}초")

    print("\n=== 관문이 막아야 할 설정 ===")
    blunt = dict(P.defaults(), **{"fire.open_fri": 70.0, "fire.w_h2": 0.2, "fire.w_voc": 0.2,
                                 "fire.pair_boost": 0.0, "fire.h2.alarm": 50.0, "fire.voc.alarm": 2000.0,
                                 "fire.h2.rise_alarm": 40.0, "fire.voc.rise_alarm": 1500.0,
                                 "fire.z_lo": 8.0, "fire.z_hi": 10.0})
    check("무딘 설정도 형식은 유효(한계 안)", P.validate(blunt) is None, str(P.validate(blunt)))
    j = E.judge(S.build("cable_overheat"), E.replay(S.build("cable_overheat"), blunt))
    check("한계 안 가장 무딘 설정도 케이블 과열은 잡음(두 가스 동반 규칙 — 손잡이로 못 끔)", j["ok"], j["why"])
    late = dict(P.defaults(), **{"contact.res_alarm": 20.0})
    j = E.judge(S.build("contact_jump"), E.replay(S.build("contact_jump"), late))
    check("접점 위험 20°C → 볼트 이완 놓침", not j["ok"], j["why"])

    print("\n=== 판정 규칙 ===")
    s = S.build("co_char")
    early = {"events": [{"t": s["t0"] + 60, "what": "fire_watch"}], "trace": []}
    check("사건 시작 전 탐지는 잡음이 아님", not E.judge(s, early)["ok"])
    on_time = {"events": [{"t": s["t_ref"] - 120, "what": "fire_watch"}], "trace": []}
    j = E.judge(s, on_time)
    check("기준 2분 전 탐지 → 잡음, 선행 2.0분", j["ok"] and j["lead_min"] == 2.0, str(j))

    print()
    if _fails:
        print(f"❌ {len(_fails)} FAIL: {_fails}")
        return 1
    print("✅ 전체 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
