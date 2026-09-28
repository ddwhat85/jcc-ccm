"""접점·커넥터 발열 예지 — 전류 대비 온도 상관 이탈로 접촉저항 증가(열화)를 잡는다.

물리: 접점 발열 P=I²R → 온도상승 ΔT ∝ I²·R. 접점이 풀리거나 부식되면 R이 커져
같은 전류에서도 ΔT가 커진다. 정상 데이터로 계수 k(ΔT≈k·I²)를 학습하고, 잔차(측정
ΔT − 예상 ΔT)가 커지거나 추세로 오르면 접점 열화로 판정한다. 판넬 화재의 실제 1위 원인.

입력: 전류 I(A, CT20A), 접점 온도 T(°C, 비접촉 S15S-T), 함내 온도 Ta(°C).
순수 함수 — 엣지·서버 공유.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .fire_risk import slope_per_min


@dataclass
class ContactCfg:
    i_min: float = 2.0          # 이 전류(A) 미만은 발열 미미 → 학습·판정 제외
    res_warn: float = 5.0       # 잔차(°C) 주의
    res_alarm: float = 12.0     # 잔차(°C) 위험(접촉저항 급증)
    t_abs_alarm: float = 60.0   # 접점 절대온도(°C) 위험(하드)
    rise_warn: float = 0.3      # 잔차 상승(°C/분) 주의(열화 추세)
    k_default: float = 0.03     # 기본 k(ΔT=k·I²); 학습 전/부족 시
    k_min: float = 0.0
    k_max: float = 1.0

    @classmethod
    def from_env(cls) -> "ContactCfg":
        c = cls()
        c.res_alarm = float(os.environ.get("JCC_CONTACT_RES_ALARM") or c.res_alarm)
        c.t_abs_alarm = float(os.environ.get("JCC_CONTACT_T_ALARM") or c.t_abs_alarm)
        return c


def fit_k(samples: list, cfg: ContactCfg) -> float:
    """samples=[(ts,I,T,Ta)] 에서 ΔT=k·I² 최소제곱 k. 부하 낮은 표본은 제외.

    ⚠ 신뢰할 학습을 위해 samples는 '정상(초기)' 구간이어야 한다. 열화 구간까지
    포함하면 k가 부풀어 현재 이상을 가릴 수 있다(실기에선 초기 기준 학습 권장).
    """
    num = den = 0.0
    for s in samples:
        _, I, T, Ta = s
        if I is None or T is None or Ta is None or I < cfg.i_min:
            continue
        x = I * I
        y = T - Ta
        num += x * y
        den += x * x
    if den <= 1e-9:
        return cfg.k_default
    return min(cfg.k_max, max(cfg.k_min, num / den))


@dataclass
class ContactResult:
    stage: str                  # normal | watch | danger
    delta_t: float              # 측정 온도상승(T−Ta)
    expected: float             # 예상 온도상승(k·I²)
    residual: float             # 초과 발열(측정−예상)
    residual_slope: float       # 잔차 분당 상승(추세)
    k: float
    reasons: list


def assess_contact(current, temp, ambient, history, cfg: ContactCfg | None = None) -> ContactResult:
    """지금 전류·접점온도·함내온도 + 정상 이력으로 접점 발열 이상을 판정한다."""
    cfg = cfg or ContactCfg()
    k = fit_k(history or [], cfg)
    dt = (temp - ambient) if (temp is not None and ambient is not None) else 0.0
    exp = k * (current or 0.0) ** 2
    res = dt - exp

    # 잔차 추세: 과거 표본을 현재 k로 잔차화해 분당 기울기(열화는 서서히 오른다)
    rser = []
    for s in (history or []):
        ts, I, T, Ta = s
        if I is None or T is None or Ta is None or I < cfg.i_min:
            continue
        rser.append((ts, (T - Ta) - k * I * I))
    rslope = slope_per_min(rser)

    reasons: list = []
    if temp is not None and temp >= cfg.t_abs_alarm:
        stage = "danger"
        reasons.append(f"접점 온도 {round(temp,1)}°C — 절대 위험")
    elif (current or 0.0) < cfg.i_min:
        stage = "normal"
        reasons.append("부하 낮음 — 발열 판정 보류")
    elif res >= cfg.res_alarm:
        stage = "danger"
        reasons.append(f"전류 대비 초과발열 +{round(res,1)}°C — 접촉저항 급증 의심")
    elif res >= cfg.res_warn or rslope >= cfg.rise_warn:
        stage = "watch"
        if res >= cfg.res_warn:
            reasons.append(f"전류 대비 발열 +{round(res,1)}°C")
        if rslope >= cfg.rise_warn:
            reasons.append(f"발열 추세 상승 {round(rslope,2)}°C/분 — 접점 열화 조짐")
    else:
        stage = "normal"
        reasons.append("정상 — 전류 대비 발열 정상")

    return ContactResult(stage=stage, delta_t=round(dt, 1), expected=round(exp, 1),
                         residual=round(res, 1), residual_slope=round(rslope, 2),
                         k=round(k, 4), reasons=reasons)
