"""진동 ISO 10816 존(A·B·C·D) — 회전 설비 진동 속도(RMS, mm/s)를 국제 표준 등급으로 읽는다.

ISO 10816-1 표의 경계는 설비 등급(Ⅰ~Ⅳ)마다 다르지만 비율이 같다:
  A/B = B/C × 0.4,  C/D = B/C × 2.5.
그래서 존은 그 센서의 경보 기준에서 바로 나온다 — B/C 경계 = '주의' 기준, C/D 경계 = '위험' 기준.
존 글자와 경보가 언제나 같은 말을 한다(기준이 없어 경보가 안 울리는 일이 없다: 제품 기본값이 늘 있다).
설비 등급을 고르면(직원 화면) 표준 기준값이 채워지고, 현장에 맞게 고쳐 쓸 수 있다.
"""
from __future__ import annotations

# 등급: (A/B, B/C, C/D) mm/s — ISO 10816-1
CLASSES = {
    "I": (0.71, 1.8, 4.5),      # 소형 설비(~15 kW)
    "II": (1.12, 2.8, 7.1),     # 중형(15~75 kW, 또는 전용 기초의 ~300 kW)
    "III": (1.8, 4.5, 11.2),    # 대형 · 강성 기초
    "IV": (2.8, 7.1, 18.0),     # 대형 · 유연 기초
}
ZONE_WORD = {"A": "우수", "B": "양호", "C": "주의 · 점검 계획", "D": "위험 · 즉시 조치"}
AB_RATIO = 0.4


def zone(v, warn, mx) -> str | None:
    """값과 그 센서의 주의·위험 기준 → 'A'~'D'. 기준이 모자라면 None."""
    if v is None or warn is None:
        return None
    if mx is None:
        mx = warn * 2.5
    if v >= mx:
        return "D"
    if v > warn:                       # 경보 판정(값 > 주의 기준)과 같은 비교
        return "C"
    return "A" if v < warn * AB_RATIO else "B"


def class_of(warn, mx) -> str | None:
    """주의·위험 기준이 어느 표준 등급과 같으면 그 등급('I'~'IV'), 아니면 None(현장 맞춤)."""
    if warn is None or mx is None:
        return None
    for c, (_, bc, cd) in CLASSES.items():
        if abs(warn - bc) < 1e-6 and abs(mx - cd) < 1e-6:
            return c
    return None


def class_thresholds(c: str) -> dict:
    """설비 등급 → 채울 기준값 {'alarm_warn': B/C, 'alarm_max': C/D}."""
    _, bc, cd = CLASSES[c]
    return {"alarm_warn": bc, "alarm_max": cd}
