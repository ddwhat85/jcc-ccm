"""예지 파라미터 레지스트리 — 튜닝 손잡이·기본값·화면 범위·절대 한계·관계 규칙을 한 곳에.

튜닝 콘솔(대시보드)·서버 적용 관문·CCM 수신 검증이 모두 이 표를 따른다.

  · default : 코드 설정(FireConfig·ContactCfg·DewCfg)의 기본값과 같아야 한다(테스트가 확인)
  · ui      : 화면 손잡이 범위 — 절대 한계보다 좁다(손잡이 끝이 곧 한계가 되지 않게)
  · hard    : 절대 한계. 서버가 뚫려 이상한 설정을 보내도 CCM이 이 밖의 값은 거부한다.
              ⚠ 펌웨어의 이 파일 사본은 서명된 OTA로만 바뀐다 → 한계를 바꾸려면 JCC 서명이 필요.

입력 보호(워밍업·끊김)와 접점 학습 파라미터는 손잡이가 아니다(학습 중 바꾸면 기준이 오염).

순수 로직 — 설정 클래스를 import하지 않고 속성 경로(fire.h2.warn)로만 읽고 쓴다.
⚠ firmware/jcc_ccm/predict/ 에 같은 파일이 복사돼 있다(tools/sync_edge_core.py).
"""
from __future__ import annotations

import math


def _p(key, group, label, unit, default, ui, hard, desc):
    return {"key": key, "group": group, "label": label, "unit": unit, "default": float(default),
            "ui": (float(ui[0]), float(ui[1]), float(ui[2])), "hard": (float(hard[0]), float(hard[1])),
            "desc": desc}


def _gas(g, name, unit, d, ui, hard):
    """가스 하나의 손잡이 4개(주의·경보·상승 주의·상승 위험)."""
    labels = (("warn", "주의", unit), ("alarm", "경보", unit),
              ("rise_warn", "상승 주의", unit + "/분"), ("rise_alarm", "상승 위험", unit + "/분"))
    return [_p(f"fire.{g}.{f}", "fire_gas", f"{name} {lab}", u, d[i], ui[i], hard[i],
               f"{name} {lab} 기준") for i, (f, lab, u) in enumerate(labels)]


PARAMS: list = [
    *_gas("h2", "H2", "%LEL", (10, 25, 3, 10),
          ((2, 20, 0.5), (10, 45, 1), (0.5, 10, 0.5), (2, 25, 0.5)),
          ((1, 40), (5, 50), (0.2, 20), (1, 40))),
    *_gas("voc", "VOC", "ppm", (200, 1000, 100, 400),
          ((50, 600, 10), (300, 1900, 50), (20, 300, 10), (100, 900, 10)),
          ((20, 1500), (100, 2000), (10, 800), (50, 1500))),
    *_gas("co", "CO", "ppm", (50, 200, 20, 80),
          ((10, 150, 5), (60, 380, 10), (5, 60, 1), (20, 180, 5)),
          ((5, 300), (30, 400), (2, 150), (10, 300))),
    _p("fire.w_h2", "fire_mix", "H2 가중치", "", 0.38, (0.2, 0.6, 0.01), (0.2, 1), "위험지수에서 H2 몫"),
    _p("fire.w_voc", "fire_mix", "VOC 가중치", "", 0.38, (0.2, 0.6, 0.01), (0.2, 1), "위험지수에서 VOC 몫"),
    _p("fire.w_co", "fire_mix", "CO 가중치", "", 0.32, (0.2, 0.6, 0.01), (0.2, 1), "위험지수에서 CO 몫"),
    _p("fire.w_temp", "fire_mix", "온도 가중치", "", 0.10, (0, 0.3, 0.01), (0, 0.5), "함내온도 급상승 확증 몫"),
    _p("fire.pair_boost", "fire_mix", "동반 상승 가산", "", 0.20, (0, 0.4, 0.01), (0, 0.5),
       "가스 두 종 이상이 같이 오를 때 더하는 몫"),
    _p("fire.conf_boost", "fire_mix", "확증 가산", "", 0.15, (0, 0.4, 0.01), (0, 0.5),
       "연기·접점 위험이 함께일 때 더하는 몫"),
    _p("fire.temp_rise_warn", "fire_mix", "온도 상승 주의", "°C/분", 1.0, (0.3, 3, 0.1), (0.1, 5),
       "함내온도 분당 상승 주의"),
    _p("fire.temp_rise_alarm", "fire_mix", "온도 상승 위험", "°C/분", 5.0, (2, 9, 0.5), (1, 10),
       "함내온도 분당 상승 위험"),
    _p("fire.z_lo", "fire_mix", "평소대비 이상 시작", "z", 3.0, (2, 5, 0.1), (1.5, 8), "평소 대비 튐 판정 시작점"),
    _p("fire.z_hi", "fire_mix", "평소대비 이상 최대", "z", 6.0, (4, 9, 0.1), (3, 10), "평소 대비 튐 최대점"),
    _p("fire.watch_fri", "fire_stage", "주의 단계", "FRI", 30, (15, 45, 1), (10, 50), "화재 '주의' 시작"),
    _p("fire.open_fri", "fire_stage", "벤트 열림(위험)", "FRI", 55, (35, 68, 1), (30, 70),
       "화재 '위험' 시작 — 벤트 자동 개방"),
    _p("fire.close_fri", "fire_stage", "벤트 닫힘", "FRI", 20, (5, 28, 1), (0, 30),
       "이 아래로 유지되면 벤트 닫힘(히스테리시스)"),
    _p("fire.crit_fri", "fire_stage", "극한 단계", "FRI", 80, (65, 88, 1), (60, 90), "화재 '극한' 시작"),
    _p("fire.over_hold", "fire_stage", "경보치 초과 지속", "초", 60, (0, 120, 5), (0, 120),
       "가스가 경보치를 이만큼 계속 넘어야 강제 '극한'(순간 튐 배제). 한계 120초 — 더 늦추면 안전장치가 무뎌짐"),
    _p("contact.i_min", "contact", "최소 부하 전류", "A", 2.0, (0.5, 4, 0.1), (0.2, 5),
       "이 전류 미만은 발열이 미미해 판정 제외"),
    _p("contact.res_warn", "contact", "초과발열 주의", "°C", 5.0, (2, 9, 0.5), (1, 15),
       "같은 전류 대비 초과 발열 주의"),
    _p("contact.res_alarm", "contact", "초과발열 위험", "°C", 11.0, (6, 18, 0.5), (3, 20),
       "같은 전류 대비 초과 발열 위험"),
    _p("contact.t_abs_alarm", "contact", "절대온도 위험", "°C", 60.0, (45, 75, 1), (40, 80), "접점 절대온도 위험"),
    _p("contact.rise_warn", "contact", "열화 추세 주의", "°C/분", 0.3, (0.1, 0.8, 0.05), (0.05, 1),
       "초과 발열의 분당 상승 주의"),
    _p("dew.margin_warn", "dew", "결로 여유 주의", "°C", 3.0, (1.5, 6, 0.1), (1, 8), "표면−이슬점 여유 주의"),
    _p("dew.margin_alarm", "dew", "결로 여유 위험", "°C", 1.0, (0.5, 2.5, 0.1), (0.5, 4),
       "표면−이슬점 여유 위험 — 히터·팬"),
    _p("dew.rh_high", "dew", "고습 주의", "%", 80.0, (65, 88, 1), (60, 90), "이 습도 이상은 여유와 무관히 주의"),
    _p("dew.fall_warn", "dew", "여유 감소 주의", "°C/분", 0.4, (0.1, 1, 0.05), (0.05, 2),
       "여유가 분당 이만큼 좁아지면 추세 주의"),
    _p("dew.horizon_min", "dew", "예측 시간", "분", 10.0, (5, 30, 1), (5, 60),
       "추세대로면 이 시간 안에 임박할 때만 주의"),
    _p("dew.trend_cap", "dew", "추세 무시 여유", "°C", 6.0, (3, 10, 0.5), (2, 15),
       "여유가 이보다 넉넉하면 추세만으로는 경보 안 함"),
]

_BY_KEY = {p["key"]: p for p in PARAMS}

# (a, "<", b): a는 b보다 작아야 한다
RELATIONS: list = [
    *[(f"fire.{g}.{a}", "<", f"fire.{g}.{b}") for g in ("h2", "voc", "co")
      for a, b in (("warn", "alarm"), ("rise_warn", "rise_alarm"))],
    ("fire.temp_rise_warn", "<", "fire.temp_rise_alarm"),
    ("fire.z_lo", "<", "fire.z_hi"),
    ("fire.close_fri", "<", "fire.watch_fri"),
    ("fire.watch_fri", "<", "fire.open_fri"),
    ("fire.open_fri", "<", "fire.crit_fri"),
    ("contact.res_warn", "<", "contact.res_alarm"),
    ("dew.margin_alarm", "<", "dew.margin_warn"),
]


def defaults() -> dict:
    return {p["key"]: p["default"] for p in PARAMS}


def validate(params) -> str | None:
    """적용해도 되는 설정인가. None=통과, 문자열=거부 사유(한국어)."""
    if not isinstance(params, dict):
        return "설정 형식 오류 — 키:값 묶음이 아님"
    unknown = sorted(k for k in params if k not in _BY_KEY)
    if unknown:
        return f"모르는 키: {', '.join(unknown[:3])}"
    missing = [p["key"] for p in PARAMS if p["key"] not in params]
    if missing:
        return f"값 빠짐: {', '.join(missing[:3])}"
    for p in PARAMS:
        v = params[p["key"]]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return f"{p['label']}({p['key']}) 값이 숫자가 아님"
        lo, hi = p["hard"]
        if not lo <= v <= hi:
            return f"{p['label']} {v}{p['unit']} — 절대 한계 {lo:g}~{hi:g} 밖"
    for a, _, b in RELATIONS:
        if not params[a] < params[b]:
            return (f"{_BY_KEY[a]['label']}({params[a]:g}) < {_BY_KEY[b]['label']}({params[b]:g}) 이어야 함")
    return None


def _target(fire, contact, dew, key):
    """'fire.h2.warn' → (fire.h2 객체, 'warn')."""
    parts = key.split(".")
    obj = {"fire": fire, "contact": contact, "dew": dew}[parts[0]]
    for name in parts[1:-1]:
        obj = getattr(obj, name)
    return obj, parts[-1]


def from_cfgs(fire, contact, dew) -> dict:
    out = {}
    for p in PARAMS:
        obj, attr = _target(fire, contact, dew, p["key"])
        out[p["key"]] = float(getattr(obj, attr))
    return out


def apply_to(fire, contact, dew, params: dict) -> None:
    """설정 객체에 제자리로 대입한다 — 같은 객체를 참조하는 컨트롤러(벤트·접점 기준)가 상태를
    유지한 채 새 기준을 보게 된다. 호출 전에 validate()를 통과시켜야 한다."""
    for p in PARAMS:
        obj, attr = _target(fire, contact, dew, p["key"])
        setattr(obj, attr, float(params[p["key"]]))


def registry_view() -> dict:
    """화면·API용 JSON."""
    return {"params": [dict(p, ui=list(p["ui"]), hard=list(p["hard"])) for p in PARAMS],
            "relations": [list(r) for r in RELATIONS]}
