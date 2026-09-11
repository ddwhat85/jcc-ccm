"""HTTP(S) 전송.

MQTT 브로커를 두기 어려운 현장을 위한 대안. 표준 POST + JSON.
requests가 있으면 쓰고, 없으면 표준 라이브러리(urllib)로 떨어진다 —
CCM에 추가 패키지를 최소화하기 위해.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error

from ..config import Config


class HttpTransport:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._h = cfg.http

    def connect(self) -> None:
        # HTTP는 세션 유지가 필요 없다. 매 전송이 독립적.
        return None

    def send(self, payload: dict) -> bool:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._h.api_key:
            headers["Authorization"] = f"Bearer {self._h.api_key}"
        req = urllib.request.Request(self._h.url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._h.timeout_seconds) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            return False
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        return None
