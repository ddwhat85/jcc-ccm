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

from . import __version__
from .config import Config
from .edge import build_edge
from .sensors import build_drivers, Reading
from .transport import build_transport

log = logging.getLogger("jcc_ccm.agent")

# 오프라인일 때 메모리에 보관할 최대 묶음 수. eMMC·RAM이 작으니 상한을 둔다.
_MAX_QUEUE = 2000
_STARTED = time.time()


def boot_ts() -> float:
    """이 장치가 켜진 시각(유닉스 초). 서버는 이 값이 바뀌면 '전원이 꺼졌다 켜짐(재시작)'으로 기록하고,
    통신만 끊긴 것(값은 큐에 쌓였다 나중에 올라옴)과 구분한다. 리눅스면 /proc/uptime, 아니면 이 프로그램 시작 시각."""
    try:
        with open("/proc/uptime", encoding="ascii") as fh:
            return round(time.time() - float(fh.read().split()[0]), 0)
    except (OSError, ValueError, IndexError):
        return round(_STARTED, 0)


class Agent:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        # 수동 센서(대시보드에서 직접 지정 → 서버가 내려보냄). 설정 파일 센서 + 이것으로 드라이버를 만든다
        from .manual import ManualSensors, manual_path
        self._manual = ManualSensors(manual_path(cfg), {s.key for s in cfg.sensors})
        self._drivers = build_drivers(cfg.sensors + self._manual.configs(), cfg.modbus)
        self._transport = build_transport(cfg)
        self._queue: collections.deque[dict] = collections.deque(maxlen=_MAX_QUEUE)
        self._stop = False
        # 엣지 예지(config [predict]) — 서버·회선과 무관하게 이 CCM이 직접 벤트·히터·팬을 몬다
        self._edge = build_edge(cfg)
        # 원격 업데이트(config [ota]) — 서명 확인·안전 설치·시험 부팅(롤백은 런처)
        self._ota = None
        if cfg.ota.enabled:
            from .ota import OtaManager
            self._ota = OtaManager(cfg)
        self._restart = False

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
        log.info("에이전트 종료%s.", " — OTA 재시작" if getattr(self, "_restart", False) else "")
        return 75 if getattr(self, "_restart", False) else 0

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
                payload["tuning"] = edge.tuning_report()
            except Exception as exc:  # noqa: BLE001 - 예지 오류로 수집이 멈추면 안 된다
                log.exception("엣지 예지 오류(수집은 계속): %s", exc)

        self._enqueue(payload)
        sent = self._flush()
        self._apply_commands()
        self._apply_tuning()
        self._apply_sensor_config()
        self._handle_ota(sent)

    def _handle_ota(self, sent: int) -> None:
        """전송 성공을 OTA 시험 부팅 확정에 반영하고, 서버의 새 버전 제안을 처리한다."""
        ota = getattr(self, "_ota", None)
        if ota is None:
            return
        try:
            for _ in range(sent or 0):
                ota.note_send_ok()
            if ota.trial_expired():
                log.error("OTA 시험 기한 초과 — 재시작해 런처가 이전 버전으로 되돌립니다")
                self._restart = self._stop = True
                return
            take = getattr(self._transport, "take_ota", None)
            offer = take() if take else None
            if offer and ota.handle_offer(offer):
                self._restart = self._stop = True
        except Exception as exc:  # noqa: BLE001 - 업데이트 오류로 수집이 멈추면 안 된다
            log.exception("OTA 처리 오류(수집은 계속): %s", exc)

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

    def _apply_tuning(self) -> None:
        """서버가 내려보낸 예지 기준 설정을 엣지에 넘긴다(검증·적용은 엣지가). 결과는 다음 보고에."""
        edge = getattr(self, "_edge", None)
        take = getattr(self._transport, "take_tuning", None)
        if edge is None or take is None:
            return
        try:
            msg = take()
            if msg:
                edge.apply_tuning(msg)
        except Exception as exc:  # noqa: BLE001 - 설정 오류로 수집이 멈추면 안 된다
            log.exception("원격 설정 처리 오류: %s", exc)

    def _apply_sensor_config(self) -> None:
        """서버가 내려보낸 수동 센서 목록을 검사·적용하고 드라이버를 다시 만든다(결과는 다음 보고에)."""
        manual = getattr(self, "_manual", None)
        take = getattr(self._transport, "take_sensor_config", None)
        if manual is None or take is None:
            return
        try:
            msg = take()
            if msg and manual.apply(msg):
                self._drivers = build_drivers(self._cfg.sensors + manual.configs(), self._cfg.modbus)
        except Exception as exc:  # noqa: BLE001 - 설정 오류로 수집이 멈추면 안 된다
            log.exception("수동 센서 설정 처리 오류: %s", exc)

    def _build_payload(self, readings: list[Reading]) -> dict:
        return {
            "device_id": self._cfg.device_id,
            "site": self._cfg.site,
            "panel": self._cfg.panel,
            "panel_name": self._cfg.panel_name,
            "ts": round(time.time(), 3),
            "fw": __version__,
            "boot_ts": boot_ts(),
            "readings": [r.as_dict() for r in readings],
            **({"ota": self._ota.status()} if getattr(self, "_ota", None) else {}),
            **({"sensor_config": self._manual.report()} if getattr(self, "_manual", None) else {}),
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
        return sent
