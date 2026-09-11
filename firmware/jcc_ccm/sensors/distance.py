"""내장 거리 센서 (도어 개폐 감지).

Turck IM18-CCM50의 /home/scripts/distance_read.sh 를 호출한다 (매뉴얼 7.4).
도어가 열리면 거리값이 크게 변하므로, 상위(서버)에서 임계값으로 개폐를 판정한다.
여기서는 원시 거리(mm)만 올린다 — 판정 로직을 장비에 박지 않아 현장별 튜닝이 쉽다.
"""
from __future__ import annotations

import re
import subprocess

from . import Reading
from ..config import SensorConfig

DISTANCE_SCRIPT = "/home/scripts/distance_read.sh"
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


class DistanceDriver:
    def __init__(self, cfg: SensorConfig, script_path: str = DISTANCE_SCRIPT):
        self._cfg = cfg
        self._script = script_path

    def read(self) -> Reading:
        cfg = self._cfg
        try:
            proc = subprocess.run(
                [self._script, "1"],   # 1 sample
                capture_output=True, text=True, timeout=10,
            )
        except FileNotFoundError:
            return Reading.failure(cfg, f"스크립트 없음: {self._script} (CCM 실기에서만 존재)")
        except subprocess.TimeoutExpired:
            return Reading.failure(cfg, "distance_read.sh 응답 시간 초과")
        except Exception as exc:  # noqa: BLE001
            return Reading.failure(cfg, f"실행 오류: {exc}")

        if proc.returncode != 0:
            return Reading.failure(cfg, f"스크립트 오류(rc={proc.returncode}): {proc.stderr.strip()}")

        m = _NUMBER.search(proc.stdout)
        if not m:
            return Reading.failure(cfg, f"숫자를 못 찾음: {proc.stdout.strip()[:80]!r}")
        return Reading.success(cfg, float(m.group()))
