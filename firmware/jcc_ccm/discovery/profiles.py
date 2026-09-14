"""센서 프로파일 라이브러리 (제품 지식 베이스).

자동 탐색이 찾아낸 장비의 '지문'을 실제 제품 정보로 바꾼다 — 브랜드·제품명·품번·
설정 매뉴얼·알람 기준값·단위까지. 이것이 "AI가 알아서 찾아준다"의 실체다:
탐색이 센서를 식별하면 여기 등록된 제품 사양을 자동으로 인스펙터에 채운다.

BOM(인터배터리 전시 제품 List)의 실제 센서들로 구성. 새 센서는 여기에 한 줄 추가하면
전 시스템이 그 제품을 인식한다.
"""
from __future__ import annotations


# 한 장비가 여러 채널(예: 온습도=온도+습도)을 낼 수 있어 emits는 리스트.
# 각 emit: key·name·unit·kind + 알람 기준값(alarm_min/max).
PROFILES = [
    {
        "ident": "TURCK-CCM-AMBIENT", "brand": "Turck", "product": "IM18-CCM50 내장 온습도",
        "part_no": "100022405", "manual": "CCM 내장 센서. 별도 배선 없이 함내 온습도를 측정. 보정 불필요.",
        "emits": [
            {"key": "cabinet_temp",     "name": "함내 온도", "unit": "C",   "kind": "temp",
             "alarm_min": 5,  "alarm_max": 45},
            {"key": "cabinet_humidity", "name": "함내 습도", "unit": "%RH", "kind": "humidity",
             "alarm_min": 20, "alarm_max": 80},
        ],
    },
    {
        "ident": "TURCK-CCM-DOOR", "brand": "Turck", "product": "IM18-CCM50 내장 거리센서",
        "part_no": "100022405", "manual": "CCM 내장 ToF 거리센서. 도어 개폐를 거리(mm)로 감지. 닫힘 기준거리 캘리브레이션.",
        "emits": [{"key": "door_distance", "name": "도어 개폐", "unit": "mm", "kind": "door",
                   "alarm_min": 0, "alarm_max": 300}],
    },
    {
        "ident": "BANNER-QM30VT", "brand": "Banner Engineering", "product": "QM30VT2 진동·온도 센서",
        "part_no": "806276", "manual": "2m 케이블. RMS 속도(mm/s)와 온도 출력. 설치축·감도 파라미터로 설정. 베어링·모터 이상진동 감시용.",
        "emits": [{"key": "vibration", "name": "진동", "unit": "mm/s", "kind": "vibration",
                   "alarm_min": 0, "alarm_max": 4.0}],
    },
    {
        "ident": "BANNER-CT20A", "brand": "Banner Engineering", "product": "S15C-CT20A-MQ 전류센서",
        "part_no": "814928", "manual": "20A CT 관통형. 메인차단기 전류를 비접촉 측정. 정격 20A, 관통 전선 1가닥.",
        "emits": [{"key": "main_current", "name": "메인차단기 전류", "unit": "A", "kind": "current",
                   "alarm_min": 0, "alarm_max": 30}],
    },
    {
        "ident": "BANNER-S15S-T", "brand": "Banner Engineering", "product": "S15S-T-MQ 비접촉 온도센서",
        "part_no": "813163", "manual": "적외선 비접촉 온도. 대상 방사율 설정 필요. 접점·단자 발열 감시용.",
        "emits": [{"key": "ncontact_temp", "name": "비접촉 온도", "unit": "C", "kind": "temp",
                   "alarm_min": 5, "alarm_max": 60}],
    },
    {
        "ident": "INFRASENSING-H2", "brand": "InfraSensing", "product": "Hydrogen (H2) Sensor",
        "part_no": "H2-LEL", "manual": "0–100% LEL 수소 감지(보정 불요형). ESS 화재 전조. 4~20mA. 정기 기능시험 권장.",
        "emits": [{"key": "h2_lel", "name": "수소 농도", "unit": "%LEL", "kind": "h2",
                   "alarm_min": 0, "alarm_max": 2.0}],
    },
    {
        "ident": "ONOFF-HSD200", "brand": "온오프시스템", "product": "HSD200 열·연기 감지기",
        "part_no": "HSD200", "manual": "열+연기 복합 감지. 접점 출력. 천장부 설치. 정기 청소·시험.",
        "emits": [{"key": "smoke", "name": "열연기", "unit": "", "kind": "smoke",
                   "alarm_min": 0, "alarm_max": 1}],
    },
]

PROFILE_BY_IDENT = {p["ident"]: p for p in PROFILES}

# CCM 게이트웨이 자체 제품 정보 (노드 인스펙터용)
CCM_PRODUCT = {
    "brand": "Turck", "product": "IM18-CCM50-MTI/24VDC", "part_no": "100022405",
    "manual": "Debian 리눅스 컨디션 모니터링 게이트웨이. RS485/CAN·아날로그·디지털 I/O. "
              "SSH 포트 1522. 자체 커넥터로 클라우드 전송. 24VDC 공급.",
}


def identify(ident: str):
    """모델 지문으로 프로파일을 찾는다. 없으면 None."""
    return PROFILE_BY_IDENT.get(ident)


def infer(raw: dict) -> dict:
    """프로파일에 없는 장비를 값 범위로 추정한다(AI 몫). 항상 confidence='추정'."""
    addr = raw.get("address")
    v = raw.get("probe")
    key = "unknown_" + str(addr if addr is not None else "x")
    if v is None:
        guess, unit, kind, amax = "미확인 신호", "", "unknown", 100
    elif 0 <= v <= 1.5:
        guess, unit, kind, amax = "미확인 (가스/비율 추정)", "%", "ratio", 2.0
    elif 15 <= v <= 35:
        guess, unit, kind, amax = "미확인 (온도 추정)", "C", "temp", 45
    elif 0 <= v <= 100:
        guess, unit, kind, amax = "미확인 (압력/비율 추정)", "?", "pressure", 100
    else:
        guess, unit, kind, amax = "미확인 (전류/전압 추정)", "?", "analog", 100
    return {"key": key, "name": guess, "unit": unit, "kind": kind,
            "alarm_min": 0, "alarm_max": amax,
            "brand": "미상", "product": "미확인 장비", "part_no": "",
            "manual": "라이브러리에 없는 장비. 값 범위로 종류를 추정함. 실기에서 모델 확인 후 프로파일 등록 권장."}
