"""센서 프로파일 라이브러리.

자동 탐색이 버스에서 찾아낸 장비의 '지문'(모델 식별 문자열 등)을 실제 센서 정체
(이름·단위·종류·읽을 채널)로 바꾸는 지식 베이스. BOM의 실제 센서들로 구성한다.

실기에서 Modbus 장비 식별(기능코드 0x2B/0x0E "Read Device Identification")이나
슬레이브 ID 조회로 얻은 모델 문자열이 여기 ident와 매칭된다. 매칭되면 '확정',
매칭 안 되면 infer()가 값 범위로 종류를 '추정'한다 — 이 추정이 AI 몫이다.
"""
from __future__ import annotations


# 한 장비가 여러 채널(예: 온습도 = 온도+습도)을 낼 수 있어 emits는 리스트다.
PROFILES = [
    {
        "ident": "TURCK-CCM-AMBIENT",          # CCM 내장 온습도
        "vendor": "Turck 내장",
        "emits": [
            {"key": "cabinet_temp",     "name": "함내 온도", "unit": "C",   "kind": "temp"},
            {"key": "cabinet_humidity", "name": "함내 습도", "unit": "%RH", "kind": "humidity"},
        ],
    },
    {
        "ident": "TURCK-CCM-DOOR",             # CCM 내장 거리센서(도어 개폐)
        "vendor": "Turck 내장",
        "emits": [{"key": "door_distance", "name": "도어 개폐", "unit": "mm", "kind": "door"}],
    },
    {
        "ident": "BANNER-QM30VT",              # 진동센서 (BOM 806276)
        "vendor": "Banner",
        "emits": [{"key": "vibration", "name": "진동", "unit": "mm/s", "kind": "vibration"}],
    },
    {
        "ident": "BANNER-CT20A",               # CT센서 20A (BOM 814928)
        "vendor": "Banner",
        "emits": [{"key": "main_current", "name": "메인차단기 전류", "unit": "A", "kind": "current"}],
    },
    {
        "ident": "BANNER-S15S-T",              # 비접촉 온도센서 (BOM 813163)
        "vendor": "Banner",
        "emits": [{"key": "ncontact_temp", "name": "비접촉 온도", "unit": "C", "kind": "temp"}],
    },
    {
        "ident": "INFRASENSING-H2",            # 수소 & VOC 센서 (BOM: JCC Scope)
        "vendor": "InfraSensing",
        "emits": [{"key": "h2_lel", "name": "수소 농도", "unit": "%LEL", "kind": "h2"}],
    },
    {
        "ident": "ONOFF-HSD200",               # 열연기 감지기 (BOM: JCC Scope)
        "vendor": "온오프시스템",
        "emits": [{"key": "smoke", "name": "열연기", "unit": "", "kind": "smoke"}],
    },
]

PROFILE_BY_IDENT = {p["ident"]: p for p in PROFILES}


def identify(ident: str):
    """모델 지문으로 프로파일을 찾는다. 없으면 None."""
    return PROFILE_BY_IDENT.get(ident)


def infer(raw: dict) -> dict:
    """프로파일에 없는 장비를 값 범위로 추정한다(AI 몫). 항상 confidence='추정'.

    실기에서는 레지스터 패턴·값 범위·변화율 등으로 더 정교하게 추정할 수 있다.
    여기서는 단일 프로브 값의 범위로 대략의 종류를 제안한다.
    """
    addr = raw.get("address")
    v = raw.get("probe")
    key = "unknown_" + str(addr if addr is not None else "x")
    if v is None:
        guess, unit, kind = "미확인 신호", "", "unknown"
    elif 0 <= v <= 1.5:
        guess, unit, kind = "미확인 (가스/비율 추정)", "%", "ratio"
    elif 15 <= v <= 35:
        guess, unit, kind = "미확인 (온도 추정)", "C", "temp"
    elif 0 <= v <= 100:
        guess, unit, kind = "미확인 (압력/비율 추정)", "?", "pressure"
    else:
        guess, unit, kind = "미확인 (전류/전압 추정)", "?", "analog"
    return {"key": key, "name": guess, "unit": unit, "kind": kind}
