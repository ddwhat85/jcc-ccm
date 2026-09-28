"""접점·커넥터 발열 예지 — 전류 대비 온도 상관 이탈로 접촉저항 증가(열화)를 잡는다.

물리: 접점 발열 P=I²R → 온도상승 ΔT ∝ I²·R. 접점이 풀리거나 부식되면 R이 커져
같은 전류에서도 ΔT가 커진다. 정상 기간에서 계수 k(ΔT≈k·I²)를 학습하고, 잔차(측정
ΔT − 예상 ΔT)가 커지거나 추세로 오르면 접점 열화로 판정한다. 판넬 화재의 실제 1위 원인.

기준 k는 반드시 '정상 기간'에서 배워 고정한다(ContactBaseline). 최근 창만으로 매번
다시 맞추면 몇 주에 걸쳐 서서히 풀리는 접점 — 실제 사고의 대부분 — 은 기준이 같이
따라 올라가 끝까지 정상으로 보인다. 고정 기준은 아주 천천히(시상수 tau_days)만,
그것도 잔차가 작을 때만 따라가므로 열화는 기준에 흡수되지 못하고 드러난다.

입력: 전류 I(A, CT20A), 접점 온도 T(°C, 비접촉 S15S-T), 함내 온도 Ta(°C).
순수 로직 — 엣지·서버 공유(firmware/jcc_ccm/predict/ 에 사본).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

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
    # 기준 학습: 두 조건을 모두 채워야 기준 확정(부하가 몇 번은 오르내린 뒤)
    learn_samples: int = 300    # 부하 있는(I≥i_min) 유효 표본 수
    learn_span: float = 1800.0  # 학습 기간(초) — 기본 30분
    tau_days: float = 7.0       # 확정 뒤 기준이 따라가는 시상수(일). 잔차가 작을 때만 추종

    @classmethod
    def from_env(cls) -> "ContactCfg":
        c = cls()
        c.res_alarm = float(os.environ.get("JCC_CONTACT_RES_ALARM") or c.res_alarm)
        c.t_abs_alarm = float(os.environ.get("JCC_CONTACT_T_ALARM") or c.t_abs_alarm)
        c.learn_samples = int(os.environ.get("JCC_CONTACT_LEARN_SAMPLES") or c.learn_samples)
        c.learn_span = float(os.environ.get("JCC_CONTACT_LEARN_SPAN") or c.learn_span)
        c.tau_days = float(os.environ.get("JCC_CONTACT_TAU_DAYS") or c.tau_days)
        return c


def fit_k(samples: list, cfg: ContactCfg) -> float:
    """samples=[(ts,I,T,Ta)] 에서 ΔT=k·I² 최소제곱 k. 부하 낮은 표본은 제외.

    ⚠ 최근 창에 쓰면 열화가 k에 흡수된다 — 기준 확정 전 임시로만 쓴다(ContactBaseline).
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


class ContactBaseline:
    """접점 발열 기준 k를 '정상 기간'에서 학습해 고정하고, 아주 천천히만 따라간다.

    learning : 부하 있는 표본을 모아 최소제곱 합(Σx·y, Σx²)을 쌓는다.
               learn_samples 개 이상 & learn_span 초 이상이면 기준 확정(ready).
    ready    : 평소 부하(학습 기간 I²의 RMS)에서 본 잔차가 작을 때(< res_warn/2)만
               시상수 tau_days로 추종한다. 그보다 커지면 추종을 멈추므로, 서서히 진행되는
               열화도 평소 부하 기준 최대 res_warn/2 까지만 가려지고 그 뒤로는 드러난다.
    상태는 to_dict()로 저장해 재시작해도 유지한다(재시작마다 다시 배우면 그동안의
    열화가 새 기준에 흡수된다). 접점을 정비·교체했으면 reset()으로 다시 배운다.
    """

    def __init__(self, cfg: ContactCfg | None = None, state: dict | None = None):
        self.cfg = cfg or ContactCfg()
        s = state or {}
        self.status = s.get("status", "learning")
        self.k = s.get("k")
        self.sxy = float(s.get("sxy", 0.0))
        self.sxx = float(s.get("sxx", 0.0))
        self.n = int(s.get("n", 0))
        self.t0 = s.get("t0")
        self.last_ts = s.get("last_ts")
        self.learned_at = s.get("learned_at")
        self.dirty = False

    @property
    def ready(self) -> bool:
        return self.status == "ready" and self.k is not None

    def progress(self) -> float:
        """학습 진행률 0..1(표본 수·기간 중 느린 쪽)."""
        if self.ready:
            return 1.0
        if self.t0 is None or self.last_ts is None:
            return 0.0
        return min(1.0, self.n / max(1, self.cfg.learn_samples),
                   (self.last_ts - self.t0) / max(1.0, self.cfg.learn_span))

    def observe(self, ts, I, T, Ta):
        """표본 1개를 반영한다. 기준이 이번에 확정되면 "ready", 아니면 None."""
        cfg = self.cfg
        if ts is None or I is None or T is None or Ta is None or I < cfg.i_min:
            return None
        if self.last_ts is not None and ts <= self.last_ts:
            return None                      # 이미 반영한 표본
        dt = (ts - self.last_ts) if self.last_ts is not None else 0.0
        self.last_ts = ts
        self.dirty = True
        x, y = I * I, T - Ta
        if not self.ready:
            if self.t0 is None:
                self.t0 = ts
            self.sxy += x * y
            self.sxx += x * x
            self.n += 1
            if (self.n >= cfg.learn_samples and ts - self.t0 >= cfg.learn_span
                    and self.sxx > 1e-9):
                self.k = min(cfg.k_max, max(cfg.k_min, self.sxy / self.sxx))
                self.status = "ready"
                self.learned_at = ts
                return "ready"
            return None
        if dt > 0 and x > 1e-9:
            k_obs = min(cfg.k_max, max(cfg.k_min, y / x))
            # 추종 여부는 '평소 부하'에서의 잔차로 판단한다. 표본별 잔차로 보면 저부하
            # 표본(같은 저항 증가도 잔차가 작음)이 계속 통과해 열화가 기준에 스며든다.
            x_ref = (self.sxx / self.n) ** 0.5 if self.n else x      # 학습 기간 I²의 RMS
            if abs(k_obs - self.k) * x_ref < cfg.res_warn / 2:
                a = min(1.0, dt / (cfg.tau_days * 86400.0))
                self.k += a * (k_obs - self.k)
        return None

    def skip(self, ts) -> None:
        """이 시각까지의 표본은 '본 것'으로 치고 배우지 않는다 — 이상 구간(발열 진행 중)을
        기준으로 배우면 고장을 정상으로 학습하게 된다. 나중에 이력 창으로 다시 들어와도 무시."""
        if ts is not None and (self.last_ts is None or ts > self.last_ts):
            self.last_ts = ts
            self.dirty = True

    def reset(self) -> None:
        """다시 배운다. 마지막 본 시각은 남겨 '재학습 이후' 표본만 쓰게 한다 — 안 그러면
        바로 다음 판정에서 최근 이력 창(정비 전, 이미 열화된 값)으로 즉시 다시 확정된다."""
        last = self.last_ts
        self.__init__(self.cfg)
        self.last_ts = last
        self.dirty = True

    def to_dict(self) -> dict:
        return {"status": self.status, "k": self.k, "sxy": self.sxy, "sxx": self.sxx,
                "n": self.n, "t0": self.t0, "last_ts": self.last_ts, "learned_at": self.learned_at}


@dataclass
class ContactResult:
    stage: str                  # normal | watch | danger
    delta_t: float              # 측정 온도상승(T−Ta)
    expected: float             # 예상 온도상승(k·I²)
    residual: float             # 초과 발열(측정−예상)
    residual_slope: float       # 잔차 분당 상승(추세)
    k: float
    reasons: list


def assess_contact(current, temp, ambient, history, cfg: ContactCfg | None = None,
                   k_ref: float | None = None, learning: float | None = None) -> ContactResult:
    """지금 전류·접점온도·함내온도로 접점 발열 이상을 판정한다.

    k_ref: 정상 기간에서 학습해 고정한 기준 k(ContactBaseline). 없으면 최근 이력으로
           임시 추정한다(기준 학습 중 — 느린 열화는 이 동안 못 잡는다).
    learning: 기준 학습 진행률(0..1). 주면 근거에 '기준 학습 중'을 덧붙인다.
    """
    cfg = cfg or ContactCfg()
    k = k_ref if k_ref is not None else fit_k(history or [], cfg)
    dt = (temp - ambient) if (temp is not None and ambient is not None) else 0.0
    exp = k * (current or 0.0) ** 2
    res = dt - exp

    # 잔차 추세: 과거 표본을 같은 k로 잔차화해 분당 기울기(급격한 발열 진행)
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
            reasons.append(f"전류 대비 발열 +{round(res,1)}°C" +
                           (" — 기준 대비 서서히 증가(접점 풀림·부식 의심)" if k_ref is not None else ""))
        if rslope >= cfg.rise_warn:
            reasons.append(f"발열 추세 상승 {round(rslope,2)}°C/분 — 접점 열화 조짐")
    else:
        stage = "normal"
        reasons.append("정상 — 전류 대비 발열 정상")
    if learning is not None and k_ref is None:
        reasons.append(f"기준 학습 중 {int(learning * 100)}% — 느린 열화 판정은 학습 후")

    return ContactResult(stage=stage, delta_t=round(dt, 1), expected=round(exp, 1),
                         residual=round(res, 1), residual_slope=round(rslope, 2),
                         k=round(k, 4), reasons=reasons)
