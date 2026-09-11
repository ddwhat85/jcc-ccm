"""센서 드라이버.

모든 드라이버는 SensorDriver 인터페이스를 구현하고 Reading을 돌려준다.
새 센서 종류를 추가할 때 agent는 건드리지 않고 여기에 드라이버만 더한다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from ..config import SensorConfig, ModbusBusConfig


@dataclass
class Reading:
    """센서 1회 측정 결과. 성공/실패를 값으로 표현해 루프가 죽지 않게 한다."""
    key: str
    name: str
    unit: str
    value: float | None                 # 실패 시 None
    ok: bool
    timestamp: float = field(default_factory=time.time)
    error: str = ""

    @classmethod
    def success(cls, cfg: SensorConfig, value: float) -> "Reading":
        return cls(key=cfg.key, name=cfg.name, unit=cfg.unit, value=value, ok=True)

    @classmethod
    def failure(cls, cfg: SensorConfig, error: str) -> "Reading":
        return cls(key=cfg.key, name=cfg.name, unit=cfg.unit, value=None,
                   ok=False, error=error)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "unit": self.unit,
            "value": self.value,
            "ok": self.ok,
            "ts": round(self.timestamp, 3),
            **({"error": self.error} if self.error else {}),
        }


class SensorDriver(Protocol):
    """한 센서를 한 번 읽는다. 예외를 밖으로 던지지 않고 Reading.failure로 감싼다."""
    def read(self) -> Reading: ...


def build_drivers(
    sensors: list[SensorConfig],
    modbus_bus: ModbusBusConfig,
) -> list[SensorDriver]:
    """설정의 센서 목록을 실제 드라이버 인스턴스로 바꾼다.

    modbus 드라이버들은 RS485 버스 하나를 공유하므로, 버스를 한 번만 열어
    나눠 쓰도록 여기서 묶어 만든다.
    """
    # 지연 import: 실기(pyserial/pymodbus 설치)와 개발 PC(미설치)를 분리.
    from .ambient import AmbientDriver
    from .distance import DistanceDriver

    drivers: list[SensorDriver] = []
    modbus_sensors = [s for s in sensors if s.enabled and s.driver == "modbus"]

    for s in sensors:
        if not s.enabled:
            continue
        if s.driver == "ambient":
            drivers.append(AmbientDriver(s))
        elif s.driver == "distance":
            drivers.append(DistanceDriver(s))

    if modbus_sensors:
        from .modbus import ModbusBus, ModbusDriver
        bus = ModbusBus(modbus_bus)
        for s in modbus_sensors:
            drivers.append(ModbusDriver(s, bus))

    return drivers
