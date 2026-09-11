"""내장 온습도 센서.

Turck IM18-CCM50이 기본 제공하는 /home/scripts/ambient_read.sh 를 호출한다
(매뉴얼 7.4 예제 스크립트). 스크립트 출력에서 숫자를 뽑아 Reading으로 감싼다.

스크립트 출력 형식은 펌웨어 버전마다 다를 수 있으므로, 특정 포맷을 가정하지 않고
출력에서 첫 번째 실수를 추출한다. 실기에서 형식을 확인하면 파서를 좁힌다.
"""
from __future__ import annotations

import re
import subprocess

from . import Reading
from ..config import SensorConfig

AMBIENT_SCRIPT = "/home/scripts/ambient_read.sh"
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


class AmbientDriver:
    def __init__(self, cfg: SensorConfig, script_path: str = AMBIENT_SCRIPT):
        self._cfg = cfg
        self._script = script_path

    def read(self) -> Reading:
        cfg = self._cfg
        try:
            proc = subprocess.run(
                [self._script, cfg.metric],
                capture_output=True, text=True, timeout=10,
            )
        except FileNotFoundError:
            return Reading.failure(cfg, f"스크립트 없음: {self._script} (CCM 실기에서만 존재)")
        except subprocess.TimeoutExpired:
            return Reading.failure(cfg, "ambient_read.sh 응답 시간 초과")
        except Exception as exc:  # noqa: BLE001 - 루프를 지키려 모든 예외를 값으로
            return Reading.failure(cfg, f"실행 오류: {exc}")

        if proc.returncode != 0:
            return Reading.failure(cfg, f"스크립트 오류(rc={proc.returncode}): {proc.stderr.strip()}")

        m = _NUMBER.search(proc.stdout)
        if not m:
            return Reading.failure(cfg, f"숫자를 못 찾음: {proc.stdout.strip()[:80]!r}")
        return Reading.success(cfg, float(m.group()))
