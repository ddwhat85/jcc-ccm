"""결로(이슬점) 예지 — 온·습도로 이슬점을 구하고 표면온도 여유가 좁아지면 결로 위험.

물리: 공기가 이슬점(Td) 이하로 식은 표면에 닿으면 수증기가 응결(결로)한다. 판넬 안
결로는 누전·트래킹·부식·기판 손상의 직접 원인이다. 함내 온·습도로 Td를 계산하고,
(표면온도 − Td) 여유가 좁아지면 히터/팬을 선제 가동해 결로를 예방한다.
표면온도 미제공 시 이슬점 깊이(DPD = 기온 − Td, 포화까지의 여유)로 대체한다.

입력: 기온 T(°C), 상대습도 RH(%), 표면온도(선택, °C·비접촉/최냉점).
순수 함수 — 엣지·서버 공유.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass

from .fire_risk import slope_per_min

# Magnus 계수 (물, 0~60°C 범위 표준)
_A = 17.62
_B = 243.12


def dew_point(temp_c, rh):
    """Magnus 식 이슬점(°C). rh는 %. 입력 이상 시 None."""
    if temp_c is None or rh is None or rh <= 0:
        return None
    rh = max(1.0, min(100.0, rh))
    g = math.log(rh / 100.0) + _A * temp_c / (_B + temp_c)
    return _B * g / (_A - g)


@dataclass
class DewCfg:
    margin_warn: float = 3.0    # 표면 여유(°C) 주의
    margin_alarm: float = 1.0   # 표면 여유(°C) 위험(결로 임박)
    rh_high: float = 80.0       # 이 습도(%) 이상은 여유와 무관히 주의
    fall_warn: float = 0.4      # 여유가 분당 이만큼 좁아지면 추세 주의(예지)

    @classmethod
    def from_env(cls) -> "DewCfg":
        c = cls()
        c.margin_warn = float(os.environ.get("JCC_DEW_MARGIN_WARN") or c.margin_warn)
        c.margin_alarm = float(os.environ.get("JCC_DEW_MARGIN_ALARM") or c.margin_alarm)
        return c


@dataclass
class DewResult:
    stage: str          # normal | watch | danger
    dew_point: float    # 이슬점(°C)
    margin: float       # 여유 = 표면(또는 기온) − 이슬점
    margin_slope: float # 여유 분당 변화(음수=좁아지는 중)
    rh: float
    action: str         # none | fan | heater_fan
    reasons: list


def assess_dewpoint(temp, rh, surface_temp=None, history=None, cfg: DewCfg | None = None) -> DewResult:
    """지금 온·습도(+표면온도·이력)로 결로 위험을 판정한다.

    history=[(ts,T,RH,surface|None)] 있으면 여유가 좁아지는 추세로 조기 예지.
    """
    cfg = cfg or DewCfg()
    td = dew_point(temp, rh)
    if td is None:
        return DewResult("normal", None, None, 0.0, rh or 0.0, "none", ["데이터 부족 — 판정 보류"])

    ref = surface_temp if surface_temp is not None else temp
    margin = ref - td

    # 여유 추세: 과거를 같은 식으로 여유화해 분당 기울기(좁아지면 음수)
    mser = []
    for s in (history or []):
        ts, ht, hrh, hs = s
        htd = dew_point(ht, hrh)
        if htd is None:
            continue
        href = hs if hs is not None else ht
        mser.append((ts, href - htd))
    mslope = slope_per_min(mser)

    reasons: list = []
    if margin <= cfg.margin_alarm:
        stage, action = "danger", "heater_fan"
        reasons.append(f"이슬점 여유 {round(margin,1)}°C — 결로 임박 (이슬점 {round(td,1)}°C)")
    elif margin <= cfg.margin_warn or (rh is not None and rh >= cfg.rh_high) or (mslope <= -cfg.fall_warn):
        stage, action = "watch", "fan"
        if margin <= cfg.margin_warn:
            reasons.append(f"이슬점 여유 {round(margin,1)}°C 좁음")
        if rh is not None and rh >= cfg.rh_high:
            reasons.append(f"습도 {round(rh)}% 높음")
        if mslope <= -cfg.fall_warn:
            reasons.append(f"여유 축소 추세 {round(mslope,2)}°C/분 — 결로 예지")
    else:
        stage, action = "normal", "none"
        reasons.append(f"정상 — 이슬점 여유 {round(margin,1)}°C")

    return DewResult(stage=stage, dew_point=round(td, 1), margin=round(margin, 1),
                     margin_slope=round(mslope, 2), rh=round(rh or 0.0, 1),
                     action=action, reasons=reasons)
