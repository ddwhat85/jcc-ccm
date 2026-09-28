"""파이썬 예지 코어 ↔ demo-api.js(브라우저 미러) 일치 검사.

같은 입력(실제 에포크 시각 포함)을 양쪽에 넣어 결과가 같은지 본다. 미러가 조용히 갈라지면
(예: 기울기 계산의 부동소수점 상쇄, 중앙값 규칙 차이) 데모 화면만 틀리게 동작하는데,
한쪽 단위테스트로는 잡히지 않는다 — 그래서 이 테스트가 있다.

python -m tests.test_parity   (server/ 에서, Node 필요 — 없으면 건너뜀)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
DEMO_API = os.path.join(os.path.dirname(os.path.dirname(HERE)), "web", "demo-api.js")

from jcc_server.fire_risk import assess, robust_z, slope_per_min
from jcc_server.contact_heat import ContactBaseline, ContactCfg, assess_contact
from jcc_server.dewpoint import assess_dewpoint
from jcc_server.incidents import group_alarms

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


E = 1_790_000_000.0          # 실제 에포크 초(부동소수점 상쇄를 드러내려면 꼭 큰 값이어야 함)


def fixtures() -> dict:
    noisy = [0.4 + 0.1 * ((i * 7) % 5 - 2) for i in range(40)]
    noisy_even = noisy[:24]
    ramp = [(E + i * 2, round(0.5 + i * 0.6, 3)) for i in range(30)]
    flat = [(E + i * 2, 0.4) for i in range(30)]
    voc_ramp = [(E + i * 2, 30 + i * 25) for i in range(30)]
    temp_up = [(E + i * 2, 27 + i * 0.2) for i in range(30)]
    c_norm = [(E + i * 2, 15, 27 + 0.2 * (i % 2), 27) for i in range(20)]
    c_trend = [(E + i * 2, 12, 27 + 0.05 * i * 2, 27) for i in range(24)]
    d_norm = [(E + i * 6, 25.0, 45.0, 25.0) for i in range(20)]
    d_near = [(E + i * 6, 25.0, 50.0, 20.0 - i * (1.1 / 19)) for i in range(20)]
    d_far = [(E + i * 6, 25.0, 50.0, 27.0 - i * (2.0 / 19)) for i in range(20)]
    return {
        "slope": [ramp, flat, voc_ramp, temp_up],
        "z": [[6.0, noisy], [0.4, noisy], [3.0, noisy_even], [1.0, [0.4] * 10]],
        "fire": [
            {"h2": {"value": 0.4, "series": flat, "baseline": noisy},
             "voc": {"value": 30, "series": [(t, 30) for t, _ in flat], "baseline": [30 + (i % 3) for i in range(30)]},
             "temp": {"value": 27, "series": [(t, 27) for t, _ in flat]}, "smoke": False},
            {"h2": {"value": 17.9, "series": ramp, "baseline": noisy},
             "voc": {"value": 755, "series": voc_ramp, "baseline": [30 + (i % 3) for i in range(30)]},
             "temp": {"value": 32.8, "series": temp_up}, "smoke": False},
            {"h2": {"value": 6.0, "series": flat, "baseline": noisy}, "voc": {}, "temp": {}, "smoke": False},
            {"h2": {"value": 0.4, "series": flat, "baseline": noisy}, "voc": {}, "temp": {}, "smoke": True},
            {"h2": {"value": 12, "series": ramp, "baseline": noisy}, "voc": {"value": 300}, "temp": {},
             "smoke": False, "current_abnormal": True},
        ],
        "contact": [[15, 27.1, 27, c_norm], [15, 50, 27, c_norm], [15, 35, 27, c_norm],
                    [12, 27 + 0.05 * 46, 27, c_trend], [1.0, 40, 25, c_norm], [3, 63, 25, c_norm]],
        "dew": [[25.0, 45.0, 25.0, d_norm], [25.0, 95.0, 25.0, d_norm], [25.0, 50.0, 18.9, d_near],
                [25.0, 50.0, 25.0, d_far], [25.0, 88.0, 25.0, d_norm], [25.0, 70.0, 20.6, d_norm]],
        "baseline": [_drift_seq(20, 300.0), _drift_seq(0.2, 60.0)],
        "incidents": _incident_cases(),
        "contact_ref": [[15, 27 + 0.03 * 225 + 7, 27, c_norm, 0.03], [15, 27 + 0.03 * 225 + 13, 27, c_norm, 0.03],
                        [15, 27.3, 27, c_norm, 0.0]],
    }


def _incident_cases() -> list:
    """경보 묶기 비교용: 화재 연쇄·화재 후 CCM 두절·CCM 두절 전후·접점·결로·같은 센서·다른 판넬."""
    kinds = {"ccm-1:h2": "h2", "ccm-1:voc": "voc", "ccm-2:t": "temp", "ccm-2:rh": "humidity",
             "ccm-2:door": "door", "ccm-3:rh": "humidity", "ccm-3:vib": "vibration", "ccm-4:smoke": "smoke",
             "ccm-4:ncontact_temp": "temp", "ccm-9:h2": "h2"}
    panels = {"ccm-1": "p1", "ccm-2": "p1", "ccm-3": "p1", "ccm-4": "p1", "ccm-9": "p2"}
    rows = [  # id, dev, key, kind, sev, dt, acked
        (1, "ccm-1", "h2", "fire", "crit", 0, 0), (2, "ccm-1", "h2", "alarm_warn", "warn", -120, 0),
        (3, "ccm-1", "voc", "drift", "warn", 30, 1), (4, "ccm-2", "t", "anomaly", "warn", 40, 0),
        (5, "ccm-4", "smoke", "alarm", "crit", 90, 0), (6, "ccm-2", "door", "alarm_warn", "warn", 10, 0),
        (7, "ccm-9", "h2", "alarm_warn", "warn", 20, 0), (8, "ccm-1", "", "silent", "crit", 150, 0),
        (9, "ccm-1", "voc", "stuck", "warn", 200, 0), (10, "ccm-3", "", "silent", "crit", 500, 0),
        (11, "ccm-3", "vib", "stuck", "warn", 700, 0), (12, "ccm-3", "vib", "alarm_warn", "warn", -400, 1),
        (13, "ccm-4", "ncontact_temp", "contact", "crit", 800, 0), (14, "ccm-4", "ncontact_temp", "anomaly", "warn", 760, 0),
        (15, "ccm-2", "rh", "dew", "warn", 900, 0), (16, "ccm-3", "rh", "stuck", "warn", 950, 0),
        (17, "ccm-2", "rh", "alarm", "crit", 920, 1), (18, "ccm-2", "door", "drift", "warn", 30, 0),
        (19, "ccm-4", "smoke", "stuck", "warn", 95, 0),       # 연기 센서 고장류는 화재에 안 묶임
    ]
    alarms = [{"id": i, "device_id": d, "sensor_key": k, "kind": kd, "severity": sv, "raised_at": E + dt,
               "acked_at": (E + dt + 1) if ak else None, "detail": f"{d}:{k} {kd}"}
              for i, d, k, kd, sv, dt, ak in rows]
    return [{"alarms": alarms, "sensors": kinds, "panels": panels},
            {"alarms": alarms[:7], "sensors": kinds, "panels": panels},
            {"alarms": [], "sensors": kinds, "panels": panels}]


def _drift_seq(days: float, step: float) -> list:
    """에포크 시각의 접점 표본열: 부하 8~16A, 2일째부터 접촉저항이 하루 15%씩 증가."""
    import math
    out = []
    for m in range(int(days * 86400 / step)):
        d = m * step / 86400
        I = 12 + 4 * math.sin(m / 7.0)
        k = 0.03 * (1 + 0.15 * max(0.0, d - 2))
        out.append((E + m * step, I, 25 + k * I * I + 0.2 * math.sin(m * 1.7), 25.0))
    return out


def python_results(fx: dict) -> dict:
    r2 = lambda x: None if x is None else round(x, 2)
    return {
        "slope": [r2(slope_per_min(s)) for s in fx["slope"]],
        "z": [r2(robust_z(v, b)) for v, b in fx["z"]],
        "fire": [{"fri": a.fri, "stage": a.stage} for a in (assess(s) for s in fx["fire"])],
        "contact": [{"stage": r.stage, "residual": r.residual, "slope": r.residual_slope}
                    for r in (assess_contact(i, t, a, h) for i, t, a, h in fx["contact"])],
        "dew": [{"stage": r.stage, "margin": r.margin, "slope": r.margin_slope}
                for r in (assess_dewpoint(t, rh, s, h) for t, rh, s, h in fx["dew"])],
        "baseline": [_py_baseline(seq) for seq in fx["baseline"]],
        "contact_ref": [{"stage": r.stage, "residual": r.residual}
                        for r in (assess_contact(i, t, a, h, k_ref=k) for i, t, a, h, k in fx["contact_ref"])],
        "incidents": [[[x["cause"], x["primary"], sorted(x["alarms"]), x["severity"], x["acked"]]
                       for x in group_alarms(c["alarms"], {tuple(k.split(":", 1)): v for k, v in c["sensors"].items()},
                                             c["panels"])] for c in fx["incidents"]],
    }


def _py_baseline(seq) -> dict:
    b = ContactBaseline(ContactCfg())
    mid = None
    for i, (ts, I, T, Ta) in enumerate(seq):
        b.observe(ts, I, T, Ta)
        if i == 149:
            mid = b.progress()
    return {"status": b.status, "k": None if b.k is None else round(b.k, 6), "learned_at": b.learned_at,
            "mid": round(mid, 4)}


def close(a, b, tol):
    if a is None or b is None:
        return a is b
    return abs(a - b) <= tol


def run():
    node = shutil.which("node")
    if not node:
        print("[SKIP] Node가 없어 JS 미러 일치 검사를 건너뜁니다.")
        return 0
    fx = fixtures()
    py = python_results(fx)
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(fx, fh)
        proc = subprocess.run([node, os.path.join(HERE, "parity_harness.js"), DEMO_API, path],
                              capture_output=True, text=True, encoding="utf-8")
    finally:
        os.unlink(path)
    if proc.returncode != 0:
        check("JS 하네스 실행", False, proc.stderr.strip()[:400])
        return 1
    js = json.loads(proc.stdout)

    print("=== 파이썬 ↔ JS 미러 일치 (에포크 시각 포함) ===")
    # 반올림 규칙 차이(파이썬 banker's vs JS half-up)만큼은 허용
    for i, (p, j) in enumerate(zip(py["slope"], js["slope"])):
        check(f"기울기 #{i}", close(p, j, 0.02), f"py={p} js={j}")
    for i, (p, j) in enumerate(zip(py["z"], js["z"])):
        check(f"로버스트 z #{i}", close(p, j, 0.02), f"py={p} js={j}")
    for i, (p, j) in enumerate(zip(py["fire"], js["fire"])):
        check(f"화재 #{i}", p["stage"] == j["stage"] and close(p["fri"], j["fri"], 0.11),
              f"py={p} js={j}")
    for i, (p, j) in enumerate(zip(py["contact"], js["contact"])):
        check(f"접점 #{i}", p["stage"] == j["stage"] and close(p["residual"], j["residual"], 0.11)
              and close(p["slope"], j["slope"], 0.02), f"py={p} js={j}")
    for i, (p, j) in enumerate(zip(py["dew"], js["dew"])):
        check(f"결로 #{i}", p["stage"] == j["stage"] and close(p["margin"], j["margin"], 0.11)
              and close(p["slope"], j["slope"], 0.02), f"py={p} js={j}")
    for i, (p, j) in enumerate(zip(py["baseline"], js["baseline"])):
        check(f"접점 기준 학습 #{i}", p["status"] == j["status"] and p["learned_at"] == j["learned_at"]
              and close(p["k"], j["k"], 1e-5) and close(p["mid"], j["mid"], 1e-3), f"py={p} js={j}")
    for i, (p, j) in enumerate(zip(py["incidents"], js["incidents"])):
        check(f"경보 묶기 #{i}", p == j, f"py={p}\n        js={j}" if p != j else f"사건 {len(p)}건 동일")
    for i, (p, j) in enumerate(zip(py["contact_ref"], js["contact_ref"])):
        check(f"접점(기준 k) #{i}", p["stage"] == j["stage"] and close(p["residual"], j["residual"], 0.11),
              f"py={p} js={j}")

    print()
    if _fails:
        print(f"❌ {len(_fails)} FAIL: {_fails}")
        return 1
    print("✅ 전체 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
