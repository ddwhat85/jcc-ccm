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

# 실제 배선 반영: 한 판넬을 CCM 여러 대가 나눠 감시한다.
# CCM 1대 = 센서 소수(윗면 RS485/CAN 1버스)만 담당 → 센서 늘면 CCM 추가.
# 아래는 사진의 스마트 판넬 구성(CCM 2대)을 흉내 낸 것.
CCMS = [
    {
        "device_id": "ccm-2663",  "name": "CCM-A (환경/진동)",
        "sensors": [
            ("cabinet_temp", "함내 온도", "C"),
            ("cabinet_humidity", "함내 습도", "%RH"),
            ("vibration", "진동", "mm/s"),
        ],
    },
    {
        "device_id": "ccm-2661",  "name": "CCM-B (가스/전류)",
        "sensors": [
            ("h2_lel", "수소 농도", "%LEL"),
            ("main_current", "메인차단기 전류", "A"),
            ("door_distance", "도어 개폐", "mm"),
        ],
    },
]
PANEL = ("panel-01", "스마트 판넬")
SITE = "인터배터리 데모"


def reading(key, name, unit, t, rng):
    if rng.random() < 0.03:
        return {"key": key, "name": name, "unit": unit, "value": None, "ok": False,
                "ts": round(time.time(), 3), "error": "일시적 읽기 오류"}
    if "temp" in key:
        v = 27 + 3 * math.sin(t / 30) + rng.uniform(-0.3, 0.3)
    elif "humid" in key:
        v = 45 + 8 * math.sin(t / 45) + rng.uniform(-1, 1)
    elif "vibration" in key:
        v = max(0.0, rng.gauss(1.2, 0.4))
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
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--cycles", type=int, default=0, help="0이면 무한")
    args = ap.parse_args()

    rngs = {c["device_id"]: random.Random(i) for i, c in enumerate(CCMS)}
    t0 = time.time()
    n = 0
    while args.cycles == 0 or n < args.cycles:
        t = time.time() - t0
        for ccm in CCMS:  # 같은 판넬의 CCM들이 각자 전송
            rng = rngs[ccm["device_id"]]
            payload = {
                "device_id": ccm["device_id"],
                "site": SITE,
                "panel": PANEL[0],
                "panel_name": PANEL[1],
                "ts": round(time.time(), 3),
                "readings": [reading(k, nm, u, t, rng) for k, nm, u in ccm["sensors"]],
            }
            try:
                status, resp = post(args.url, payload)
                print(f"  {ccm['device_id']} ({PANEL[1]}): {status} {resp}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {ccm['device_id']}: 전송 실패 {exc}")
        n += 1
        if args.cycles == 0 or n < args.cycles:
            time.sleep(args.interval)
    print(f"완료: {n} 주기")


if __name__ == "__main__":
    main()
