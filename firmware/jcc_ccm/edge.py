"""엣지 예지 — CCM이 서버·인터넷 없이 스스로 화재 징조·결로를 판정해 벤트·히터·팬을 몬다.

서버와 **같은 코어**(jcc_ccm/predict/ = server/jcc_server 정본의 사본)로 판정한다.
차이는 이력 저장소뿐: 서버는 DB, 엣지는 센서별 메모리 링버퍼.

한 주기의 흐름
  observe(readings) → step(now):
    입력 조립(inputs.build_panel_inputs) → 판정(Predictor.assess_panel)
    → fail-safe 검사 → 출력 구동(바뀐 것만) → 서버 보고용 요약(report)

출력 원칙(안전측)
  · 판정 보류 중(학습 중·데이터 끊김)엔 자동 출력을 건드리지 않는다 — 재부팅 직후
    이력이 없다고 열려 있던 벤트를 닫는 사고를 막는다.
  · 가스 입력이 failsafe_after_seconds 이상 끊기면 벤트를 fail-safe 상태(기본 개방)로.
    데이터가 돌아와 정상이 이어지면 평소 히스테리시스로 닫힌다.
  · 사람의 수동 조작(대시보드 → 텔레메트리 응답으로 내려옴)은 자동보다 우선하며 즉시 반영.
  · 출력 쓰기 실패는 다음 주기에 재시도(상태를 '모름'으로 둔다).
"""
from __future__ import annotations

import collections
import json
import logging
import os
import time

from .actuators import on_word
from .config import Config
from .predict import Predictor, build_panel_inputs

log = logging.getLogger("jcc_ccm.edge")

_GOVERNS = {"vent": "fire", "heater": "dew", "fan": "dew"}   # 출력 → 담당 알고리즘
_VALID_ACTIONS = {"vent": ("open", "close", "auto"),
                  "heater": ("on", "off", "auto"), "fan": ("on", "off", "auto")}


class EdgePredictor:
    HISTORY = 160          # 센서별 링버퍼(기준선 150표본 + 여유) — 메모리 수 KB

    def __init__(self, cfg: Config, actuators: dict, clock=time.time):
        self._cfg = cfg
        self._panel = cfg.panel or cfg.device_id
        self._roles = {role: (cfg.device_id, key) for role, key in cfg.predict.roles.items()}
        self._hist = {key: collections.deque(maxlen=self.HISTORY)
                      for key in cfg.predict.roles.values()}
        self._pred = Predictor()
        self._pred.autovent = cfg.predict.autovent
        self._acts = actuators
        self._vent_failsafe = next((a.failsafe for a in cfg.actuators if a.kind == "vent"), "open")
        self._clock = clock
        self._lost_since: float | None = None
        self._failsafe = False
        self._pending: dict = {}
        self._res: dict | None = None
        self._actions: list = []
        self._state_file = cfg.predict.state_file
        self._saved_at = 0.0
        self._state_warned = False
        self._load_state()

    # ── 학습 상태(접점 발열 기준) — 재부팅해도 유지 ───────────
    SAVE_EVERY = 300.0     # 초. eMMC 쓰기를 줄이려 평소엔 5분에 한 번, 기준 확정·재학습은 즉시

    def _load_state(self) -> None:
        if not self._state_file:
            return
        try:
            with open(self._state_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("예지 학습 상태 파일을 못 읽음(새로 학습): %s", exc)
            return
        self._pred.load_state(self._panel, data)
        bl = self._pred.contact_baseline(self._panel)
        log.info("예지 학습 상태 복원: 접점 기준 %s", "확정" if bl.ready else f"학습 중 {int(bl.progress()*100)}%")

    def _save_state(self, force: bool = False) -> None:
        if not self._state_file:
            return
        now = self._clock()
        if not force and now - self._saved_at < self.SAVE_EVERY:
            return
        state = self._pred.export_state(self._panel)
        self._saved_at = now
        if not state:
            return
        tmp = self._state_file + ".tmp"
        try:   # 원자적 교체 — 쓰는 도중 전원이 나가도 옛 파일은 온전
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            os.replace(tmp, self._state_file)
        except OSError as exc:
            if not self._state_warned:
                self._state_warned = True
                log.warning("예지 학습 상태 저장 실패(메모리로만 유지): %s", exc)

    # ── 입력 ──────────────────────────────────────────────
    def observe(self, readings) -> None:
        for r in readings:
            h = self._hist.get(r.key)
            if h is not None:
                h.append((r.timestamp, r.value, r.ok))

    def _last_gas_ts(self) -> float | None:
        """H2·VOC 중 가장 최근 유효 표본 시각(없으면 None)."""
        ts = [s[-1][0] for role in ("h2", "voc") if role in self._roles
              for s in [self._series(*self._roles[role], self.HISTORY)] if s]
        return max(ts) if ts else None

    def _series(self, dev: str, key: str, n: int) -> list:
        """inputs.series 규약: 최근 n개 '읽기' 중 유효 표본 [(ts, value)]."""
        h = self._hist.get(key)
        if not h:
            return []
        return [(ts, v) for ts, v, ok in list(h)[-n:] if ok and v is not None]

    # ── 한 주기 ──────────────────────────────────────────
    def step(self, now: float | None = None) -> dict:
        now = self._clock() if now is None else now
        asm = build_panel_inputs(self._roles, self._series, now)
        pend = asm["pending"]
        res = self._pred.assess_panel(self._panel, now, asm["inputs"], pend)
        self._pending, self._res = pend, res

        # fail-safe: 가스 입력이 오래 끊기면(센서 사망·배선 탈락) 벤트를 안전측으로
        lost = str(pend.get("fire", "")).startswith("데이터 끊김")
        if not lost:
            self._lost_since, self._failsafe = None, False
        else:
            # '끊김 판정'이 아니라 마지막 유효 가스 표본부터 센다 — 설정값이 말 그대로의 의미가 되게
            last = self._last_gas_ts()
            self._lost_since = last if last is not None else (self._lost_since or now)
            if (not self._failsafe
                    and now - self._lost_since >= self._cfg.predict.failsafe_after_seconds):
                self._failsafe = True
                log.warning("가스 입력 %d초 끊김 — fail-safe(%s)", now - self._lost_since,
                            "벤트 개방" if self._vent_failsafe == "open" else "상태 유지")
                if self._vent_failsafe == "open" and "vent" in self._acts:
                    self._pred.failsafe_vent(self._panel)

        self._drive(now)
        ev = (res.get("contact") or {}).get("baseline_event")
        if ev == "ready":
            log.info("접점 발열 기준 학습 완료 — 서서히 풀리는 접점도 감시")
        self._save_state(force=bool(ev))
        return self._report(now)

    def _why(self, kind: str, on: bool, mode: str, synced: bool, now: float) -> str:
        r = self._res or {}
        if mode == "manual":
            return "수동 조작"
        if kind == "vent" and self._failsafe and on and self._vent_failsafe == "open":
            return f"가스 센서 {int(now - (self._lost_since or now))}초 끊김 — fail-safe 개방"
        if synced:
            return "상태 동기화"
        if kind == "vent":
            f = r.get("fire", {})
            return (f"화재 징조 FRI {f.get('fri')} — " + " · ".join(f.get("reasons", [])[:2])) if on \
                else "환기 완료 — FRI 기준 아래 유지"
        d = r.get("dew", {})
        return " · ".join(d.get("reasons", [])[:1]) or ("결로 위험" if on else "결로 위험 해소")

    def _drive(self, now: float) -> None:
        """판정된 상태를 실제 출력으로. 바뀐 것만 쓰고, 보류 중인 자동 출력은 건드리지 않는다."""
        view = self._pred.actuators(self._panel)
        want = {"vent": view["vent"]["open"], "heater": view["heater"]["on"], "fan": view["fan"]["on"]}
        for kind, act in self._acts.items():
            mode = view[kind]["mode"]
            failsafe = kind == "vent" and self._failsafe and self._vent_failsafe == "open"
            if _GOVERNS[kind] in self._pending and mode != "manual" and not failsafe:
                continue
            if act.state == want[kind]:
                continue
            synced = act.state is None and not want[kind]   # 기동 후 첫 동기화(꺼짐 확인)
            ok = act.set(want[kind])
            why = self._why(kind, want[kind], mode, synced, now)
            self._actions.append({"actuator": kind, "on": want[kind], "mode": mode if not failsafe else "failsafe",
                                  "why": why, "ok": bool(ok), "ts": round(now, 3)})
            lvl = logging.WARNING if (want[kind] and kind == "vent") or not ok else logging.INFO
            log.log(lvl, "%s %s — %s%s", kind, on_word(kind, want[kind]), why, "" if ok else " (쓰기 실패, 재시도)")

    # ── 서버에서 내려온 명령(대시보드 수동 조작) ──────────────
    def apply_command(self, cmd: dict, now: float | None = None) -> bool:
        now = self._clock() if now is None else now
        actuator, action = str(cmd.get("actuator", "")), str(cmd.get("action", ""))
        if actuator == "contact" and action == "relearn":      # 접점 정비 후 기준 재학습
            if not all(r in self._roles for r in ("current", "contact_temp", "ambient")):
                log.info("접점 재학습 명령 — 이 CCM엔 접점 발열 입력이 없어 무시")
                return False
            self._pred.reset_contact_baseline(self._panel)
            self._save_state(force=True)
            log.info("원격 명령: 접점 발열 기준 재학습 시작")
            return True
        if actuator not in self._acts or action not in _VALID_ACTIONS.get(actuator, ()):
            log.warning("알 수 없는/이 CCM에 없는 명령 무시: %s", cmd)
            return False
        self._pred.set_actuator(self._panel, actuator, action)
        log.info("원격 명령: %s %s", actuator, action)
        self._drive(now)
        return True

    # ── 보고 ──────────────────────────────────────────────
    def _report(self, now: float) -> dict:
        r = self._res or {}
        view = self._pred.actuators(self._panel)
        def algo(k, num):
            a = r.get(k, {})
            return {"stage": a.get("stage"), num: a.get(num), "why": (a.get("reasons") or [])[:2]}
        acts = {}
        for kind, act in self._acts.items():
            mode = view[kind]["mode"]
            if kind == "vent" and self._failsafe and self._vent_failsafe == "open" and mode != "manual":
                mode = "failsafe"
            acts[kind] = {"on": act.state, "mode": mode}
        contact = algo("contact", "residual")
        bl = (r.get("contact") or {}).get("baseline")
        if bl:
            contact["baseline"] = {"status": bl.get("status"), "progress": bl.get("progress")}
        out = {"v": 1, "ts": round(now, 3), "panel": self._panel,
               "fire": algo("fire", "fri"), "contact": contact,
               "dew": algo("dew", "margin"), "actuators": acts,
               "failsafe": self._failsafe, "actions": self._actions}
        self._actions = []
        return out


def build_edge(cfg: Config, force_log: bool = False) -> EdgePredictor | None:
    """config의 [predict]가 켜져 있으면 엣지 예지를 만든다(아니면 None)."""
    if not cfg.predict.enabled:
        return None
    from .actuators import build_actuators
    acts = build_actuators(cfg.actuators, cfg.modbus, force_log=force_log)
    log.info("엣지 예지 켜짐: 역할 %s · 출력 %s", ", ".join(f"{k}={v}" for k, v in cfg.predict.roles.items()),
             ", ".join(acts) or "없음(판정·보고만)")
    return EdgePredictor(cfg, acts)
