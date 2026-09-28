"""개발용 시뮬레이션 모드.

실기(CCM) 없이 개발 PC에서 펌웨어 루프 전체를 확인한다. 센서 드라이버를
그럴듯한 값을 내는 가짜로 바꾸고, 전송은 콘솔 출력으로 대체한다.
로직(주기·묶음 구성·큐·재시도)은 실제 Agent와 동일한 경로를 탄다.
"""
from __future__ import annotations

import json
import logging
import math
import random
import time

from .config import Config, SensorConfig
from .sensors import Reading

log = logging.getLogger("jcc_ccm.sim")


SCENARIO_START = 45.0   # 워밍업(학습 중)이 끝난 뒤 시작
SCENARIO_RAMP = 60.0


class _FakeDriver:
    """센서 종류에 맞춰 현실적인 파형을 낸다 (판넬 모니터링 데모용).

    scenario를 주면 SCENARIO_START초 뒤부터 엣지 예지 시연 상황을 만든다:
      fire    H2·VOC 동반 상승(임계 前)  → CCM이 스스로 벤트 개방
      contact 접점온도만 상승            → 접점 발열 위험
      dew     습도 상승                  → 히터·팬
      gasloss 가스 센서 무응답           → 60초 뒤 fail-safe 벤트 개방
    """

    def __init__(self, cfg: SensorConfig, seed: int, scenario: str = ""):
        self._cfg = cfg
        self._rng = random.Random(seed)
        self._t0 = time.time()
        self._scenario = scenario

    def _scenario_value(self, key: str, t: float):
        s = self._scenario
        if not s or t < SCENARIO_START:
            return None
        prog = min(1.0, (t - SCENARIO_START) / SCENARIO_RAMP)
        if s == "fire" and "h2" in key:
            return 0.5 + 17 * prog
        if s == "fire" and "voc" in key:
            return 30 + 770 * prog
        if s == "contact" and "ncontact" in key:
            return 27 + 24 * prog
        if s == "dew" and "humid" in key:
            return 55 + 40 * prog
        return None

    def read(self) -> Reading:
        cfg = self._cfg
        t = time.time() - self._t0
        key = cfg.key
        if self._scenario == "gasloss" and t >= SCENARIO_START and ("h2" in key or "voc" in key):
            return Reading.failure(cfg, "시뮬레이션: 가스 센서 무응답")
        # 5%의 확률로 일시적 읽기 실패를 흉내 내, 큐·경고 로직도 시연되게 한다.
        if self._rng.random() < 0.05:
            return Reading.failure(cfg, "시뮬레이션: 일시적 읽기 오류")
        sv = self._scenario_value(key, t)
        if sv is not None:
            return Reading.success(cfg, round(sv + self._rng.uniform(-0.2, 0.2), 2))

        if "ncontact" in key:
            val = 27 + 3 * math.sin(t / 30) + self._rng.uniform(-0.3, 0.3)
        elif "temp" in key:
            base = 27 + 3 * math.sin(t / 30)          # 24~30 ℃
            val = base + self._rng.uniform(-0.3, 0.3)
        elif "voc" in key:
            val = max(0.0, self._rng.gauss(30, 10))  # ppm
        elif "smoke" in key:
            val = 0
        elif "humid" in key:
            val = 45 + 8 * math.sin(t / 45) + self._rng.uniform(-1, 1)
        elif "distance" in key or "door" in key:
            val = 12 if self._rng.random() > 0.1 else 340   # 가끔 도어 열림
        elif "h2" in key:
            val = max(0.0, self._rng.gauss(0.4, 0.15))       # %LEL, 대개 낮음
        elif "current" in key:
            val = 18 + 4 * math.sin(t / 20) + self._rng.uniform(-0.5, 0.5)
        else:
            val = self._rng.uniform(0, 100)
        return Reading.success(cfg, round(val, 2))


class _ConsoleTransport:
    """전송 대신 텔레메트리를 보기 좋게 콘솔에 찍는다."""

    def connect(self) -> None:
        print("── 시뮬레이션 전송: 콘솔 출력 (실제 MQTT/HTTP 아님) ──\n")

    def send(self, payload: dict) -> bool:
        rs = payload["readings"]
        head = f"[{time.strftime('%H:%M:%S')}] device={payload['device_id']}"
        print(head)
        for r in rs:
            if r["ok"]:
                print(f"    {r['name']:<12} {r['value']:>8} {r['unit']}")
            else:
                print(f"    {r['name']:<12} {'--':>8}  (실패: {r.get('error','')})")
        e = payload.get("edge")
        if e:
            from .actuators import on_word
            st = lambda k, n: f"{e[k]['stage']}" + (f" {e[k][n]}" if e[k].get(n) is not None else "")
            acts = " ".join(f"{k}={on_word(k, a['on']) if a['on'] is not None else '?'}"
                            f"({a['mode']})" for k, a in e["actuators"].items())
            print(f"    [엣지] 화재 {st('fire','fri')} · 접점 {st('contact','residual')} · "
                  f"결로 {st('dew','margin')} | {acts}" + ("  ⚠fail-safe" if e.get("failsafe") else ""))
            for a in e["actions"]:
                print(f"    [엣지 조치] {a['actuator']} → {on_word(a['actuator'], a['on'])} — {a['why']}")
        print(f"    → payload {len(json.dumps(payload, ensure_ascii=False))} bytes 전송\n")
        return True

    def take_commands(self) -> list:
        return []

    def close(self) -> None:
        print("── 시뮬레이션 종료 ──")


def run_simulation(cfg: Config, scenario: str = "") -> int:
    from .agent import Agent
    from .edge import build_edge

    agent = Agent.__new__(Agent)          # __init__의 실드라이버 생성을 건너뛴다
    agent._cfg = cfg
    agent._drivers = [
        _FakeDriver(s, seed=i, scenario=scenario) for i, s in enumerate(cfg.sensors) if s.enabled
    ]
    agent._transport = _ConsoleTransport()
    import collections
    agent._queue = collections.deque(maxlen=2000)
    agent._stop = False
    agent._edge = build_edge(cfg, force_log=True)   # 실기 릴레이 대신 로그 출력
    if scenario:
        log.info("시나리오 '%s' — %d초 뒤 시작(그 전은 학습·정상)", scenario, int(SCENARIO_START))

    log.info("시뮬레이션 시작 (Ctrl+C로 종료). 센서 %d개, 주기 %ds.",
             len(agent._drivers), cfg.interval_seconds)
    agent.run()
    return 0
