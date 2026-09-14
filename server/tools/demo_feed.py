"""데모 피더 — 서버에 현실적인 텔레메트리를 실제 HTTP로 쏜다.

펌웨어를 CCM에 올리기 전, 서버·저장·대시보드 전 구간을 검증하는 용도.
펌웨어와 동일한 payload 형식을 쓴다. 실제 펌웨어의 http_transport가 보내는 것과 같다.

    python tools/demo_feed.py --url http://127.0.0.1:8770/v1/telemetry --devices 2 --interval 2
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
import urllib.request

SENSORS = [
    ("cabinet_temp", "함내 온도", "C"),
    ("cabinet_humidity", "함내 습도", "%RH"),
    ("door_distance", "도어 개폐", "mm"),
    ("h2_lel", "수소 농도", "%LEL"),
    ("main_current", "메인차단기 전류", "A"),
]


def reading(key, name, unit, t, rng):
    if rng.random() < 0.03:
        return {"key": key, "name": name, "unit": unit, "value": None, "ok": False,
                "ts": round(time.time(), 3), "error": "일시적 읽기 오류"}
    if "temp" in key:
        v = 27 + 3 * math.sin(t / 30) + rng.uniform(-0.3, 0.3)
    elif "humid" in key:
        v = 45 + 8 * math.sin(t / 45) + rng.uniform(-1, 1)
    elif "door" in key:
        v = 12 if rng.random() > 0.1 else 340
    elif "h2" in key:
        v = max(0.0, rng.gauss(0.4, 0.2))
    elif "current" in key:
        v = 18 + 4 * math.sin(t / 20) + rng.uniform(-0.5, 0.5)
    else:
        v = rng.uniform(0, 100)
    return {"key": key, "name": name, "unit": unit, "value": round(v, 2),
            "ok": True, "ts": round(time.time(), 3)}


def post(url, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8770/v1/telemetry")
    ap.add_argument("--devices", type=int, default=2)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--cycles", type=int, default=0, help="0이면 무한")
    args = ap.parse_args()

    rngs = [random.Random(i) for i in range(args.devices)]
    t0 = time.time()
    n = 0
    while args.cycles == 0 or n < args.cycles:
        t = time.time() - t0
        for i in range(args.devices):
            payload = {
                "device_id": f"ccm-demo-{i+1:04d}",
                "site": "인터배터리 데모" if i == 0 else f"현장 {i+1}",
                "ts": round(time.time(), 3),
                "readings": [reading(k, nm, u, t, rngs[i]) for k, nm, u in SENSORS],
            }
            try:
                status, resp = post(args.url, payload)
                print(f"  {payload['device_id']}: {status} {resp}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {payload['device_id']}: 전송 실패 {exc}")
        n += 1
        if args.cycles == 0 or n < args.cycles:
            time.sleep(args.interval)
    print(f"완료: {n} 주기")


if __name__ == "__main__":
    main()
