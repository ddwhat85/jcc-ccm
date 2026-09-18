"""자가치유 — 문제를 '감지'만 하지 않고 안전한 선에서 '스스로 복구'한다.

  L1 (채널 재시작): 센서 채널이 침묵(silent)·고착(stuck)이면 그 채널만 재시작.
                    가장 안전한 조치라 먼저 시도한다.
  L2 (CCM 재시작):  ① CCM이 통째로 침묵하거나 ② L1이 포기한 채널이면, 그 CCM을
                    통째로 재시작. 효과는 크지만 그 CCM 아래 전 센서가 잠깐 끊기므로
                    상한을 더 엄격히 둔다(재시도 적게·쿨다운 길게).

공통 안전장치(폭주·오작동 방지):
  - 재시도 상한·쿨다운·하루 총량으로 무한 재시작을 막는다. 넘으면 포기하고
    사람에게 넘긴다(→ 상향·알림).
  - 사람 우선: 사람이 확인(ack)한 경보는 자동개입하지 않는다(사람이 조치 중).
  - 전 과정 로그: L1은 heal_restart/heal_ok/heal_giveup, L2는 heal2_* 로 감사 남김.

L3(사람 승인이 필요한 위험 조치)는 아직 하지 않는다.

환경변수
  JCC_AUTOHEAL              "0"이면 자가치유 전체를 끈다(기본 켜짐).
  JCC_HEAL_MAX_RETRY        L1 장애당 최대 채널 재시작 횟수(기본 2).
  JCC_HEAL_COOLDOWN         L1 같은 센서 재시도 간격 초(기본 60).
  JCC_HEAL_DAILY_CAP        L1 하루 채널 재시작 총량(기본 30).
  JCC_AUTOHEAL_L2           "0"이면 L2(CCM 재시작)만 끈다(기본 켜짐, L1은 유지).
  JCC_HEAL_L2_MAX_RETRY     L2 CCM당 최대 재시작 횟수(기본 1 — 재부팅은 무겁다).
  JCC_HEAL_L2_COOLDOWN      L2 같은 CCM 재시도 간격 초(기본 180).
  JCC_HEAL_L2_DAILY_CAP     L2 하루 CCM 재시작 총량(기본 10).
  JCC_HEAL_L2_ON_CHANNEL_FAIL  "0"이면 'L1 실패→CCM 재시작' 승격을 끈다(기본 켜짐).
"""
from __future__ import annotations

import os
import time

# 자동 재시작으로 풀릴 법한, 채널 수준의 일시 장애만 L1 대상으로 한다.
HEAL_TARGETS = ("silent", "stuck")


class Healer:
    """활성 경보를 받아 L1(채널)·L2(CCM) 자동 복구를 수행하고, 내역을 로그로 남긴다."""

    def __init__(self, storage, *, enabled=True, max_retry=2, cooldown=60.0, daily_cap=30,
                 l2_enabled=True, l2_max_retry=1, l2_cooldown=180.0, l2_daily_cap=10,
                 l2_on_channel_fail=True):
        self.storage = storage
        self.enabled = enabled
        self.max_retry = max(1, int(max_retry))
        self.cooldown = float(cooldown)
        self.daily_cap = int(daily_cap)
        # L2 (CCM 단위)
        self.l2_enabled = l2_enabled
        self.l2_max_retry = max(1, int(l2_max_retry))
        self.l2_cooldown = float(l2_cooldown)
        self.l2_daily_cap = int(l2_daily_cap)
        self.l2_on_channel_fail = l2_on_channel_fail
        # (dev,key) -> {"n","last","episode","gaveup"}   L1 채널 시도 기록
        self._attempts: dict = {}
        # dev -> {"n","last","episode","gaveup","reason"}   L2 CCM 시도 기록
        self._dev_attempts: dict = {}
        self._day = None
        self._day_count = 0
        self._l2_day_count = 0

    @classmethod
    def from_env(cls, storage) -> "Healer":
        return cls(
            storage,
            enabled=(os.environ.get("JCC_AUTOHEAL", "1") != "0"),
            max_retry=int(os.environ.get("JCC_HEAL_MAX_RETRY") or 2),
            cooldown=float(os.environ.get("JCC_HEAL_COOLDOWN") or 60),
            daily_cap=int(os.environ.get("JCC_HEAL_DAILY_CAP") or 30),
            l2_enabled=(os.environ.get("JCC_AUTOHEAL_L2", "1") != "0"),
            l2_max_retry=int(os.environ.get("JCC_HEAL_L2_MAX_RETRY") or 1),
            l2_cooldown=float(os.environ.get("JCC_HEAL_L2_COOLDOWN") or 180),
            l2_daily_cap=int(os.environ.get("JCC_HEAL_L2_DAILY_CAP") or 10),
            l2_on_channel_fail=(os.environ.get("JCC_HEAL_L2_ON_CHANNEL_FAIL", "1") != "0"),
        )

    def _roll_day(self, now: float) -> None:
        day = int(now // 86400)
        if day != self._day:
            self._day, self._day_count, self._l2_day_count = day, 0, 0

    def tick(self, active_alarms: list[dict], now: float | None = None) -> list[dict]:
        """한 주기 처리. 실제로 재시작을 건 항목 목록을 돌려준다(로그·테스트용)."""
        if not self.enabled:
            return []
        now = time.time() if now is None else now
        self._roll_day(now)
        acted: list[dict] = []

        # ── L1: 센서 채널 재시작 ──────────────────────────────
        seen: set = set()
        for a in active_alarms:
            key = a.get("sensor_key")
            if not key or a.get("kind") not in HEAL_TARGETS:
                continue                              # 센서 채널 장애만(CCM 침묵은 L2)
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
                        f"자동복구 실패: 채널 재시작 {self.max_retry}회로 복구 안 됨"
                        + (" — CCM 재시작으로 승격" if (self.l2_enabled and self.l2_on_channel_fail)
                           else " — 사람 확인 필요"),
                        source="system")
                continue
            if now - rec["last"] < self.cooldown:
                continue                              # 쿨다운
            if self._day_count >= self.daily_cap:
                continue                              # 하루 총량 초과

            rec["n"] += 1
            rec["last"] = now
            self._day_count += 1
            self.storage.restart_channel(
                dev, key,
                note=f"자동복구 L1: 채널 재시작 {rec['n']}/{self.max_retry}차 시도 ({a.get('kind')})")
            acted.append({"level": 1, "device_id": dev, "sensor_key": key, "attempt": rec["n"]})

        # 더 이상 경보가 없는 센서: 재시작 이력이 있으면 '복구 성공'으로 마감.
        for sk in list(self._attempts):
            if sk not in seen:
                rec = self._attempts.pop(sk)
                if rec["n"] > 0 and not rec["gaveup"]:
                    self.storage.log_event(
                        sk[0], sk[1], "heal_ok",
                        "자동복구 성공: 채널 재시작 후 정상 복귀", source="system")

        # ── L2: CCM 재시작 ────────────────────────────────────
        if self.l2_enabled:
            acted += self._tick_l2(active_alarms, now)
        return acted

    def _tick_l2(self, active_alarms: list[dict], now: float) -> list[dict]:
        acted: list[dict] = []
        # 재시작 대상 CCM 모으기: dev -> (reason, episode, acked)
        targets: dict = {}
        for a in active_alarms:
            if a.get("kind") == "silent" and not a.get("sensor_key"):
                targets[a["device_id"]] = ("silent", a.get("raised_at"), bool(a.get("acked_at")))
        if self.l2_on_channel_fail:
            # L1이 포기한 채널의 부모 CCM으로 승격(형제 센서까지 잠깐 끊김 — 상한 엄격).
            for (dev, _key), rec in self._attempts.items():
                if rec.get("gaveup"):
                    targets.setdefault(dev, ("channel_fail", rec["episode"], False))

        seen_dev: set = set()
        for dev, (reason, episode, acked) in targets.items():
            seen_dev.add(dev)
            drec = self._dev_attempts.get(dev)
            if not drec or drec["episode"] != episode:
                drec = {"n": 0, "last": 0.0, "episode": episode, "gaveup": False, "reason": reason}
                self._dev_attempts[dev] = drec
            if acked:
                continue                              # 사람이 조치 중 → 보류
            if drec["n"] >= self.l2_max_retry:
                if not drec["gaveup"]:
                    drec["gaveup"] = True
                    self.storage.log_event(
                        dev, "", "heal2_giveup",
                        f"CCM 자동 재시작 {self.l2_max_retry}회로도 복구 안 됨 — 사람 확인 필요",
                        source="system")
                continue
            if now - drec["last"] < self.l2_cooldown:
                continue
            if self._l2_day_count >= self.l2_daily_cap:
                continue

            drec["n"] += 1
            drec["last"] = now
            self._l2_day_count += 1
            why = "CCM 침묵" if reason == "silent" else "채널 재시작 실패 → 승격"
            self.storage.restart_device(
                dev, note=f"자동복구 L2: CCM 재시작 {drec['n']}/{self.l2_max_retry}차 시도 ({why})")
            acted.append({"level": 2, "device_id": dev, "attempt": drec["n"]})

        for dev in list(self._dev_attempts):
            if dev not in seen_dev:
                drec = self._dev_attempts.pop(dev)
                if drec["n"] > 0 and not drec["gaveup"]:
                    self.storage.log_event(
                        dev, "", "heal2_ok",
                        "자동복구 성공: CCM 재시작 후 정상 복귀", source="system")
        return acted
