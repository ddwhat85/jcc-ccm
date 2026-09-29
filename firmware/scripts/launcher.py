#!/usr/bin/env python3
"""JCC-CCM 런처 — systemd가 이걸 실행한다. OTA 시험 부팅을 관리하고 실패하면 되돌린다.

⚠ 이 파일은 OTA로 교체되지 않는 '고정' 파일이다. 새 펌웨어가 문법 오류로 아예 안
뜨더라도 되돌릴 수 있어야 하므로, releases/의 어떤 코드도 import하지 않는다(표준
라이브러리만). 설치: /opt/jcc-ccm/launcher.py (scripts/install.sh).

매 부팅:
  current(지금 버전)가 시험 중(pending)이면 부팅 횟수를 센다.
  MAX_BOOTS번 넘게 부팅했거나(계속 죽음) TRIAL_DEADLINE이 지났는데 아직 확정이 안 됐으면
  → 이전 버전으로 current를 되돌리고, 그 버전은 bad로 기록(다시 받지 않음).
  그다음 해당 버전 폴더를 PYTHONPATH로 잡아 `python -m jcc_ccm <인자>`로 교체 실행한다.
"""
from __future__ import annotations

import json
import os
import sys
import time

BASE = os.environ.get("JCC_BASE", "/opt/jcc-ccm")
MAX_BOOTS = 3              # 시험 중 이만큼 넘게 다시 뜨면(계속 죽음) 되돌린다
TRIAL_DEADLINE = 900.0     # 초. jcc_ccm/ota.py 의 TRIAL_DEADLINE과 같아야 한다


def decide(state: dict, current: str, now: float):
    """(새 state, 새 current, 조치) — 조치는 run | trial | rollback. 순수 함수(테스트 대상)."""
    p = state.get("pending")
    if not p or p.get("version") != current:
        return state, current, "run"
    boots = int(p.get("boots", 0)) + 1
    expired = now - float(p.get("since", now)) > TRIAL_DEADLINE
    if boots > MAX_BOOTS or expired:
        bad = [b for b in state.get("bad", []) if b != current] + [current]
        new = dict(state, pending=None, bad=bad[-20:],
                   rolled_back={"version": current, "to": p.get("prev", ""), "at": now,
                                "reason": "시험 기한 초과" if expired else f"부팅 {boots - 1}회 실패"})
        return new, p.get("prev", ""), "rollback"
    return dict(state, pending=dict(p, boots=boots)), current, "trial"


def _read(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return default


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def prepare(base: str, now: float):
    """state·current를 읽어 결정하고 기록한다. (조치, 실행할 버전 폴더)."""
    state_path = os.path.join(base, "ota", "state.json")
    try:
        state = json.loads(_read(state_path, "{}") or "{}")
        if not isinstance(state, dict):
            state = {}
    except ValueError:
        state = {}
    current = (_read(os.path.join(base, "current"), "") or "").strip()
    new_state, new_current, action = decide(state, current, now)
    if new_state is not state:
        _write(state_path, json.dumps(new_state, ensure_ascii=False))
    if new_current != current:
        _write(os.path.join(base, "current"), new_current + "\n")
    rel = os.path.join(base, "releases", new_current) if new_current else base
    if not os.path.isdir(os.path.join(rel, "jcc_ccm")):      # 폴더가 없으면 최초 설치 위치로
        rel = base
    return action, rel


def main(argv: list[str]) -> int:
    action, rel = prepare(BASE, time.time())
    if action != "run":
        print(f"[launcher] OTA {action} → {rel}", file=sys.stderr, flush=True)
    env = dict(os.environ, PYTHONPATH=rel)
    os.execve(sys.executable, [sys.executable, "-m", "jcc_ccm", *argv], env)
    return 0   # execve가 돌아오지 않음


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
