"""내장 거리 센서 (도어 개폐 감지).

Turck IM18-CCM50의 /home/scripts/distance_read.sh 를 호출한다 (매뉴얼 7.4).
원시 거리(mm)만 올리고, 개폐 판정은 상위(서버)에서 임계값으로 한다 —
판정 로직을 장비에 박지 않아 현장별 튜닝이 쉽다.
"""
from __future__ import annotations

from . import Reading
from .script_sensor import read_script_number
from ..config import SensorConfig

DISTANCE_SCRIPT = "/home/scripts/distance_read.sh"


class DistanceDriver:
    def __init__(self, cfg: SensorConfig, script_path: str = DISTANCE_SCRIPT):
        self._cfg = cfg
        self._script = script_path

    def read(self) -> Reading:
        return read_script_number(self._cfg, self._script, ["1"])  # 1 sample
