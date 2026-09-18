"""자가치유 L1 — 채널 자동 재시작.

신뢰 로드맵의 다음 단계: 문제를 '감지'만 하지 않고 안전한 선에서 '스스로 복구'한다.
L1은 가장 안전한 조치인 **센서 채널 재시작**만 자동으로 한다. 침묵(silent)·고착(stuck)
처럼 일시 장애로 흔히 채널 재시작 한 번에 풀리는 문제만 대상으로 삼는다.

안전장치(폭주·오작동 방지):
  - 대상 한정: silent·stuck 만 (드리프트·이상은 보정/공정 문제라 재시작 대상 아님).
  - 재시도 상한: 한 장애당 max_retry 회까지만. 넘으면 포기하고 사람에게 넘긴다(→ 상향·알림).
  - 쿨다운: 같은 센서에 cooldown 초 안에는 다시 시도 안 함.
  - 하루 총량: daily_cap 회 넘으면 그날은 자동개입 중단(무언가 크게 잘못된 신호).
  - 사람 우선: 사람이 확인(ack)한 경보는 자동개입하지 않는다(사람이 조치 중).
  - 전 과정 로그: heal_restart(시도)·heal_ok(복구)·heal_giveup(포기)로 감사 남김.

L2(CCM 재시작)·L3(사람 승인 필요한 위험 조치)는 아직 하지 않는다.

환경변수
  JCC_AUTOHEAL         "0"이면 끈다(기본 켜짐).
  JCC_HEAL_MAX_RETRY   장애당 최대 재시작 횟수(기본 2).
  JCC_HEAL_COOLDOWN    같은 센서 재시도 간격 초(기본 60).
  JCC_HEAL_DAILY_CAP   하루 자동 재시작 총량(기본 30).
"""
from __future__ import annotations

import os
import time

# 자동 재시작으로 풀릴 법한, 채널 수준의 일시 장애만 대상으로 한다.
HEAL_TARGETS = ("silent", "stuck")


class Healer:
    """활성 경보를 받아 L1 자동 복구를 수행하고, 개입 내역을 로그로 남긴다."""

    def __init__(self, storage, *, enabled=True, max_retry=2, cooldown=60.0, daily_cap=30):
        self.storage = storage
        self.enabled = enabled
        self.max_retry = max(1, int(max_retry))
        self.cooldown = float(cooldown)
        self.daily_cap = int(daily_cap)
        # (dev,key) -> {"n":시도횟수,"last":마지막시각,"episode":경보 raised_at,"gaveup":bool}
        self._attempts: dict = {}
        self._day = None
        self._day_count = 0

    @classmethod
    def from_env(cls, storage) -> "Healer":
        return cls(
            storage,
            enabled=(os.environ.get("JCC_AUTOHEAL", "1") != "0"),
            max_retry=int(os.environ.get("JCC_HEAL_MAX_RETRY") or 2),
            cooldown=float(os.environ.get("JCC_HEAL_COOLDOWN") or 60),
            daily_cap=int(os.environ.get("JCC_HEAL_DAILY_CAP") or 30),
        )

    def _roll_day(self, now: float) -> None:
        day = int(now // 86400)
        if day != self._day:
            self._day, self._day_count = day, 0

    def tick(self, active_alarms: list[dict], now: float | None = None) -> list[dict]:
        """한 주기 처리. 실제로 재시작을 건 항목 목록을 돌려준다(로그·테스트용)."""
        if not self.enabled:
            return []
        now = time.time() if now is None else now
        self._roll_day(now)
        acted: list[dict] = []
        seen: set = set()

        for a in active_alarms:
            key = a.get("sensor_key")
            if not key or a.get("kind") not in HEAL_TARGETS:
                continue                              # 센서 채널 장애만(CCM 침묵은 L2 영역)
            dev = a["device_id"]
            sk = (dev, key)
            seen.add(sk)
            rec = self._attempts.get(sk)
            if not rec or rec["episode"] != a.get("raised_at"):
                rec = {"n": 0, "last": 0.0, "episode": a.get("raised_at"), "gaveup": False}
                self._attempts[sk] = rec

            if a.get("acked_at"):
                continue                              # 사람이 조치 중 → 자동개입 보류
            if rec["n"] >= self.max_retry:
                if not rec["gaveup"]:
                    rec["gaveup"] = True
                    self.storage.log_event(
                        dev, key, "heal_giveup",
                        f"자동복구 실패: 채널 재시작 {self.max_retry}회로 복구 안 됨 — 사람 확인 필요",
                        source="system")
                continue
            if now - rec["last"] < self.cooldown:
                continue                              # 쿨다운
            if self._day_count >= self.daily_cap:
                continue                              # 하루 총량 초과 → 자동개입 중단

            rec["n"] += 1
            rec["last"] = now
            self._day_count += 1
            self.storage.restart_channel(
                dev, key,
                note=f"자동복구 L1: 채널 재시작 {rec['n']}/{self.max_retry}차 시도 ({a.get('kind')})")
            acted.append({"device_id": dev, "sensor_key": key, "attempt": rec["n"]})

        # 더 이상 경보가 없는 센서: 재시작 이력이 있으면 '복구 성공'으로 마감한다.
        for sk in list(self._attempts):
            if sk not in seen:
                rec = self._attempts.pop(sk)
                if rec["n"] > 0 and not rec["gaveup"]:
                    self.storage.log_event(
                        sk[0], sk[1], "heal_ok",
                        "자동복구 성공: 채널 재시작 후 정상 복귀", source="system")
        return acted
