"""출력(릴레이) 드라이버 — 벤트·히터·팬.

엣지 예지가 판정한 상태를 실제 릴레이로 내보낸다. 모든 드라이버는 set(on)으로
켜고/끄며, 실패는 예외 대신 False로 알린다(루프가 죽지 않고 다음 주기에 재시도).

배선 권장(안전측): 벤트는 '전원이 끊기면 열리는' 방향으로 배선한다(NC 접점 또는
스프링 리턴 댐퍼). 소프트웨어 fail-safe는 가스 센서를 잃었을 때만 다루고, CCM 자체의
정전·고장은 하드웨어 배선이 책임진다.
"""
from __future__ import annotations

import logging
from typing import Protocol

from .config import ActuatorConfig, ModbusBusConfig

log = logging.getLogger("jcc_ccm.actuator")

_ON_WORD = {"vent": ("개방", "닫힘"), "heater": ("가동", "정지"), "fan": ("가동", "정지")}


def on_word(kind: str, on: bool) -> str:
    yes, no = _ON_WORD.get(kind, ("켜짐", "꺼짐"))
    return yes if on else no


class Actuator(Protocol):
    kind: str
    state: bool | None       # 마지막으로 '성공적으로' 쓴 상태(모르면 None)

    def set(self, on: bool) -> bool: ...


class LogActuator:
    """실기 출력 없이 로그만 남긴다 — 시운전(배선 전), 시뮬레이션용."""

    def __init__(self, cfg: ActuatorConfig):
        self.kind = cfg.kind
        self.state: bool | None = None
        self.writes: list[bool] = []

    def set(self, on: bool) -> bool:
        self.writes.append(bool(on))
        self.state = bool(on)
        log.info("[출력·로그] %s → %s", self.kind, on_word(self.kind, on))
        return True


class ModbusCoilActuator:
    """Modbus RTU 릴레이 모듈의 코일 1개. 센서와 같은 RS485 버스를 공유한다."""

    def __init__(self, cfg: ActuatorConfig, bus_cfg: ModbusBusConfig):
        from .sensors.modbus import shared_bus
        self.kind = cfg.kind
        self.state: bool | None = None
        self._cfg = cfg
        self._bus = shared_bus(bus_cfg)

    def set(self, on: bool) -> bool:
        coil = (not on) if self._cfg.invert else bool(on)
        try:
            self._bus.write_coil(self._cfg.modbus_slave, self._cfg.modbus_coil, coil)
        except ModuleNotFoundError:
            log.error("pymodbus 미설치 — %s 출력을 쓸 수 없습니다", self.kind)
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning("%s 출력 쓰기 실패(다음 주기 재시도): %s", self.kind, exc)
            return False
        self.state = bool(on)
        log.info("[출력] %s → %s (slave %d, coil %d)", self.kind, on_word(self.kind, on),
                 self._cfg.modbus_slave, self._cfg.modbus_coil)
        return True


def build_actuators(cfgs: list[ActuatorConfig], bus_cfg: ModbusBusConfig,
                    force_log: bool = False) -> dict:
    """{kind: Actuator}. force_log=True면 실기 출력 대신 전부 로그(시뮬레이션)."""
    out = {}
    for a in cfgs:
        if force_log or a.driver == "log":
            out[a.kind] = LogActuator(a)
        else:
            out[a.kind] = ModbusCoilActuator(a, bus_cfg)
    return out
