"""수집 에이전트 — 펌웨어의 메인 루프.

주기마다: 모든 센서를 읽어 → 하나의 텔레메트리 묶음으로 만들어 → 전송한다.
전송이 실패하면 로컬 큐에 쌓아두고 다음 주기에 재시도한다(현장 회선이 끊겨도
데이터를 잃지 않기 위해). SIGTERM/SIGINT로 깔끔히 멈춘다(systemd 대응).
"""
from __future__ import annotations

import collections
import logging
import signal
import time

from .config import Config
from .edge import build_edge
from .sensors import build_drivers, Reading
from .transport import build_transport

log = logging.getLogger("jcc_ccm.agent")

# 오프라인일 때 메모리에 보관할 최대 묶음 수. eMMC·RAM이 작으니 상한을 둔다.
_MAX_QUEUE = 2000


class Agent:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._drivers = build_drivers(cfg.sensors, cfg.modbus)
        self._transport = build_transport(cfg)
        self._queue: collections.deque[dict] = collections.deque(maxlen=_MAX_QUEUE)
        self._stop = False
        # 엣지 예지(config [predict]) — 서버·회선과 무관하게 이 CCM이 직접 벤트·히터·팬을 몬다
        self._edge = build_edge(cfg)

    # ── 수명주기 ──────────────────────────────────────────────
    def _install_signals(self) -> None:
        def _handler(signum, _frame):  # noqa: ANN001
            log.info("신호 %s 수신 — 종료합니다.", signum)
            self._stop = True
        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)

    def run(self) -> None:
        self._install_signals()
        log.info("JCC-CCM 에이전트 시작: device=%s site=%s 센서 %d개, 주기 %ds",
                 self._cfg.device_id, self._cfg.site, len(self._drivers),
                 self._cfg.interval_seconds)
        try:
            self._transport.connect()
        except Exception as exc:  # noqa: BLE001 - 연결 실패해도 큐잉하며 재시도
            log.warning("전송 연결 초기화 실패(계속 진행): %s", exc)

        while not self._stop:
            started = time.monotonic()
            self._tick()
            # 주기를 지키되, 수집에 걸린 시간을 빼서 드리프트를 막는다.
            elapsed = time.monotonic() - started
            self._sleep(max(0.0, self._cfg.interval_seconds - elapsed))

        self._transport.close()
        log.info("에이전트 종료.")

    def _sleep(self, seconds: float) -> None:
        # 종료 신호에 빠르게 반응하도록 잘게 나눠 잔다.
        deadline = time.monotonic() + seconds
        while not self._stop:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # remaining이 while 검사 후 0 이하로 떨어져도 time.sleep(음수)가 나지 않게 방어.
            time.sleep(min(0.5, remaining))

    # ── 한 주기 ──────────────────────────────────────────────
    def _tick(self) -> None:
        readings = [d.read() for d in self._drivers]
        payload = self._build_payload(readings)

        ok_count = sum(1 for r in readings if r.ok)
        if ok_count < len(readings):
            failed = [r.key for r in readings if not r.ok]
            log.warning("센서 %d/%d 실패: %s", len(failed), len(readings), ", ".join(failed))

        # 엣지 예지는 전송보다 먼저 — 회선이 죽어도 벤트는 이 주기에 바로 움직인다.
        edge = getattr(self, "_edge", None)
        if edge is not None:
            try:
                edge.observe(readings)
                payload["edge"] = edge.step()
            except Exception as exc:  # noqa: BLE001 - 예지 오류로 수집이 멈추면 안 된다
                log.exception("엣지 예지 오류(수집은 계속): %s", exc)

        self._enqueue(payload)
        self._flush()
        self._apply_commands()

    def _apply_commands(self) -> None:
        """서버가 내려보낸 수동 조작 명령을 엣지 출력에 반영한다(다음 보고에 결과가 실린다)."""
        edge = getattr(self, "_edge", None)
        take = getattr(self._transport, "take_commands", None)
        if edge is None or take is None:
            return
        try:
            for cmd in take():
                edge.apply_command(cmd)
        except Exception as exc:  # noqa: BLE001
            log.exception("원격 명령 처리 오류: %s", exc)

    def _build_payload(self, readings: list[Reading]) -> dict:
        return {
            "device_id": self._cfg.device_id,
            "site": self._cfg.site,
            "panel": self._cfg.panel,
            "panel_name": self._cfg.panel_name,
            "ts": round(time.time(), 3),
            "readings": [r.as_dict() for r in readings],
        }

    # ── 전송 큐 (오프라인 내구성) ──────────────────────────────
    def _enqueue(self, payload: dict) -> None:
        if len(self._queue) == self._queue.maxlen:
            log.warning("전송 큐가 가득 참(%d). 가장 오래된 데이터를 버립니다.", _MAX_QUEUE)
        self._queue.append(payload)

    def _flush(self) -> None:
        """큐에 쌓인 오래된 것부터 보낸다. 하나라도 실패하면 멈추고 다음 주기에 재시도."""
        sent = 0
        while self._queue:
            payload = self._queue[0]
            if self._transport.send(payload):
                self._queue.popleft()
                sent += 1
            else:
                break  # 회선이 죽었다. 순서를 지키려 여기서 중단.
        if sent:
            log.info("전송 %d건 완료, 대기 %d건.", sent, len(self._queue))
        elif self._queue:
            log.warning("전송 실패, 대기 %d건 보관 중.", len(self._queue))
