"""예지보전 통합 엔진 — 판넬 단위로 화재·접점발열·결로를 한 번에 평가하고 액추에이터를 몬다.

세 코어(fire_risk·contact_heat·dewpoint)를 판넬(zone) 단위로 융합해:
  · 화재 위험지수(FRI) → 벤트 자동 개방/닫힘(히스테리시스, VentController)
  · 접점 발열(전류-온도 상관 이탈) → 위험 경보
  · 결로(이슬점 여유) → 히터·팬 선제 가동
접점 발열이 위험이면 화재 확증(current_abnormal)으로 넘겨 FRI를 끌어올린다(교차검증).

순수 로직 — 저장소/네트워크에 의존하지 않는다. 부작용(경보·이벤트)은 storage가 결과를
보고 처리한다. demo-api.js가 같은 로직을 미러한다(로직 갈라짐 방지).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .fire_risk import FireConfig, VentController, assess as assess_fire
from .contact_heat import ContactCfg, assess_contact
from .dewpoint import DewCfg, assess_dewpoint


class DewActuator:
    """결로 단계 → 히터/팬. danger=히터+팬, watch=팬, normal 유지되면 끔. 수동 우선.

    벤트와 같은 히스테리시스: 정상이 recover_hold 초 지속돼야 끈다(경계 깜빡임 방지).
    """

    def __init__(self, recover_hold: float = 60.0):
        self.recover_hold = float(recover_hold)
        self.heater = False
        self.fan = False
        self.manual = False
        self._calm_since = None

    def set_manual(self, heater: bool, fan: bool):
        self.manual = True
        self.heater = bool(heater)
        self.fan = bool(fan)

    def clear_manual(self):
        self.manual = False

    def step(self, stage: str, now: float):
        """이번 주기의 조치: "heater_fan"|"fan"|"off"|None."""
        if self.manual:
            return None
        if stage == "danger":
            self._calm_since = None
            if not (self.heater and self.fan):
                self.heater = True
                self.fan = True
                return "heater_fan"
            return None
        if stage == "watch":
            self._calm_since = None
            if not self.fan:
                self.fan = True
                return "fan"
            return None
        # normal
        if self.heater or self.fan:
            if self._calm_since is None:
                self._calm_since = now
            elif now - self._calm_since >= self.recover_hold:
                self.heater = False
                self.fan = False
                self._calm_since = None
                return "off"
        return None


@dataclass
class _PanelState:
    vent: VentController
    dew: DewActuator


class Predictor:
    """판넬별 컨트롤러 상태를 들고, 매 주기 assess_panel로 평가·작동한다."""

    def __init__(self, fire_cfg: FireConfig | None = None,
                 contact_cfg: ContactCfg | None = None,
                 dew_cfg: DewCfg | None = None):
        self.fire_cfg = fire_cfg or FireConfig.from_env()
        self.contact_cfg = contact_cfg or ContactCfg.from_env()
        self.dew_cfg = dew_cfg or DewCfg.from_env()
        self.autovent = (os.environ.get("JCC_FIRE_AUTOVENT") or "1") != "0"
        self._panels: dict[str, _PanelState] = {}

    def _state(self, panel: str) -> _PanelState:
        st = self._panels.get(panel)
        if st is None:
            st = _PanelState(vent=VentController(self.fire_cfg), dew=DewActuator())
            self._panels[panel] = st
        return st

    # ── 수동 오버라이드 ──────────────────────────────────────
    def set_actuator(self, panel: str, actuator: str, action: str) -> dict:
        """벤트/히터/팬 수동 조작. action: open|close|on|off|auto."""
        st = self._state(panel)
        if actuator == "vent":
            if action == "auto":
                st.vent.clear_manual()
            elif action in ("open", "close"):
                st.vent.set_manual(action == "open")
        elif actuator in ("heater", "fan"):
            if action == "auto":
                st.dew.clear_manual()
            else:
                heater = st.dew.heater
                fan = st.dew.fan
                if actuator == "heater":
                    heater = (action == "on")
                else:
                    fan = (action == "on")
                st.dew.set_manual(heater, fan)
        return self._actuator_view(st)

    def _actuator_view(self, st: _PanelState) -> dict:
        return {
            "vent": {"open": st.vent.open, "mode": "manual" if st.vent.manual else "auto"},
            "heater": {"on": st.dew.heater, "mode": "manual" if st.dew.manual else "auto"},
            "fan": {"on": st.dew.fan, "mode": "manual" if st.dew.manual else "auto"},
        }

    # ── 판넬 평가 ────────────────────────────────────────────
    def assess_panel(self, panel: str, now: float, inputs: dict, pending: dict | None = None) -> dict:
        """inputs = {
             h2:{value,series,baseline}, voc:{...}, temp:{value,series},
             smoke:bool,
             contact:{current,temp,ambient,history},
             dew:{temp,rh,surface,history},
           }  (없는 부분은 안전하게 생략)
        pending = {"fire"|"contact"|"dew": 사유} — 입력이 아직 덜 쌓였거나 끊긴 알고리즘.
          그 알고리즘은 판정을 보류하고 액추에이터 상태를 그대로 둔다(안전측: 가스 데이터가
          끊겼다고 열려 있던 벤트를 닫지 않는다).
        """
        pending = pending or {}
        st = self._state(panel)

        # 1) 접점 발열 — 먼저 평가해 화재 확증으로 넘긴다
        c = inputs.get("contact") or {}
        contact = assess_contact(c.get("current"), c.get("temp"), c.get("ambient"),
                                 c.get("history") or [], self.contact_cfg)

        # 2) 화재(FRI) — 접점 위험이면 current_abnormal로 확증
        sig = {
            "h2": inputs.get("h2") or {},
            "voc": inputs.get("voc") or {},
            "temp": inputs.get("temp") or {},
            "smoke": bool(inputs.get("smoke")),
            "current_abnormal": contact.stage == "danger" and "contact" not in pending,
        }
        fire = assess_fire(sig, self.fire_cfg)
        vent_action = None
        if self.autovent and "fire" not in pending:
            vent_action = st.vent.step(fire.fri, fire.stage, now)

        # 3) 결로 — 히터/팬
        d = inputs.get("dew") or {}
        dew = assess_dewpoint(d.get("temp"), d.get("rh"), d.get("surface"),
                              d.get("history") or [], self.dew_cfg)
        dew_action = None if "dew" in pending else st.dew.step(dew.stage, now)

        out = self._result(panel, now, st, fire, vent_action, contact, dew, dew_action)
        for k, why in pending.items():          # 보류된 알고리즘은 숫자 대신 사유를 보인다
            if k in out:
                out[k]["stage"] = "pending"
                out[k]["reasons"] = [why]
                for f in ("fri", "residual", "margin"):
                    if f in out[k]:
                        out[k][f] = None
        return out

    def _result(self, panel, now, st, fire, vent_action, contact, dew, dew_action) -> dict:
        return {
            "panel": panel,
            "ts": now,
            "fire": {
                "fri": fire.fri, "stage": fire.stage, "reasons": fire.reasons,
                "terms": fire.terms,
                "vent": {"open": st.vent.open, "mode": "manual" if st.vent.manual else "auto"},
                "action": vent_action,
                "autovent": self.autovent,
            },
            "contact": {
                "stage": contact.stage, "delta_t": contact.delta_t,
                "expected": contact.expected, "residual": contact.residual,
                "residual_slope": contact.residual_slope, "k": contact.k,
                "reasons": contact.reasons,
            },
            "dew": {
                "stage": dew.stage, "dew_point": dew.dew_point, "margin": dew.margin,
                "margin_slope": dew.margin_slope, "rh": dew.rh,
                "heater": st.dew.heater, "fan": st.dew.fan,
                "mode": "manual" if st.dew.manual else "auto",
                "action": dew_action, "reasons": dew.reasons,
            },
        }
