"""출력 작동 확인 — 벤트·히터·팬이 명령대로 됐는지 되읽고, 안 되면 다시 해 보고, 끝내 안 되면 '작동 실패'.

릴레이에 "열어라"를 쓰기만 하면, 릴레이 고착·배선 탈락·모듈 재부팅·댐퍼 걸림이 있어도 화면은
"벤트 개방"이라고 말한다. 화재 예지 제품에서 가장 위험한 거짓말이라 출력마다 이 가드를 붙인다.

확인 방식(ActuatorConfig.feedback_mode)
  none   : 확인 안 함(status "off")
  coil   : 릴레이 코일 되읽기 — 릴레이가 붙었나 + 모듈 재부팅으로 풀렸나
  switch : 위치 스위치(보조접점) — 실제로 열렸나/닫혔나

상태: ok(확인됨) · moving(작동 중) · fault(작동 실패) · unknown(읽기 실패 — 고장으로 단정하지 않음) · off
원칙
  · 통신(읽기) 실패는 고장이 아니다 → unknown, 다음 주기에 다시 읽는다
  · 구동기 혹사 금지: 고장 중 다시 써 보기는 check_every에 한 번
  · 이벤트는 바뀐 순간에만(작동 실패·복구·릴레이 복구·명령 없이 위치 바뀜·재시도)
"""
from __future__ import annotations

import logging

from .actuators import on_word
from .config import ActuatorConfig

log = logging.getLogger("jcc_ccm.guard")


class ActuatorGuard:
    def __init__(self, act, cfg: ActuatorConfig, clock):
        self._act = act
        self._cfg = cfg
        self._clock = clock
        self.mode = cfg.feedback_mode()
        self.want: bool | None = None
        self.status = "off" if self.mode == "none" else "unknown"
        self.fault = ""
        self.position: str | None = None
        self._deadline = 0.0
        self._left = cfg.retries
        self._last_check = 0.0
        self._last_try = 0.0

    # ── 명령 ───────────────────────────────────────────────
    def commanded(self, on: bool, now: float) -> None:
        """쓰기 성공 직후 호출. 작동 시간 안에 맞아야 한다."""
        self.want = bool(on)
        if self.mode == "none":
            return
        self.status, self._deadline, self._left = "moving", now + self._cfg.travel(), self._cfg.retries
        self._last_check = now

    # ── 매 주기 ────────────────────────────────────────────
    def _read(self):
        v = self._act.read_position() if self.mode == "switch" else self._act.read_state()
        if self.mode == "switch" and v is not None:
            self.position = ("open" if v else "closed") if self._cfg.kind == "vent" else ("on" if v else "off")
        return v

    def _word(self, on: bool) -> str:
        return on_word(self._cfg.kind, on)

    def _ev(self, kind: str, text: str, level: str) -> dict:
        (log.error if level == "crit" else log.warning if level == "warn" else log.info)("%s: %s", self._cfg.kind, text)
        return {"kind": kind, "text": text, "level": level}

    def _rewrite(self, now: float) -> bool:
        self._last_try = now
        return bool(self._act.set(self.want))

    def tick(self, now: float) -> list:
        if self.mode == "none" or self.want is None:
            return []
        if self.status == "moving":
            return self._tick_moving(now)
        if self.status == "unknown":
            return self._tick_unknown(now)
        if now - self._last_check < self._cfg.check_every:
            return []
        self._last_check = now
        return self._tick_fault(now) if self.status == "fault" else self._tick_ok(now)

    def _tick_moving(self, now: float) -> list:
        v = self._read()
        if v is not None and v == self.want:
            self.status, self.fault = "ok", ""
            return []
        if now < self._deadline:
            return []                       # 아직 움직이는 중
        if v is None:
            self.status = "unknown"         # 기한이 지났는데 못 읽음 — 통신 문제일 수 있다
            return []
        return self._mismatch(now, v)

    def _tick_unknown(self, now: float) -> list:
        v = self._read()
        if v is None:
            return []
        if v == self.want:
            self.status, self.fault = "ok", ""
            return []
        return self._mismatch(now, v)

    def _mismatch(self, now: float, actual: bool) -> list:
        what = "실제" if self.mode == "switch" else "릴레이"
        if self._left > 0:
            self._left -= 1
            self._rewrite(now)
            self.status, self._deadline = "moving", now + self._cfg.travel()
            return [self._ev("retry", f"명령 {self._word(self.want)}·{what} {self._word(actual)} — 다시 시도"
                                      f"(남은 {self._left}회)", "info")]
        self.status = "fault"
        self.fault = f"명령 {self._word(self.want)}·{what} {self._word(actual)}" + (
            " — 구동기 걸림·배선·전원 확인" if self.mode == "switch" else " — 릴레이 모듈·배선 확인")
        self._last_check = self._last_try = now
        return [self._ev("fault", f"작동 실패: {self.fault}", "crit")]

    def _tick_ok(self, now: float) -> list:
        v = self._read()
        if v is None:
            self.status = "unknown"
            return []
        if v == self.want:
            return []
        # 명령 없이 달라짐: 코일이면 모듈 재부팅으로 풀림, 스위치면 누가 손으로 움직였거나 풀림
        self._rewrite(now)
        self.status, self._deadline, self._left = "moving", now + self._cfg.travel(), self._cfg.retries
        if self.mode == "coil":
            return [self._ev("restored", f"릴레이가 풀려 있어 다시 {self._word(self.want)}(모듈 재부팅 의심)", "warn")]
        return [self._ev("moved", f"명령 없이 위치가 {self._word(v)}로 바뀜 — 다시 {self._word(self.want)}", "warn")]

    def _tick_fault(self, now: float) -> list:
        v = self._read()
        if v is not None and v == self.want:
            self.status, self.fault = "ok", ""
            return [self._ev("recovered", f"작동 확인 — {self._word(self.want)} 정상", "info")]
        if now - self._last_try >= self._cfg.check_every:   # 고친 뒤 스스로 풀리게 가끔 다시 써 본다
            self._rewrite(now)
        return []
