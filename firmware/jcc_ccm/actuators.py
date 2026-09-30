"""출력(릴레이) 드라이버 — 벤트·히터·팬.

엣지 예지가 판정한 상태를 실제 릴레이로 내보낸다. 모든 드라이버는 set(on)으로
켜고/끄며, 실패는 예외 대신 False로 알린다(루프가 죽지 않고 다음 주기에 재시도).

배선 권장(안전측): 벤트는 '전원이 끊기면 열리는' 방향으로 배선한다(NC 접점 또는
스프링 리턴 댐퍼). 소프트웨어 fail-safe는 가스 센서를 잃었을 때만 다루고, CCM 자체의
정전·고장은 하드웨어 배선이 책임진다.
"""
from __future__ import annotations

import logging
import time
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
    def read_state(self) -> bool | None:
        """릴레이(코일)를 되읽은 논리 상태. 읽을 수 없으면 None."""
    def read_position(self) -> bool | None:
        """위치 스위치: 열림(켜짐)=True. 읽을 수 없으면 None."""


class LogActuator:
    """실기 출력 없이 로그만 남긴다 — 시운전(배선 전), 시뮬레이션용.

    작동 확인을 시험할 수 있게 가상의 릴레이·구동기를 흉내 낸다(고장 주입):
      sim_stuck     : None=정상, True/False = 구동기가 그 위치에 걸림(명령과 무관)
      sim_delay     : 명령 후 실제 위치가 따라오기까지 걸리는 초
      sim_read_fail : 읽기(통신) 실패
      sim_coil_reset(): 릴레이 모듈 재부팅 — 코일이 풀림(꺼짐)
    """

    def __init__(self, cfg: ActuatorConfig):
        self.kind = cfg.kind
        self.state: bool | None = None
        self.writes: list[bool] = []
        self.clock = time.time
        self.sim_stuck: bool | None = None
        self.sim_delay = 0.0
        self.sim_read_fail = False
        self._coil: bool | None = None
        self._coil_at = 0.0
        self._phys_prev: bool | None = None

    def set(self, on: bool) -> bool:
        self.writes.append(bool(on))
        self._phys_prev = self._physical()
        self._coil, self._coil_at = bool(on), self.clock()
        self.state = bool(on)
        log.info("[출력·로그] %s → %s", self.kind, on_word(self.kind, on))
        return True

    def sim_coil_reset(self) -> None:
        self._phys_prev = self._physical()
        self._coil, self._coil_at = False, self.clock()

    def _physical(self) -> bool | None:
        if self.sim_stuck is not None:
            return self.sim_stuck
        if self._coil is None:
            return None
        return self._coil if self.clock() - self._coil_at >= self.sim_delay else self._phys_prev

    def read_state(self) -> bool | None:
        return None if self.sim_read_fail else self._coil

    def read_position(self) -> bool | None:
        return None if self.sim_read_fail else self._physical()


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

    def read_state(self) -> bool | None:
        try:
            v = self._bus.read_bits(self._cfg.modbus_slave, self._cfg.modbus_coil, "coil")
        except Exception as exc:  # noqa: BLE001 - 통신 문제는 '모름'(고장으로 단정하지 않는다)
            log.debug("%s 코일 되읽기 실패: %s", self.kind, exc)
            return None
        return (not v) if self._cfg.invert else v

    def read_position(self) -> bool | None:
        c = self._cfg
        if c.feedback_input < 0:
            return None
        slave = c.feedback_slave if c.feedback_slave >= 0 else c.modbus_slave
        try:
            v = self._bus.read_bits(slave, c.feedback_input, "discrete")
        except Exception as exc:  # noqa: BLE001
            log.debug("%s 위치 스위치 읽기 실패: %s", self.kind, exc)
            return None
        return (not v) if c.feedback_invert else v


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
