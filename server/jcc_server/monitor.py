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
    escalate_after = float(os.environ.get("JCC_ESCALATE_AFTER") or 120)
    from .notify import dispatch

    keep_readings = float(os.environ.get("JCC_KEEP_READING_DAYS") or 14)
    keep_events = float(os.environ.get("JCC_KEEP_EVENT_DAYS") or 90)

    def loop() -> None:
        # 시작 직후에는 아직 데이터가 안 쌓였을 수 있어 한 박자 쉬고 시작한다.
        time.sleep(interval)
        last_prune = 0.0
        while True:
            try:
                storage.liveness_scan(device_timeout=dev_to, sensor_timeout=sen_to)
                storage.health_scan(sensor_timeout=sen_to)   # 고착·드리프트·이상 감지
                for a in storage.escalate_due(after_seconds=escalate_after):  # 미확인 경보 상향→알림
                    dispatch(a)
                now = time.time()
                if now - last_prune > 3600:                  # 1시간마다 보존 정리
                    last_prune = now
                    storage.prune(readings_days=keep_readings, events_days=keep_events)
            except Exception:  # noqa: BLE001 - 감시는 죽지 않아야 한다
                pass
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True).start()
