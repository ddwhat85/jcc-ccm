"""알림 발송 — 카카오 알림톡 · 문자(SMS/LMS) · 웹훅.

경보를 담당자 휴대폰까지 실제로 보낸다. 채널은 환경변수로만 켜진다(키를 코드에
두지 않는다). 아무것도 설정 안 하면 조용히 아무 일도 하지 않는다 — 경보는 이미
활성 경보 목록과 이벤트 로그에 남아 있으므로 발송 실패가 감시를 막지 않는다.

지원 채널
  1) 카카오 알림톡 (알리고)  — JCC_ALIGO_* + JCC_ALIGO_SENDERKEY + JCC_ALIGO_TPL
     알림톡 실패 시 같은 요청에서 문자로 자동 대체발송(failover)된다.
  2) 문자 SMS/LMS (알리고)   — JCC_ALIGO_* (SENDERKEY/TPL 없이)
  3) 범용 웹훅               — JCC_WEBHOOK (Slack/Teams/사내 서버 등)

환경변수
  JCC_ALIGO_KEY        알리고 API Key
  JCC_ALIGO_USER       알리고 사용자 ID
  JCC_ALIGO_SENDER     발신번호(사전 등록된 번호, 예: 0212345678)
  JCC_ALIGO_RECEIVERS  수신번호. 쉼표로 여러 명 (예: 01011112222,01033334444)
  JCC_ALIGO_SENDERKEY  카카오 발신프로필 키(알림톡용)
  JCC_ALIGO_TPL        승인된 알림톡 템플릿 코드
  JCC_ALIMTALK_TEXT    알림톡 본문 틀. 승인된 템플릿과 **글자까지 동일**해야 한다.
                       치환자: {severity} {device} {detail} {time}
  JCC_WEBHOOK          웹훅 URL

⚠ 알림톡은 카카오 심사를 통과한 템플릿만 발송된다. 본문이 템플릿과 한 글자라도
  다르면 거부되므로, JCC_ALIMTALK_TEXT는 승인받은 문구 그대로 넣어야 한다.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

_ALIGO_SMS = "https://apis.aligo.in/send/"
_ALIGO_TOKEN = "https://kakaoapi.aligo.in/akv10/token/create/30/s/"
_ALIGO_ALIMTALK = "https://kakaoapi.aligo.in/akv10/alimtalk/send/"

_DEFAULT_TEXT = ("[JCC-CCM] {severity} 경보\n"
                 "장비: {device}\n"
                 "내용: {detail}\n"
                 "시각: {time}")


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _post_form(url: str, fields: dict, timeout: float = 8) -> dict:
    """알리고는 application/x-www-form-urlencoded + JSON 응답."""
    data = urllib.parse.urlencode(
        {k: v for k, v in fields.items() if v not in (None, "")}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError:
        return {"result_code": "-99", "message": raw[:200]}


def _receivers() -> list[str]:
    return [r.strip() for r in _env("JCC_ALIGO_RECEIVERS").split(",") if r.strip()]


def build_text(alarm: dict) -> str:
    """경보 하나를 사람이 읽는 문구로. 알림톡 템플릿과 문자 본문에 공통 사용."""
    tpl = _env("JCC_ALIMTALK_TEXT") or _DEFAULT_TEXT
    sev = {"crit": "위험", "warn": "주의"}.get(str(alarm.get("severity")), "알림")
    return tpl.format(
        severity=sev,
        device=alarm.get("device_id") or "-",
        detail=alarm.get("detail") or alarm.get("kind") or "-",
        time=time.strftime("%m-%d %H:%M:%S", time.localtime(alarm.get("raised_at") or time.time())),
    )


# ── 채널별 발송 ────────────────────────────────────────────
def send_webhook(text: str) -> dict:
    url = _env("JCC_WEBHOOK")
    if not url:
        return {"channel": "webhook", "skipped": True}
    try:
        req = urllib.request.Request(
            url, data=json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=8).read()
        return {"channel": "webhook", "ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"channel": "webhook", "ok": False, "error": str(exc)[:160]}


def send_sms(text: str) -> dict:
    """문자 발송. 90바이트 초과면 자동으로 LMS(장문)로 보낸다."""
    key, user, sender = _env("JCC_ALIGO_KEY"), _env("JCC_ALIGO_USER"), _env("JCC_ALIGO_SENDER")
    rcv = _receivers()
    if not (key and user and sender and rcv):
        return {"channel": "sms", "skipped": True}
    long_msg = len(text.encode("euc-kr", "replace")) > 90
    try:
        res = _post_form(_ALIGO_SMS, {
            "key": key, "user_id": user, "sender": sender,
            "receiver": ",".join(rcv), "msg": text,
            "msg_type": "LMS" if long_msg else "SMS",
            "title": "JCC-CCM 경보" if long_msg else None,
        })
        ok = str(res.get("result_code")) == "1"
        return {"channel": "sms", "ok": ok, "resp": res.get("message"), "sent": len(rcv)}
    except Exception as exc:  # noqa: BLE001
        return {"channel": "sms", "ok": False, "error": str(exc)[:160]}


def _alimtalk_token(key: str, user: str) -> str | None:
    try:
        res = _post_form(_ALIGO_TOKEN, {"apikey": key, "userid": user})
        if str(res.get("code")) == "0":
            return (res.get("token") or "")
    except Exception:  # noqa: BLE001
        return None
    return None


def send_alimtalk(text: str) -> dict:
    """카카오 알림톡. 실패하면 알리고가 같은 요청에서 문자로 대체발송(failover)."""
    key, user, sender = _env("JCC_ALIGO_KEY"), _env("JCC_ALIGO_USER"), _env("JCC_ALIGO_SENDER")
    senderkey, tpl = _env("JCC_ALIGO_SENDERKEY"), _env("JCC_ALIGO_TPL")
    rcv = _receivers()
    if not (key and user and sender and senderkey and tpl and rcv):
        return {"channel": "alimtalk", "skipped": True}
    token = _alimtalk_token(key, user)
    if not token:
        return {"channel": "alimtalk", "ok": False, "error": "토큰 발급 실패(키·ID 확인)"}
    fields = {
        "apikey": key, "userid": user, "token": token,
        "senderkey": senderkey, "tpl_code": tpl, "sender": sender,
        "failover": "Y",                      # 알림톡 실패 시 문자로 대체
    }
    for i, num in enumerate(rcv[:100], start=1):   # 알리고 1회 최대 100건
        fields[f"receiver_{i}"] = num
        fields[f"subject_{i}"] = "JCC-CCM 경보"
        fields[f"message_{i}"] = text
        fields[f"fsubject_{i}"] = "JCC-CCM 경보"   # 대체 문자 제목/본문
        fields[f"fmessage_{i}"] = text
    try:
        res = _post_form(_ALIGO_ALIMTALK, fields)
        ok = str(res.get("code")) == "0"
        return {"channel": "alimtalk", "ok": ok,
                "resp": res.get("message"), "sent": len(rcv[:100])}
    except Exception as exc:  # noqa: BLE001
        return {"channel": "alimtalk", "ok": False, "error": str(exc)[:160]}


# ── 통합 발송 ──────────────────────────────────────────────
def dispatch(alarm: dict, reason: str = "발생") -> list[dict]:
    """경보 하나를 설정된 모든 채널로 보낸다. 실패해도 예외를 밖으로 던지지 않는다.

    알림톡이 설정돼 있으면 알림톡(+실패 시 문자 대체)만 보내고, 문자 설정만 있으면
    문자로 보낸다. 같은 내용이 두 번 가지 않게 한다.
    """
    text = build_text(alarm)
    if reason and reason != "발생":
        text = f"[{reason}] " + text
    out: list[dict] = []
    at = send_alimtalk(text)
    out.append(at)
    if at.get("skipped"):          # 알림톡 미설정이면 문자로
        out.append(send_sms(text))
    out.append(send_webhook(text))
    return [r for r in out if not r.get("skipped")]


def configured_channels() -> list[str]:
    """지금 켜져 있는 채널 이름(설정 점검·테스트용)."""
    ch = []
    key, user, sender = _env("JCC_ALIGO_KEY"), _env("JCC_ALIGO_USER"), _env("JCC_ALIGO_SENDER")
    if key and user and sender and _receivers():
        if _env("JCC_ALIGO_SENDERKEY") and _env("JCC_ALIGO_TPL"):
            ch.append("alimtalk")
        else:
            ch.append("sms")
    if _env("JCC_WEBHOOK"):
        ch.append("webhook")
    return ch
