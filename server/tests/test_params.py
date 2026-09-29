"""예지 파라미터 레지스트리(params) 테스트 — 기본값 일치·범위·거부 사유·적용.

pytest 없이 단독 실행: python -m tests.test_params  (server/ 에서)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server import params as P
from jcc_server.contact_heat import ContactCfg
from jcc_server.dewpoint import DewCfg
from jcc_server.fire_risk import FireConfig

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
    print("=== 파라미터 레지스트리 ===")
    d = P.defaults()
    check("기본값 = 코드 설정 기본값", d == P.from_cfgs(FireConfig(), ContactCfg(), DewCfg()))
    check("손잡이 38개", len(P.PARAMS) == 38, str(len(P.PARAMS)))
    check("키 중복 없음", len({p["key"] for p in P.PARAMS}) == len(P.PARAMS))
    check("기본값 통과", P.validate(d) is None, str(P.validate(d)))
    bad_range = [p["key"] for p in P.PARAMS
                 if not (p["hard"][0] <= p["ui"][0] <= p["default"] <= p["ui"][1] <= p["hard"][1])]
    check("기본값 ⊂ 화면 범위 ⊂ 절대 한계", not bad_range, str(bad_range))
    check("관계 규칙이 모두 레지스트리 키", all(a in d and b in d for a, _, b in P.RELATIONS))

    miss = dict(d)
    miss.pop("fire.open_fri")
    for name, prm, needle in (
        ("빠진 키", miss, "빠짐"),
        ("모르는 키", dict(d, **{"fire.bogus": 1.0}), "모르는"),
        ("숫자 아님", with_(fire__open_fri="abc"), "숫자"),
        ("NaN", with_(fire__open_fri=float("nan")), "숫자"),
        ("불리언은 숫자 아님", with_(fire__open_fri=True), "숫자"),
        ("벤트 기준 한계 밖(71)", with_(fire__open_fri=71.0), "한계"),
        ("닫힘 ≥ 주의", with_(fire__close_fri=28.0, fire__watch_fri=25.0), "<"),
        ("H2 주의 ≥ 경보", with_(**{"fire__h2__warn": 30.0}), "<"),
        ("결로 위험 ≥ 주의", with_(dew__margin_alarm=3.5), "<"),
    ):
        why = P.validate(prm)
        check(f"거부: {name}", isinstance(why, str) and needle in why, str(why))
    check("dict 아님 거부", isinstance(P.validate([1, 2]), str))

    fc, cc, dc = FireConfig(), ContactCfg(), DewCfg()
    new = with_(fire__open_fri=60.0, **{"fire__h2__alarm": 30.0}, contact__res_warn=6.0,
                dew__rh_high=85.0, fire__w_co=0.25)
    P.apply_to(fc, cc, dc, new)
    check("apply_to 제자리 반영", P.from_cfgs(fc, cc, dc) == new)
    check("학습 파라미터는 손잡이 아님", cc.learn_samples == 300 and "contact.learn_samples" not in d)

    view = P.registry_view()
    check("registry_view JSON 형식", isinstance(view["params"], list) and view["relations"]
          and set(view["params"][0]) >= {"key", "group", "label", "unit", "default", "ui", "hard", "desc"})

    print()
    if _fails:
        print(f"❌ {len(_fails)} FAIL: {_fails}")
        return 1
    print("✅ 전체 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
