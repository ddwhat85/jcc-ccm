"""수동 센서 지정 규격 — 자동 탐색에 안 잡힌 센서를 사람이 직접 지정할 때의 형식과 검사.

서버(입력 받을 때)와 CCM(설정 받을 때)이 **같은 규칙**으로 검사한다. 오타 하나로 엉뚱한
장비를 두드리거나 CCM이 죽지 않게, 범위를 벗어난 값은 양쪽 다 거부한다.
정본은 여기, 펌웨어 사본은 tools/sync_edge_core.py로만 복사한다(손으로 고치지 않는다).

형식(dict)
  key       센서 키(영문 소문자·숫자·_ 2~40자, CCM 안에서 유일)
  name      표시 이름          unit   단위
  driver    "modbus"(RS485 RTU) | "modbus_tcp"(이더넷)
  slave     Modbus 주소(RTU 1~247, TCP 유닛 ID 0~255)
  register  레지스터 번호 0~65535     type  "input" | "holding"
  datatype  uint16|int16|uint32|int32|float32
  scale, offset   값 = 원값 × scale + offset
  host, port      TCP 전용(IPv4 또는 호스트 이름, 1~65535)
"""
from __future__ import annotations

import math
import re

DRIVERS = ("modbus", "modbus_tcp")
TYPES = ("input", "holding")
DATATYPES = ("uint16", "int16", "uint32", "int32", "float32")
MAX_PER_CCM = 16
_KEY = re.compile(r"^[a-z0-9_]{2,40}$")
_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


def _int(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v):
        raise ValueError(f"{what}는 정수여야 합니다")
    v = int(v)
    if not lo <= v <= hi:
        raise ValueError(f"{what}는 {lo}~{hi} 사이여야 합니다")
    return v


def _num(v, what, nonzero=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 1e6:
        raise ValueError(f"{what}는 ±1,000,000 안의 숫자여야 합니다")
    if nonzero and v == 0:
        raise ValueError(f"{what}는 0일 수 없습니다")
    return float(v)


def clean(spec) -> dict:
    """검사·정규화한 사본을 돌려준다. 틀리면 ValueError(사람이 읽을 이유)."""
    if not isinstance(spec, dict):
        raise ValueError("센서 지정은 객체여야 합니다")
    key = str(spec.get("key", "")).strip()
    if not _KEY.match(key):
        raise ValueError("센서 키는 영문 소문자·숫자·_ 2~40자여야 합니다")
    driver = spec.get("driver")
    if driver not in DRIVERS:
        raise ValueError("연결 방식은 RS485(modbus) 또는 Modbus TCP(modbus_tcp)여야 합니다")
    name = str(spec.get("name", "")).strip()[:40] or key
    out = {
        "key": key, "name": name, "unit": str(spec.get("unit", "")).strip()[:12], "driver": driver,
        "slave": _int(spec.get("slave"), 1 if driver == "modbus" else 0, 247 if driver == "modbus" else 255,
                      "Modbus 주소" if driver == "modbus" else "유닛 ID"),
        "register": _int(spec.get("register", 0), 0, 65535, "레지스터 번호"),
        "type": spec.get("type", "input"),
        "datatype": spec.get("datatype", "uint16"),
        "scale": _num(spec.get("scale", 1.0), "배율", nonzero=True),
        "offset": _num(spec.get("offset", 0.0), "보정값"),
    }
    if out["type"] not in TYPES:
        raise ValueError("레지스터 종류는 input 또는 holding이어야 합니다")
    if out["datatype"] not in DATATYPES:
        raise ValueError(f"형식은 {', '.join(DATATYPES)} 중 하나여야 합니다")
    if driver == "modbus_tcp":
        host = str(spec.get("host", "")).strip()
        if not _HOST.match(host) or ".." in host:
            raise ValueError("IP 주소(또는 호스트 이름)가 올바르지 않습니다")
        out["host"] = host
        out["port"] = _int(spec.get("port", 502), 1, 65535, "포트")
    return out


def clean_list(specs) -> tuple:
    """([정상 사본...], {key 또는 '#순번': 이유}). 키 중복·개수 초과도 거른다."""
    ok, errors, seen = [], {}, set()
    for i, s in enumerate(specs if isinstance(specs, list) else []):
        label = str(s.get("key")) if isinstance(s, dict) and s.get("key") else f"#{i + 1}"
        try:
            c = clean(s)
        except ValueError as exc:
            errors[label] = str(exc)
            continue
        if c["key"] in seen:
            errors[label] = "같은 키가 두 번 있습니다"
            continue
        if len(ok) >= MAX_PER_CCM:
            errors[label] = f"CCM 하나에 수동 센서는 {MAX_PER_CCM}개까지입니다"
            continue
        seen.add(c["key"])
        ok.append(c)
    return ok, errors
