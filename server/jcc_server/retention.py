"""데이터 보관 기간 — 기본과 고객사별 장기 보관(3년·5년, 유료 묶음).

  기본: 원본 측정값 JCC_KEEP_READING_DAYS(14일) · 경보·활동 기록 JCC_KEEP_EVENT_DAYS(90일) · 시간별 값 JCC_KEEP_HOURLY_DAYS(3년)
  장기 보관을 켠 고객사: 경보·활동 기록과 시간별 값(평균·최저·최고)을 그 햇수만큼. 원본 측정값(몇 초 간격)은 그대로 14일 —
  몇 년치 원본은 디스크를 금방 채우고, 감사·원인 조사에는 시간별 값과 경보·조치 기록이면 충분하다.
  월간 보고서·안전 관리 확인서·점검 보고서는 원래 지우지 않는다.
"""
from __future__ import annotations

import os

YEARS = (0, 3, 5)
D = 86400.0


def _env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def readings_days() -> float:
    return _env("JCC_KEEP_READING_DAYS", 14)


def events_days() -> float:
    return _env("JCC_KEEP_EVENT_DAYS", 90)


def hourly_days() -> float:
    return _env("JCC_KEEP_HOURLY_DAYS", 1100)


def plan(years: int = 0) -> dict:
    """고객 화면·내보내기에 쓰는 보관 기간(일)."""
    long = years * 365 + 5 if years in YEARS and years else 0
    return {"years": years if long else 0, "readings_days": readings_days(),
            "events_days": max(events_days(), long), "hourly_days": max(hourly_days(), long)}


def long_keep(storage) -> dict:
    """장기 보관 고객사 판넬의 기기·판넬 키 → 보관 일수. 정리(prune)가 이 키들은 그 일수까지 남긴다."""
    yrs = {c["id"]: c.get("keep_years") or 0 for c in storage.accounts.list_customers()}
    owner = storage.accounts.panel_owner_map()
    out: dict = {}
    for p in storage.list_panels():
        y = yrs.get(owner.get(p["panel"]), 0)
        if y:
            days = y * 365 + 5
            out[p["panel"]] = days
            for c in p["ccms"]:
                out[c["device_id"]] = days
    return out
