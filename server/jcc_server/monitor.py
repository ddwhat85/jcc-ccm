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
    # 어느 등급까지 상향·알림할지. 기본 crit(위험)만 — 주의까지 문자로 보내면 알림 피로.
    escalate_sev = os.environ.get("JCC_ESCALATE_SEVERITY") or "crit"
    from .notify import dispatch
    from .heal import Healer, HEAL_TARGETS as _HEAL_TARGETS
    healer = Healer.from_env(storage)   # 자가치유 L1(채널 자동 재시작)

    keep_readings = float(os.environ.get("JCC_KEEP_READING_DAYS") or 14)
    keep_events = float(os.environ.get("JCC_KEEP_EVENT_DAYS") or 90)

    def loop() -> None:
        # 시작 직후에는 아직 데이터가 안 쌓였을 수 있어 한 박자 쉬고 시작한다.
        time.sleep(interval)
        last_prune = 0.0
        notified: set = set()
        first_pass = True          # 재시작 직후 기존 경보를 다시 발송하지 않기 위함
        while True:
            try:
                storage.liveness_scan(device_timeout=dev_to, sensor_timeout=sen_to)
                storage.health_scan(sensor_timeout=sen_to)   # 고착·드리프트·이상 감지

                # 자가치유 L1: 채널 장애(침묵·고착)는 먼저 자동 재시작으로 복구를 시도한다.
                active = storage.list_active_alarms()
                healer.tick(active)

                # 새로 뜬 위험(crit) 경보는 즉시 알림 발송.
                # 단, 자동복구가 손대는 종류(침묵)는 즉시 알리지 않고 복구 실패 시
                # 상향(escalate)에서 알린다 — 재시작 한 번에 풀릴 일로 매번 문자 보내지 않게.
                notified.intersection_update({a["id"] for a in active})   # 해제된 건 잊는다
                heal_kinds = set(_HEAL_TARGETS) if healer.enabled else set()
                for a in active:
                    if a["id"] in notified:
                        continue
                    notified.add(a["id"])
                    if not first_pass and a.get("severity") == "crit" and a.get("kind") not in heal_kinds:
                        dispatch(a, "발생")
                first_pass = False

                for a in storage.escalate_due(after_seconds=escalate_after,
                                              severity=escalate_sev):        # 미확인 경보 상향→알림
                    dispatch(a, "상향")
                now = time.time()
                if now - last_prune > 3600:                  # 1시간마다 보존 정리
                    last_prune = now
                    storage.prune(readings_days=keep_readings, events_days=keep_events)
            except Exception:  # noqa: BLE001 - 감시는 죽지 않아야 한다
                pass
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True).start()
