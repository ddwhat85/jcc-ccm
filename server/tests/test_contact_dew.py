"""접점 발열 예지(contact_heat) + 결로 예지(dewpoint) 시나리오 테스트.

pytest 없이 단독 실행: python -m tests.test_contact_dew  (server/ 에서)
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server.contact_heat import ContactBaseline, ContactCfg, assess_contact, fit_k
from jcc_server.dewpoint import DewCfg, assess_dewpoint, dew_point

_fails = []


def check(name, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


# ── 접점 발열용 정상 이력 생성: ΔT = k·I² + 잡음, 전류는 부하 변동 ──
def contact_hist(k, n=24, step=60, noise=0.4):
    rows = []
    amps = [6, 9, 12, 15, 11, 8, 14, 10]
    for i in range(n):
        I = amps[i % len(amps)] + (0.7 if i % 3 == 0 else -0.5)
        Ta = 25.0 + (0.3 if i % 2 else -0.3)
        wobble = noise * (1 if i % 2 == 0 else -1) * (0.5 + (i % 4) / 4.0)
        T = Ta + k * I * I + wobble
        rows.append((i * step, I, T, Ta))
    return rows


# ── 결로용 정상 이력: 온·습도 서서히 변화 ──
def dew_hist(temps, rhs, surf=None, step=60):
    rows = []
    for i, (t, r) in enumerate(zip(temps, rhs)):
        s = surf[i] if surf is not None else None
        rows.append((i * step, t, r, s))
    return rows


def run():
    print("=== 접점 발열 예지 (contact_heat) ===")
    cfg = ContactCfg()

    # 1) fit_k 가 참 계수를 복원
    k_true = 0.05
    hist = contact_hist(k_true)
    k_est = fit_k(hist, cfg)
    check("fit_k 계수 복원", abs(k_est - k_true) < 0.01, f"k_est={k_est:.4f} (참 {k_true})")

    # 2) 정상: 지금 전류·온도가 학습된 관계와 일치 → normal
    Ta = 25.0
    I = 15.0
    T_normal = Ta + k_true * I * I  # 예상대로
    r = assess_contact(I, T_normal, Ta, hist, cfg)
    check("정상(전류 대비 발열 정상)", r.stage == "normal", f"stage={r.stage} res={r.residual}")

    # 3) 위험: 같은 전류인데 초과발열 큼(잔차 ≥ 12°C) → danger
    T_hot = Ta + k_true * I * I + 14.0
    r = assess_contact(I, T_hot, Ta, hist, cfg)
    check("위험(접촉저항 급증)", r.stage == "danger", f"stage={r.stage} res={r.residual}")

    # 4) 주의: 잔차가 5~12°C 사이 → watch
    T_warm = Ta + k_true * I * I + 7.0
    r = assess_contact(I, T_warm, Ta, hist, cfg)
    check("주의(전류 대비 발열)", r.stage == "watch", f"stage={r.stage} res={r.residual}")

    # 5) 절대온도 하드: 접점 60°C 이상 → danger (전류 무관)
    r = assess_contact(3.0, 63.0, 25.0, hist, cfg)
    check("절대온도 위험", r.stage == "danger", f"stage={r.stage} T=63")

    # 6) 부하 낮음: 판정 보류 → normal
    r = assess_contact(1.0, 40.0, 25.0, hist, cfg)
    check("부하 낮음 보류", r.stage == "normal", f"stage={r.stage}")

    # 7) 예지(추세): 잔차가 서서히 상승하는 이력 → residual_slope 양수
    rows = []
    for i in range(24):
        I2 = 12.0
        Ta2 = 25.0
        drift = 0.05 * i  # 분당 접점 열화로 잔차 상승
        T2 = Ta2 + k_true * I2 * I2 + drift
        rows.append((i * 60, I2, T2, Ta2))
    r = assess_contact(12.0, 25.0 + k_true * 144 + 1.2, 25.0, rows, cfg)
    check("예지(발열 추세 상승 감지)", r.residual_slope > 0.0, f"slope={r.residual_slope}°C/분")

    print("\n=== 접점 기준 학습 (서서히 풀리는 접점) ===")
    # 20일 동안 1분마다: 부하는 8~16A로 오르내리고, 접촉저항(k)은 2일째부터 하루 15%씩 증가
    # (실제 접점 사고의 전형 — 몇 주에 걸쳐 조금씩 풀림·부식)
    import math
    k_true, Ta = 0.03, 25.0
    bl = ContactBaseline(cfg)
    hist = []
    first_watch_new = first_watch_old = None
    for m in range(20 * 1440):
        ts = m * 60.0
        day = m / 1440.0
        I = 12 + 4 * math.sin(m / 37.0)
        k_now = k_true * (1 + 0.15 * max(0.0, day - 2))
        T = Ta + k_now * I * I + 0.2 * math.sin(m * 1.7)          # 작은 측정 잡음
        hist.append((ts, I, T, Ta))
        hist = hist[-24:]
        bl.observe(ts, I, T, Ta)
        if m % 60:                                                    # 1시간마다 판정
            continue
        new = assess_contact(I, T, Ta, hist, cfg, k_ref=bl.k if bl.ready else None)
        old = assess_contact(I, T, Ta, hist, cfg)                     # 최근 24분 창으로 매번 다시 맞춤
        if new.stage != "normal" and first_watch_new is None:
            first_watch_new = day
        if old.stage != "normal" and first_watch_old is None:
            first_watch_old = day
    # 1분 주기면 300표본 = 약 5시간(표본 수·30분 기간 둘 다 채워야 확정)
    check("기준 학습 확정(300표본 채운 시점)", bl.ready and bl.learned_at == 299 * 60,
          f"learned_at={bl.learned_at / 3600:.1f}시간")
    check("서서히 풀리는 접점을 잡음(20일 안)", first_watch_new is not None and first_watch_new < 20,
          f"{first_watch_new and round(first_watch_new,1)}일째 주의")
    check("기존 창 방식은 끝까지 놓침(개선 근거)", first_watch_old is None,
          f"창 방식 첫 경보={first_watch_old}")
    # 설계 한계: 평소 부하(RMS I²)에서 가려지는 열화는 최대 res_warn/2(=2.5°C)
    x_ref = (bl.sxx / bl.n) ** 0.5
    hidden = (bl.k - k_true) * x_ref
    check("기준에 스며든 열화 ≤ 2.5°C(평소 부하 기준)", hidden <= cfg.res_warn / 2 + 0.05,
          f"k={bl.k:.4f}(참 {k_true}) → 평소 부하에서 {hidden:.2f}°C 가려짐")

    # 저장·복원: 재시작해도 기준 유지(다시 배우면 그동안의 열화가 새 기준에 흡수됨)
    bl2 = ContactBaseline(cfg, bl.to_dict())
    check("저장·복원 후 기준 유지", bl2.ready and abs(bl2.k - bl.k) < 1e-12)
    bl2.reset()
    check("정비 후 재학습(reset) → 학습 중", not bl2.ready and bl2.progress() == 0.0)
    # 재학습은 reset 이후 표본만 쓴다(정비 전 이력으로 즉시 재확정되면 안 됨)
    before = bl2.last_ts
    for ts, I, T, Ta_ in hist:                     # 정비 전 이력(시각 ≤ before)을 다시 먹여도
        bl2.observe(ts, I, T, Ta_)
    check("재학습은 reset 이후 표본만 사용", bl2.n == 0 and bl2.last_ts == before, f"n={bl2.n}")
    # 학습 중 진행률: 표본 수·기간 중 느린 쪽
    b3 = ContactBaseline(cfg)
    for m in range(150):
        b3.observe(m * 6.0, 12, 30, 25)
    check("학습 진행률(느린 쪽 기준)", not b3.ready and 0.45 <= b3.progress() <= 0.51,
          f"{b3.progress():.2f} (표본 150/300, 기간 894/1800초)")
    # 부하 낮은 표본·중복 시각은 학습에서 제외
    b4 = ContactBaseline(cfg)
    b4.observe(10.0, 1.0, 30, 25)
    b4.observe(20.0, 12, 30, 25)
    b4.observe(20.0, 12, 30, 25)
    check("저부하·중복 표본 제외", b4.n == 1, f"n={b4.n}")

    print("\n=== 결로 예지 (dewpoint) ===")
    dcfg = DewCfg()

    # 8) dew_point 공식 검증: 25°C/50% → 약 13.9°C
    td = dew_point(25.0, 50.0)
    check("이슬점 공식(25°C/50%≈13.9)", td is not None and abs(td - 13.9) < 0.4, f"Td={td:.2f}")

    # 9) 정상: 표면 25°C, 건조(RH 40%) → 여유 큼 → normal
    r = assess_dewpoint(25.0, 40.0, surface_temp=25.0, cfg=dcfg)
    check("정상(여유 큼)", r.stage == "normal", f"stage={r.stage} margin={r.margin}")

    # 10) 위험: 표면이 이슬점 근처(여유 ≤ 1°C) → danger + heater_fan
    td2 = dew_point(25.0, 70.0)  # ≈19.1
    r = assess_dewpoint(25.0, 70.0, surface_temp=td2 + 0.5, cfg=dcfg)
    check("위험(결로 임박)", r.stage == "danger" and r.action == "heater_fan",
          f"stage={r.stage} action={r.action} margin={r.margin}")

    # 11) 주의: 여유가 1~3°C → watch + fan
    r = assess_dewpoint(25.0, 70.0, surface_temp=td2 + 2.5, cfg=dcfg)
    check("주의(여유 좁음)", r.stage == "watch" and r.action == "fan",
          f"stage={r.stage} margin={r.margin}")

    # 12) 습도 하드: 표면 여유 커도 RH ≥ 80% → 최소 watch
    r = assess_dewpoint(25.0, 88.0, surface_temp=25.0, cfg=dcfg)
    check("고습도 주의", r.stage == "watch", f"stage={r.stage} rh={r.rh}")

    # 13) 예지(추세): 여유가 좁아지는 이력 → margin_slope 음수, watch 진입
    temps = [25.0] * 20
    rhs = [45 + i * 1.8 for i in range(20)]  # 습도 상승 → 이슬점 상승 → 여유 축소
    surf = [24.0] * 20
    hist_d = dew_hist(temps, rhs, surf)
    r = assess_dewpoint(25.0, 45 + 19 * 1.8, surface_temp=24.0, history=hist_d, cfg=dcfg)
    check("예지(여유 축소 추세 감지)", r.margin_slope < 0.0, f"slope={r.margin_slope}°C/분")

    # 14) 먼 곳의 요동: 여유 11°C에서 분당 1°C씩 줄어도 → 정상(오경보 방지)
    far = [(i * 6, 25.0, 50.0, 27.0 - i * (2.0 / 19)) for i in range(20)]
    r = assess_dewpoint(25.0, 50.0, surface_temp=25.0, history=far, cfg=dcfg)
    check("먼 곳 요동은 무시", r.stage == "normal", f"stage={r.stage} margin={r.margin} slope={r.margin_slope}")

    # 15) 가까운 추세: 여유 5°C에서 분당 ~0.6°C 축소 → 약 7분 뒤 결로 임박 → 주의
    near = [(i * 6, 25.0, 50.0, 20.0 - i * (1.1 / 19)) for i in range(20)]
    r = assess_dewpoint(25.0, 50.0, surface_temp=18.9, history=near, cfg=dcfg)
    check("가까운 추세는 주의(ETA)", r.stage == "watch" and any("분 뒤" in x for x in r.reasons),
          f"stage={r.stage} margin={r.margin} reasons={r.reasons}")

    print()
    if _fails:
        print(f"❌ {len(_fails)} FAIL: {_fails}")
        return 1
    print("✅ 전체 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
