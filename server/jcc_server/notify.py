"""알림 발송 프레임워크.

경보를 외부 채널로 내보낸다. 지금은 범용 웹훅(JCC_WEBHOOK)만 구현 — Slack/Teams
Incoming Webhook, 또는 사내 서버로 바로 쏠 수 있다. 실운영에서는 여기에 카카오
알림톡·문자(알리고/NHN 등)·이메일(SMTP)을 연결하는 자리다.

환경변수가 없으면 아무것도 안 한다(경보는 이벤트 로그·활성 경보 목록에 이미 남는다).
"""
from __future__ import annotations

import json
import os
import urllib.request


def dispatch(alarm: dict) -> bool:
    """경보 하나를 설정된 채널로 발송. 성공 여부 반환(미설정이면 False)."""
    hook = os.environ.get("JCC_WEBHOOK")
    if not hook:
        return False
    sev = str(alarm.get("severity", "")).upper()
    text = f"[JCC-CCM] {sev} · {alarm.get('device_id', '')} · {alarm.get('detail', '')}"
    body = json.dumps({"text": text}).encode("utf-8")
    try:
        req = urllib.request.Request(
            hook, data=body, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5).read()
        return True
    except Exception:  # noqa: BLE001 - 알림 실패가 감시를 죽이면 안 된다
        return False
