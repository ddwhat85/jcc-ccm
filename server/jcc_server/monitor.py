"""하트비트 감시(watchdog).

주기적으로 storage.liveness_scan()을 돌려 CCM/센서가 조용해졌는지 검사한다.
이것이 "알람이 안 뜨면 진짜 안전한 것"을 보장하는 신뢰의 최저선이다 — 값이
정상이라서 조용한 것과, 장비가 죽어서 조용한 것을 구분해 후자를 경보로 올린다.

실기·데모 상관없이 항상 켠다(핵심 안전 기능).
"""
from __future__ import annotations

import os
import threading
import time


def start(storage, interval: float = 10) -> None:
    interval = float(os.environ.get("JCC_MONITOR_INTERVAL") or interval)
    dev_to = float(os.environ.get("JCC_DEVICE_TIMEOUT") or 60)
    sen_to = float(os.environ.get("JCC_SENSOR_TIMEOUT") or 45)

    def loop() -> None:
        # 시작 직후에는 아직 데이터가 안 쌓였을 수 있어 한 박자 쉬고 시작한다.
        time.sleep(interval)
        while True:
            try:
                storage.liveness_scan(device_timeout=dev_to, sensor_timeout=sen_to)
                storage.health_scan(sensor_timeout=sen_to)   # 고착·드리프트 감지
            except Exception:  # noqa: BLE001 - 감시는 죽지 않아야 한다
                pass
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True).start()
