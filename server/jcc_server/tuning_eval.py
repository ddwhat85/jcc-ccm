"""튜닝 평가기 — 시나리오를 실제 판정 경로(build_panel_inputs → Predictor)로 재생해 성적표를 낸다.

서버 적용 관문의 정본이다. 브라우저 미리보기(predict-core.js)는 같은 규칙을 미러하고,
패리티 테스트가 결과 일치를 확인한다.

사건(what): fire_watch · fire_danger · vent_open · contact_watch · contact_danger ·
            dew_watch · dew_danger · fan · heater  — 각각 '꺼짐→켜짐' 순간만 기록
"""
from __future__ import annotations

import bisect

from . import params as P
from . import scenarios as S
from .contact_heat import ContactCfg
from .dewpoint import DewCfg
from .fire_risk import FireConfig
from .inputs import build_panel_inputs
from .predict import Predictor

_WATCH = ("fire_watch", "contact_watch", "dew_watch")


def _predictor(params: dict) -> Predictor:
    fc, cc, dc = FireConfig(), ContactCfg(), DewCfg()
    P.apply_to(fc, cc, dc, params)
    pr = Predictor(fc, cc, dc)
    pr.autovent = True
    return pr


def _flags(res: dict, pending: dict) -> dict:
    """이번 틱에 켜져 있는 상태들(보류된 알고리즘은 직전 상태 유지를 위해 None)."""
    f, c, d = res["fire"], res["contact"], res["dew"]
    out = {}
    if "fire" not in pending:
        out["fire_watch"] = f["stage"] in ("watch", "danger", "critical")
        out["fire_danger"] = f["stage"] in ("danger", "critical")
    out["vent_open"] = bool(f["vent"]["open"])
    if "contact" not in pending:
        out["contact_watch"] = c["stage"] in ("watch", "danger")
        out["contact_danger"] = c["stage"] == "danger"
    if "dew" not in pending:
        out["dew_watch"] = d["stage"] in ("watch", "danger")
        out["dew_danger"] = d["stage"] == "danger"
    out["fan"] = bool(d["fan"])
    out["heater"] = bool(d["heater"])
    return out


def _metric(scn: dict, res: dict):
    a = scn["algo"]
    if a == "fire":
        return res["fire"]["fri"]
    if a == "contact":
        return res["contact"]["residual"]
    return res["dew"]["margin"]


def replay(scn: dict, params: dict) -> dict:
    """시나리오 한 편을 재생. {"events":[{"t","what"}], "trace":[[t, 지표|None]]}"""
    pr = _predictor(params)
    roles = {r: ("sim", r) for r in scn["roles"]}
    data = scn["roles"]
    tss = {r: [p[0] for p in pts] for r, pts in data.items()}
    now = [0.0]

    def series(_dev, key, n):
        i = bisect.bisect_right(tss[key], now[0])
        return [(p[0], p[1]) for p in data[key][max(0, i - n):i]]

    events, trace, state = [], [], {}
    step = scn["step"]
    k = 0
    while True:
        t = scn["t0"] + k * step
        if t > scn["t_end"]:
            break
        k += 1
        now[0] = t
        asm = build_panel_inputs(roles, series, t)
        if not asm["inputs"]:
            trace.append([t, None])
            continue
        res = pr.assess_panel("sim", t, asm["inputs"], asm["pending"])
        for what, on in _flags(res, asm["pending"]).items():
            if on and not state.get(what):
                events.append({"t": t, "what": what})
            state[what] = on
        trace.append([t, _metric(scn, res)])
    return {"events": events, "trace": trace}


def judge(scn: dict, run: dict) -> dict:
    """사고: 사건 시작(t_start) 뒤 need 중 하나가 t_ref 전에 → 잡음(시작 전 탐지는 잡음이 아니라 우연).
    함정: forbid 중 하나라도 → 오경보."""
    ev = run["events"]
    watch_hits = sum(1 for e in ev if e["what"] in _WATCH)
    exp = scn["expect"]
    if scn["kind"] == "accident":
        hits = [e["t"] for e in ev if e["what"] in exp["need"] and e["t"] >= scn["t_start"]]
        first = min(hits) if hits else None
        ok = first is not None and first < scn["t_ref"]
        lead = round((scn["t_ref"] - first) / 60.0, 1) if ok else None
        if ok:
            why = f"{scn['ref_label']} {lead}분 전에 잡음"
        elif first is None:
            why = "끝까지 못 잡음"
        else:
            why = f"{scn['ref_label']} {round((first - scn['t_ref']) / 60.0, 1)}분 뒤에야 잡음"
        return {"ok": ok, "first": first, "lead_min": lead, "why": why, "watch_hits": watch_hits}
    bad = [e for e in ev if e["what"] in exp["forbid"]]
    first = bad[0]["t"] if bad else None
    why = "오경보 없음" if not bad else f"오경보: {bad[0]['what']} ({round((first - scn['t0']) / 60.0, 1)}분)"
    return {"ok": not bad, "first": first, "lead_min": None, "why": why, "watch_hits": watch_hits}


def scorecard(params: dict, names=None) -> dict:
    """모든 시나리오 성적표."""
    per = {}
    for n in (names or S.NAMES):
        scn = S.build(n)
        per[n] = judge(scn, replay(scn, params))
        per[n]["kind"] = scn["kind"]
        per[n]["known"] = scn.get("known", "")
    acc = [v for v in per.values() if v["kind"] == "accident"]
    traps = [v for v in per.values() if v["kind"] == "trap"]
    leads = [v["lead_min"] for v in acc if v["ok"]]      # 척도가 분~일로 섞여 평균은 무의미 → 가장 아슬아슬한 값
    return {
        "caught": sum(1 for v in acc if v["ok"]),
        "missed": sum(1 for v in acc if not v["ok"]),
        "false_alarms": sum(1 for v in traps if not v["ok"]),
        "watch_in_traps": sum(v["watch_hits"] for v in traps),
        "lead_worst": min(leads) if leads else None,
        "per": per,
    }
