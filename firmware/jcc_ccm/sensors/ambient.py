"""내장 온습도 센서.

Turck IM18-CCM50이 기본 제공하는 /home/scripts/ambient_read.sh 를 호출한다
(매뉴얼 7.4 예제 스크립트). 실행·파싱은 script_sensor 공통 헬퍼가 담당한다.
"""
from __future__ import annotations

from . import Reading
from .script_sensor import read_script_number
from ..config import SensorConfig

AMBIENT_SCRIPT = "/home/scripts/ambient_read.sh"


class AmbientDriver:
    def __init__(self, cfg: SensorConfig, script_path: str = AMBIENT_SCRIPT):
        self._cfg = cfg
        self._script = script_path

    def read(self) -> Reading:
        # metric: temp | hum | all — config에서 검증됨
        return read_script_number(self._cfg, self._script, [self._cfg.metric])
