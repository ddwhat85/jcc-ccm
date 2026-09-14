"""내장 센서 공통 로직.

Turck IM18-CCM50이 기본 제공하는 /home/scripts 의 셸 스크립트를 실행하고,
출력에서 첫 번째 실수를 뽑아 Reading으로 감싼다. ambient_read.sh / distance_read.sh
등이 모두 같은 방식이라, 실행·파싱·예외처리를 여기 한 곳에 둔다.

출력 형식은 펌웨어 버전마다 다를 수 있어 특정 포맷을 가정하지 않고 첫 실수를 뽑는다.
실기에서 형식을 확인하면 파서를 좁힌다.
"""
from __future__ import annotations

import re
import subprocess

from . import Reading
from ..config import SensorConfig

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def read_script_number(cfg: SensorConfig, script: str, args: list[str],
                       timeout: float = 10.0) -> Reading:
    """script를 args와 함께 실행하고 출력의 첫 숫자를 값으로 하는 Reading을 돌려준다.

    실패는 예외로 던지지 않고 Reading.failure로 감싼다(수집 루프를 지키기 위해).
    """
    try:
        proc = subprocess.run(
            [script, *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        return Reading.failure(cfg, f"스크립트 없음: {script} (CCM 실기에서만 존재)")
    except subprocess.TimeoutExpired:
        return Reading.failure(cfg, f"{script} 응답 시간 초과")
    except Exception as exc:  # noqa: BLE001 - 루프를 지키려 모든 예외를 값으로
        return Reading.failure(cfg, f"실행 오류: {exc}")

    if proc.returncode != 0:
        return Reading.failure(cfg, f"스크립트 오류(rc={proc.returncode}): {proc.stderr.strip()}")

    m = _NUMBER.search(proc.stdout)
    if not m:
        return Reading.failure(cfg, f"숫자를 못 찾음: {proc.stdout.strip()[:80]!r}")
    return Reading.success(cfg, float(m.group()))
