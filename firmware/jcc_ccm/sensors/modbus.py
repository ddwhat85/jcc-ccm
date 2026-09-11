"""외부 센서 (Banner 등) — RS485 + Modbus RTU.

CCM의 CAN/RS485 포트로 연결된 센서를 표준 Modbus RTU로 읽는다.
RS485는 멀티드롭 버스라 여러 센서가 한 선을 공유하므로, ModbusBus가 포트를
한 번만 열고 각 ModbusDriver가 슬레이브 주소로 나눠 읽는다.

Modbus는 공개 표준(Modbus Organization)이다. 특정 벤더 코드에 의존하지 않는다.
"""
from __future__ import annotations

import struct
import threading

from . import Reading
from ..config import SensorConfig, ModbusBusConfig


class ModbusBus:
    """pyserial+pymodbus 위의 얇은 래퍼. 스레드 안전하게 버스를 직렬화한다."""

    def __init__(self, cfg: ModbusBusConfig):
        self._cfg = cfg
        self._lock = threading.Lock()
        self._client = None  # 지연 연결

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        # 지연 import: 개발 PC엔 pymodbus가 없어도 이 파일 import는 가능해야 한다.
        from pymodbus.client import ModbusSerialClient

        cfg = self._cfg
        self._client = ModbusSerialClient(
            port=cfg.port,
            baudrate=cfg.baudrate,
            parity=cfg.parity,
            stopbits=cfg.stopbits,
            bytesize=cfg.bytesize,
            timeout=cfg.timeout_seconds,
        )
        self._client.connect()
        return self._client

    def read_registers(self, slave: int, address: int, count: int, kind: str) -> list[int]:
        """input/holding 레지스터를 읽어 워드 리스트로 돌려준다. 실패 시 예외."""
        with self._lock:
            client = self._ensure_client()
            if kind == "input":
                rr = client.read_input_registers(address=address, count=count, slave=slave)
            else:
                rr = client.read_holding_registers(address=address, count=count, slave=slave)
            if rr.isError():
                raise IOError(f"Modbus 오류 slave={slave} addr={address}: {rr}")
            return list(rr.registers)

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                finally:
                    self._client = None


# datatype -> 필요한 16bit 워드 수
_WORDS = {"uint16": 1, "int16": 1, "uint32": 2, "int32": 2, "float32": 2}


def _decode(regs: list[int], datatype: str) -> float:
    """워드 리스트를 지정 타입의 숫자로 해석한다 (빅엔디안 워드 순서)."""
    if datatype in ("uint16", "int16"):
        raw = regs[0]
        if datatype == "int16" and raw >= 0x8000:
            raw -= 0x10000
        return float(raw)
    packed = struct.pack(">HH", regs[0], regs[1])
    if datatype == "uint32":
        return float(struct.unpack(">I", packed)[0])
    if datatype == "int32":
        return float(struct.unpack(">i", packed)[0])
    if datatype == "float32":
        return float(struct.unpack(">f", packed)[0])
    raise ValueError(f"알 수 없는 datatype: {datatype}")


class ModbusDriver:
    def __init__(self, cfg: SensorConfig, bus: ModbusBus):
        self._cfg = cfg
        self._bus = bus

    def read(self) -> Reading:
        cfg = self._cfg
        count = _WORDS.get(cfg.modbus_datatype)
        if count is None:
            return Reading.failure(cfg, f"지원하지 않는 datatype: {cfg.modbus_datatype}")
        try:
            regs = self._bus.read_registers(
                slave=cfg.modbus_slave,
                address=cfg.modbus_register,
                count=count,
                kind=cfg.modbus_type,
            )
            raw = _decode(regs, cfg.modbus_datatype)
        except ModuleNotFoundError:
            return Reading.failure(cfg, "pymodbus 미설치 (CCM 실기에서 설치)")
        except Exception as exc:  # noqa: BLE001
            return Reading.failure(cfg, f"Modbus 읽기 실패: {exc}")

        value = raw * cfg.scale + cfg.offset
        return Reading.success(cfg, value)
