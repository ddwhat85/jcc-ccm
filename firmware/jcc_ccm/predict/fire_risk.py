"""화재 징조 예지 코어 — 화재 위험지수(FRI) 산출.

리튬/ESS 열폭주는 오프가스(H2·VOC) 발생 → 온도 상승 → 연기 → 화염 순으로 간다.
가스의 '값'뿐 아니라 '상승 속도'와 '평소 대비 급등'을 잡아 화염·연기 전에 예지한다.

이 모듈은 **순수 함수**다(부작용·I/O 없음). 같은 로직을 펌웨어(엣지)와 서버가 공유한다.
벤트 실제 작동·상태·타이머는 호출측(VentController/monitor)이 담당한다.

핵심 계산
  1) 신호별 3개 특징을 [0,1]로 정규화:
     level  = ramp(value, warn, alarm)          현재값이 경고~위험 사이 어디인가
     rise   = ramp(slope/min, riseWarn, riseAlarm)  얼마나 빨리 오르는가(예지)
     anom   = ramp(robust_z, 3.0, 6.0)          평소 분포 대비 급등(중앙값·MAD)
     g = max(level, rise, anom)                 가스 위험도(어느 신호든 강하면 위험)
  2) H2·VOC가 동시에 활성이면 열폭주 서명 → 동반 가산(co-boost)
  3) 온도 급상승·연기·전류이상 = 확증 가산
  4) FRI = 100 * clamp(0.38*gH2 + 0.38*gVOC + co + 0.10*temp + conf)
  5) 안전 하한(hard floor): 가스가 위험(alarm) 초과거나 연기 감지면 FRI를 강제로 끌어올린다
  6) 단계: normal < watch(30) < danger(55) < critical(80)  (+ 하드 조건)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


# ── 유틸: 정규화·기울기·로버스트 z ──────────────────────────
def ramp(x: float, lo: float, hi: float) -> float:
    """x를 [lo,hi] 구간에서 0..1로 선형 매핑(밖은 0 또는 1)."""
    if hi <= lo:
        return 1.0 if x >= hi else 0.0
    if x <= lo:
        return 0.0
    if x >= hi:
        return 1.0
    return (x - lo) / (hi - lo)


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def slope_per_min(series: list[tuple[float, float]]) -> float:
    """(ts초, 값) 표본들의 최소제곱 기울기를 '분당 변화량'으로 돌려준다.

    표본이 2개 미만이거나 시간 폭이 0이면 0.0. 최근 값이 오르면 양수.
    """
    pts = [(t, v) for t, v in series if v is not None]
    n = len(pts)
    if n < 2:
        return 0.0
    t0 = pts[0][0]
    xs = [t - t0 for t, _ in pts]            # 초
    ys = [v for _, v in pts]
    mx = sum(xs) / n
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 1e-9:
        return 0.0
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    slope_per_sec = num / den
    return slope_per_sec * 60.0              # 분당


def robust_z(value: float, baseline: list[float]) -> float:
    """평소 분포(baseline) 대비 현재값의 로버스트 z (중앙값·MAD 기반).

    이상치에 강하다. 표본이 적으면 0.0. MAD=0이면 표준편차로 대체.
    """
    vals = [v for v in baseline if v is not None]
    if len(vals) < 6 or value is None:
        return 0.0
    s = sorted(vals)
    m = len(s)
    med = s[m // 2] if m % 2 else (s[m // 2 - 1] + s[m // 2]) / 2
    dev = sorted(abs(v - med) for v in vals)
    mad = dev[m // 2] if m % 2 else (dev[m // 2 - 1] + dev[m // 2]) / 2
    if mad > 1e-9:
        sigma = 1.4826 * mad
    else:                                     # 변동이 거의 없으면 표준편차로
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        sigma = var ** 0.5
    if sigma <= 1e-9:
        return 0.0
    return (value - med) / sigma


# ── 설정 ────────────────────────────────────────────────────
@dataclass
class GasCfg:
    warn: float
    alarm: float
    rise_warn: float      # 분당 상승 경고
    rise_alarm: float     # 분당 상승 위험


@dataclass
class FireConfig:
    h2: GasCfg = field(default_factory=lambda: GasCfg(warn=10.0, alarm=25.0, rise_warn=3.0, rise_alarm=10.0))   # %LEL, %LEL/min
    voc: GasCfg = field(default_factory=lambda: GasCfg(warn=200.0, alarm=1000.0, rise_warn=100.0, rise_alarm=400.0))  # ppm
    temp_rise_warn: float = 1.0     # °C/min
    temp_rise_alarm: float = 5.0
    w_h2: float = 0.38
    w_voc: float = 0.38
    w_temp: float = 0.10
    co_boost: float = 0.20          # H2·VOC 동반 가산 계수
    conf_boost: float = 0.15        # 연기/전류이상 확증 가산
    z_lo: float = 3.0               # 이상탐지 z 시작
    z_hi: float = 6.0
    open_fri: float = 55.0          # 벤트 개방(위험)
    close_fri: float = 20.0         # 벤트 닫힘(복구, 히스테리시스)
    crit_fri: float = 80.0          # 극한
    watch_fri: float = 30.0

    @classmethod
    def from_env(cls) -> "FireConfig":
        c = cls()
        c.open_fri = float(os.environ.get("JCC_FIRE_OPEN_FRI") or c.open_fri)
        c.close_fri = float(os.environ.get("JCC_FIRE_CLOSE_FRI") or c.close_fri)
        c.crit_fri = float(os.environ.get("JCC_FIRE_CRIT_FRI") or c.crit_fri)
        return c


@dataclass
class FireAssessment:
    fri: float
    stage: str                      # normal | watch | danger | critical
    reasons: list[str]
    terms: dict                     # 계산 근거(디버그·표시)


# ── 가스 위험도 한 종 ───────────────────────────────────────
def _gas_score(value, series, baseline, cfg: GasCfg, zcfg: FireConfig):
    """한 가스의 위험도 g=max(level,rise,anom)와 근거를 돌려준다."""
    lvl = ramp(value if value is not None else 0.0, cfg.warn, cfg.alarm)
    sp = slope_per_min(series or [])
    rise = ramp(sp, cfg.rise_warn, cfg.rise_alarm)
    z = robust_z(value, baseline or [])
    anom = ramp(z, zcfg.z_lo, zcfg.z_hi)
    g = max(lvl, rise, anom)
    return g, {"value": value, "level": round(lvl, 2), "slope_per_min": round(sp, 2),
               "rise": round(rise, 2), "z": round(z, 2), "anom": round(anom, 2),
               "g": round(g, 2)}


# ── 메인 평가 ───────────────────────────────────────────────
def assess(signals: dict, cfg: FireConfig | None = None) -> FireAssessment:
    """존의 신호로 화재 위험지수와 단계를 산출한다.

    signals = {
      "h2":  {"value": %LEL, "series": [(ts,v)..], "baseline": [v..]},
      "voc": {"value": ppm,  "series": [...],      "baseline": [...]},
      "temp":{"value": °C,   "series": [...]},
      "smoke": bool, "current_abnormal": bool,
    }  (없는 키는 안전하게 무시)
    """
    cfg = cfg or FireConfig()
    h2 = signals.get("h2") or {}
    voc = signals.get("voc") or {}
    temp = signals.get("temp") or {}
    smoke = bool(signals.get("smoke"))
    cur_ab = bool(signals.get("current_abnormal"))

    g_h2, t_h2 = _gas_score(h2.get("value"), h2.get("series"), h2.get("baseline"), cfg.h2, cfg)
    g_voc, t_voc = _gas_score(voc.get("value"), voc.get("series"), voc.get("baseline"), cfg.voc, cfg)

    temp_sp = slope_per_min(temp.get("series") or [])
    temp_term = ramp(temp_sp, cfg.temp_rise_warn, cfg.temp_rise_alarm)

    co = cfg.co_boost * min(g_h2, g_voc) if (g_h2 >= 0.3 and g_voc >= 0.3) else 0.0
    conf = cfg.conf_boost if (smoke or cur_ab) else 0.0

    fri = 100.0 * clamp01(cfg.w_h2 * g_h2 + cfg.w_voc * g_voc + co
                          + cfg.w_temp * temp_term + conf)

    # 안전 하한: 가스가 이미 위험 임계 초과거나 연기면 FRI를 강제로 끌어올린다
    h2v, vocv = h2.get("value"), voc.get("value")
    if (h2v is not None and h2v >= cfg.h2.alarm) or (vocv is not None and vocv >= cfg.voc.alarm):
        fri = max(fri, cfg.crit_fri)
    if smoke:
        fri = max(fri, 85.0)

    # 단계 판정(FRI + 하드 조건)
    both_strong = g_h2 >= 0.5 and g_voc >= 0.5
    if fri >= cfg.crit_fri or smoke or \
       (h2v is not None and h2v >= cfg.h2.alarm and vocv is not None and vocv >= cfg.voc.alarm):
        stage = "critical"
    elif fri >= cfg.open_fri or both_strong:
        stage = "danger"
    elif fri >= cfg.watch_fri:
        stage = "watch"
    else:
        stage = "normal"

    # 사람이 읽는 근거
    reasons: list[str] = []
    if t_h2["rise"] > 0.3:
        reasons.append(f"H2 상승 {t_h2['slope_per_min']}%LEL/분")
    if t_h2["anom"] > 0.3:
        reasons.append(f"H2 평소대비 급등 z={t_h2['z']}")
    if t_h2["level"] > 0.3:
        reasons.append(f"H2 {h2v}%LEL(경고선 접근)")
    if t_voc["rise"] > 0.3:
        reasons.append(f"VOC 상승 {t_voc['slope_per_min']}ppm/분")
    if t_voc["anom"] > 0.3:
        reasons.append(f"VOC 평소대비 급등 z={t_voc['z']}")
    if t_voc["level"] > 0.3:
        reasons.append(f"VOC {vocv}ppm(경고선 접근)")
    if co > 0:
        reasons.append("H2·VOC 동반 상승 — 열폭주 서명")
    if temp_term > 0.3:
        reasons.append(f"온도 급상승 {round(temp_sp,1)}°C/분")
    if smoke:
        reasons.append("열연기 감지")
    if cur_ab:
        reasons.append("전류 이상")
    if not reasons:
        reasons.append("정상 범위")

    return FireAssessment(fri=round(fri, 1), stage=stage, reasons=reasons,
                          terms={"h2": t_h2, "voc": t_voc,
                                 "temp_slope_per_min": round(temp_sp, 2),
                                 "temp_term": round(temp_term, 2),
                                 "co_boost": round(co, 3), "confirm": round(conf, 3)})


# ── 벤트 컨트롤러 (히스테리시스·단계적 자동) ────────────────
class VentController:
    """FRI 단계 → 벤트 개방/닫힘을 히스테리시스로 결정한다(존별 상태 보유).

    open  : danger/critical 진입 시
    hold  : critical 유지(+ 차단기/소화 연동 신호)
    close : normal & FRI<close_fri 가 유지시간 지속 시(복구)
    수동(manual) 상태면 자동 로직은 보류(사람 우선).
    """

    def __init__(self, cfg: FireConfig | None = None, recover_hold: float = 60.0):
        self.cfg = cfg or FireConfig()
        self.recover_hold = float(recover_hold)
        self.open = False          # 벤트 열림?
        self.manual = False        # 사람이 수동 조작 중?
        self._below_since = None    # FRI가 close 밑으로 내려간 시각

    def set_manual(self, open_: bool):
        self.manual = True
        self.open = bool(open_)

    def clear_manual(self):
        self.manual = False

    def step(self, fri: float, stage: str, now: float) -> str | None:
        """이번 주기의 조치를 돌려준다: "open"|"hold"|"close"|None."""
        if self.manual:
            return None
        if stage in ("danger", "critical"):
            self._below_since = None
            if not self.open:
                self.open = True
                return "open"
            return "hold" if stage == "critical" else None
        # normal/watch
        if self.open:
            if fri < self.cfg.close_fri:
                if self._below_since is None:
                    self._below_since = now
                elif now - self._below_since >= self.recover_hold:
                    self.open = False
                    self._below_since = None
                    return "close"
            else:
                self._below_since = None
        return None
