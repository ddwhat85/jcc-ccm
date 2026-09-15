"""데모 모드 — 배포된 서버가 스스로 현실적인 센서값을 생성한다.

환경변수 JCC_DEMO가 설정됐을 때만 켜진다(render.yaml에서 켬). 클라우드에는 실제
CCM이 없으므로, 지인 검토용 공유 링크에서 값이 실제로 움직이도록 서버 안에서
자동 탐색을 한 번 돌리고 이후 주기적으로 시뮬레이션 텔레메트리를 저장한다.

실제 운영 서버는 JCC_DEMO를 켜지 않으므로 이 코드는 동작하지 않는다(진짜 CCM만 수집).
"""
from __future__ import annotations

import math
import random
import threading
import time

from .app import _run_discovery


def _value(key: str, kind: str, t: float, rng: random.Random):
    """센서 종류에 맞는 그럴듯한 값. demo_feed.py와 같은 패턴."""
    if rng.random() < 0.03:
        return None  # 가끔 읽기 실패(현실 반영)
    if "temp" in key or kind == "temp":
        v = 27 + 3 * math.sin(t / 30) + rng.uniform(-0.3, 0.3)
    elif "humid" in key or kind == "humidity":
        v = 45 + 8 * math.sin(t / 45) + rng.uniform(-1, 1)
    elif "vibration" in key or kind == "vibration":
        v = max(0.0, rng.gauss(1.2, 0.4))
    elif "door" in key or kind == "door":
        v = 12 if rng.random() > 0.1 else 340
    elif "h2" in key or kind == "h2":
        v = max(0.0, rng.gauss(0.4, 0.2))
    elif "current" in key or kind == "current":
        v = 18 + 4 * math.sin(t / 20) + rng.uniform(-0.5, 0.5)
    else:  # 미확인/추정 장비 등
        v = max(0.0, rng.gauss(40, 3))
    # 가끔(약 6%) 임계값을 넘겨 경보 로그가 뜨게 한다(검토용 데모 생동감).
    if rng.random() < 0.06:
        spike = {"h2": 2.6, "current": 33, "vibration": 5.2, "temp": 47, "humidity": 88}
        for k, sv in spike.items():
            if k in key or kind == k:
                return sv
    return round(v, 2)


def _loop(storage, interval: float) -> None:
    # 1) 자동 탐색을 한 번 돌려 토폴로지를 만든다(실기의 [AI 자동연결]과 동일).
    try:
        result = _run_discovery()
        storage.set_discovery(result)
    except Exception:  # noqa: BLE001 - 데모는 실패해도 서버를 죽이지 않는다
        return

    ccms = result.get("ccms") or []
    panel = result.get("panel", "")
    panel_name = result.get("panel_name", "")
    site = result.get("site", "")
    rngs = {c["device_id"]: random.Random(i) for i, c in enumerate(ccms)}
    t0 = time.time()
    drop_until: dict = {}   # (dev,key) -> 이 시각까지 이 센서는 전송 안 함(침묵 시연)

    # 2) 이후 주기적으로 각 CCM이 자기 센서값을 올리는 것처럼 저장한다.
    while True:
        t = time.time() - t0
        wall = time.time()
        for c in ccms:
            dev = c["device_id"]
            rng = rngs[dev]
            readings = []
            for s in c.get("sensors") or []:
                key = s.get("key", "")
                sk = (dev, key)
                # 가끔 한 센서가 한동안 침묵(케이블 탈락·센서 사망 시연) → watchdog가 잡는다.
                if wall < drop_until.get(sk, 0):
                    continue
                if rng.random() < 0.012:
                    drop_until[sk] = wall + 70   # 약 70초 침묵 시작
                    continue
                val = _value(key, s.get("kind", ""), t, rng)
                readings.append({
                    "key": key, "name": s.get("name", ""),
                    "unit": s.get("unit", ""), "value": val, "ok": val is not None,
                    "ts": round(time.time(), 3),
                })
            try:
                storage.ingest({
                    "device_id": dev, "site": site, "panel": panel,
                    "panel_name": panel_name, "readings": readings,
                })
            except Exception:  # noqa: BLE001
                pass
        time.sleep(interval)


def start(storage, interval: float = 2.0) -> None:
    """데모 피더를 데몬 스레드로 시작한다(서버 종료 시 함께 종료)."""
    threading.Thread(target=_loop, args=(storage, interval), daemon=True).start()
