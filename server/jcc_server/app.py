"""HTTP 앱 — 라우팅과 엔드포인트.

표준 라이브러리 http.server 위에 얇은 라우터를 얹었다. ThreadingHTTPServer로
여러 CCM이 동시에 POST해도 처리한다.

엔드포인트
  POST /v1/telemetry             펌웨어가 보내는 수집 데이터 (http_transport와 짝)
  GET  /api/devices              CCM 목록 + 각 센서 최신값
  GET  /api/panels               판넬 단위로 묶은 목록 (판넬 1개 = CCM 여러 대)
  GET  /api/devices/{id}/history?sensor=KEY&limit=N   센서 이력
  GET  /health                   상태 확인
  GET  /                         대시보드 (static/index.html)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

from .storage import Storage

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

# 자동 탐색은 펌웨어(jcc_ccm.discovery)의 실제 코드를 재사용한다. 같은 저장소의
# firmware 패키지를 경로에 얹어, 실기 CCM이 돌릴 코드와 동일한 것을 시뮬레이션한다.
_FIRMWARE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "firmware")


def _run_discovery() -> dict:
    if _FIRMWARE_DIR not in sys.path:
        sys.path.insert(0, _FIRMWARE_DIR)
    from jcc_ccm.discovery import discover_sim
    return discover_sim()

# 선택적 인증: 환경변수 JCC_API_KEY가 설정되면 POST에 Bearer 토큰을 요구한다.
_API_KEY = os.environ.get("JCC_API_KEY", "")

# 대시보드 접근 비밀번호(공용 1개). 환경변수 JCC_DASHBOARD_PW가 설정됐을 때만
# 로그인 화면이 뜬다. 비워두면(개발·지인 데모) 인증 없이 바로 열린다.
#   운영 서버:  set JCC_DASHBOARD_PW=원하는비번   (코드·저장소에 넣지 않는다)
# 세션은 비밀번호로 서명한 토큰을 쿠키에 담아 유지한다(별도 시크릿 불필요).
_DASH_PW = os.environ.get("JCC_DASHBOARD_PW", "")


def _session_token() -> str:
    """비밀번호를 키로 서명한 세션 토큰. 비번을 모르면 위조 불가."""
    return hmac.new(_DASH_PW.encode("utf-8"), b"jcc-auth-v1", hashlib.sha256).hexdigest()


# 로그인 화면(단독 HTML). 대시보드와 같은 다크 톤. 비번 확인 후 /api/login → 쿠키 → 새로고침.
_LOGIN_HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JCC-CCM · 로그인</title>
<style>
  :root{--bg:#0b1220;--card:#131c2e;--line:#26324a;--ink:#e7edf7;--dim:#8b98b0;
        --accent:#3b82f6;--accent2:#22c55e;--err:#f43f5e}
  *{box-sizing:border-box}
  html,body{height:100%;margin:0}
  body{background:radial-gradient(1200px 600px at 50% -10%,#16233c 0%,var(--bg) 60%);
       color:var(--ink);font:15px/1.5 system-ui,"Segoe UI",Roboto,"Malgun Gothic",sans-serif;
       display:grid;place-items:center;padding:24px}
  .card{width:100%;max-width:360px;background:var(--card);border:1px solid var(--line);
        border-radius:18px;padding:34px 30px;box-shadow:0 24px 60px rgba(0,0,0,.45)}
  .brand{display:flex;align-items:center;gap:10px;margin-bottom:6px}
  .logo{width:36px;height:36px;border-radius:9px;background:linear-gradient(135deg,var(--accent),#1e40af);
        display:grid;place-items:center;font-weight:800;font-size:15px;letter-spacing:.5px}
  .brand b{font-size:18px;letter-spacing:.3px}
  .sub{color:var(--dim);font-size:13px;margin:2px 0 22px}
  label{display:block;font-size:12px;color:var(--dim);margin-bottom:7px}
  input{width:100%;padding:12px 14px;border-radius:11px;border:1px solid var(--line);
        background:#0e1626;color:var(--ink);font-size:15px;outline:none;transition:border-color .12s}
  input:focus{border-color:var(--accent)}
  button{width:100%;margin-top:16px;padding:12px;border:none;border-radius:11px;cursor:pointer;
         background:linear-gradient(135deg,var(--accent),#2563eb);color:#fff;font-size:15px;
         font-weight:700;transition:filter .12s}
  button:hover{filter:brightness(1.1)}
  button:disabled{opacity:.6;cursor:default}
  .err{color:var(--err);font-size:13px;margin-top:12px;min-height:18px}
  .foot{margin-top:20px;color:var(--dim);font-size:11px;text-align:center}
</style></head><body>
  <form class="card" id="f" autocomplete="off">
    <div class="brand"><div class="logo">JCC</div><b>JCC-CCM</b></div>
    <div class="sub">스마트 판넬 모니터링 · 접근 인증</div>
    <label for="pw">비밀번호</label>
    <input id="pw" type="password" autofocus autocomplete="current-password" placeholder="비밀번호를 입력하세요">
    <button id="b" type="submit">로그인</button>
    <div class="err" id="e"></div>
    <div class="foot">JCC Solution</div>
  </form>
<script>
  const f=document.getElementById('f'),pw=document.getElementById('pw'),
        e=document.getElementById('e'),b=document.getElementById('b');
  f.addEventListener('submit',async(ev)=>{
    ev.preventDefault(); e.textContent=''; b.disabled=true; b.textContent='확인 중…';
    try{
      const r=await fetch('/api/login',{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw.value})});
      if(r.ok){ location.replace('/'); return; }
      const j=await r.json().catch(()=>({}));
      e.textContent=j.error||'로그인에 실패했습니다.';
    }catch(_){ e.textContent='서버에 연결할 수 없습니다.'; }
    b.disabled=false; b.textContent='로그인'; pw.select();
  });
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "JCC-CCM/0.1"
    storage: Storage  # 서버 생성 시 주입

    # ── 공통 응답 헬퍼 ──────────────────────────────────────
    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text: str, status: int = 200, ctype: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: ANN001 - 조용한 접근 로그
        pass

    # ── 인증 (공용 비밀번호) ────────────────────────────────
    def _cookies(self) -> dict:
        out = {}
        for part in (self.headers.get("Cookie", "") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                out[k] = v
        return out

    def _authed(self) -> bool:
        """비밀번호가 설정돼 있지 않으면 항상 통과. 설정돼 있으면 세션 쿠키 검사."""
        if not _DASH_PW:
            return True
        return hmac.compare_digest(self._cookies().get("jcc_session", ""), _session_token())

    # ── GET ────────────────────────────────────────────────
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        # 로그인 게이트: 비번이 걸려 있고 미인증이면 대시보드/API 접근 차단.
        # (상태 확인 /health 와 이미지 /img/* 는 로그인 화면 표시용으로 열어둔다)
        if _DASH_PW and not self._authed() and path != "/health" and not path.startswith("/img/"):
            if path in ("/", "/index.html"):
                return self._serve_login()
            return self._json({"error": "unauthorized"}, 401)

        if path in ("/", "/index.html"):
            return self._serve_dashboard()
        if path == "/health":
            return self._json({"ok": True, "ts": time.time()})
        if path == "/api/devices":
            return self._json({"devices": self.storage.list_devices()})
        if path == "/api/panels":
            return self._json({"panels": self.storage.list_panels()})
        if path == "/api/alarms":
            return self._json({"alarms": self.storage.list_active_alarms()})
        if path == "/api/healthcheck":
            rep = self.storage.diagnose_all()
            c = rep.get("counts", {})
            self.storage.log_event("", "", "healthcheck",
                                   f"전체 점검: {rep.get('summary','')} "
                                   f"(정상 {c.get('pass',0)}·주의 {c.get('warn',0)}·이상 {c.get('fail',0)})")
            return self._json(rep)
        if path == "/api/auth/status":
            return self._json({"enabled": bool(_DASH_PW)})
        if path == "/api/notify/status":
            from .notify import configured_channels
            return self._json({"channels": configured_channels()})
        if path == "/api/events":
            q = parse_qs(parsed.query)
            dev = (q.get("device_id") or [""])[0]
            key = (q.get("sensor_key") or [""])[0]
            try:
                limit = int((q.get("limit") or ["50"])[0])
            except ValueError:
                limit = 50
            return self._json({"events": self.storage.list_events(dev, key, limit)})

        if path.startswith("/img/"):
            return self._serve_image(path[len("/img/"):])

        m = re.fullmatch(r"/api/devices/([^/]+)/history", path)
        if m:
            device_id = m.group(1)
            q = parse_qs(parsed.query)
            sensor = (q.get("sensor") or [""])[0]
            if not sensor:
                return self._json({"error": "sensor 파라미터가 필요합니다"}, 400)
            try:
                limit = int((q.get("limit") or ["200"])[0])
            except ValueError:
                return self._json({"error": "limit은 정수여야 합니다"}, 400)
            return self._json({
                "device_id": device_id,
                "sensor": sensor,
                "points": self.storage.history(device_id, sensor, limit),
            })

        self._json({"error": "not found"}, 404)

    # ── POST ───────────────────────────────────────────────
    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        # 로그인/로그아웃은 인증 이전에 처리한다.
        if parsed.path == "/api/login":
            return self._login()
        if parsed.path == "/api/logout":
            return self._logout()
        # 그 밖의 대시보드 API는 로그인 필요(장비 텔레메트리는 Bearer 키로 따로 인증).
        if _DASH_PW and not self._authed() and parsed.path != "/v1/telemetry":
            return self._json({"error": "unauthorized"}, 401)
        if parsed.path == "/api/discover":
            return self._discover()
        if parsed.path == "/api/channel":
            return self._channel()
        if parsed.path == "/api/setting":
            return self._setting()
        if parsed.path == "/api/device/command":
            return self._device_command()
        if parsed.path == "/api/diagnose":
            return self._diagnose()
        if parsed.path == "/api/alarm/ack":
            return self._ack()
        if parsed.path == "/api/notify/test":
            return self._notify_test()
        if parsed.path == "/api/discover/report":
            return self._discover_report()
        if parsed.path == "/api/panel/name":
            return self._panel_name()
        if parsed.path == "/api/channels/enable-all":
            n = self.storage.enable_all_channels()
            return self._json({"ok": True, "enabled": n})
        if parsed.path != "/v1/telemetry":
            return self._json({"error": "not found"}, 404)

        if _API_KEY:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {_API_KEY}":
                return self._json({"error": "unauthorized"}, 401)

        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > 1_000_000:
            return self._json({"error": "빈 요청이거나 너무 큼"}, 400)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        if not isinstance(payload, dict) or "device_id" not in payload:
            return self._json({"error": "device_id가 필요합니다"}, 400)

        try:
            n = self.storage.ingest(payload)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": f"저장 실패: {exc}"}, 500)
        return self._json({"ok": True, "stored": n})

    # ── 자동 탐색 (AI 자동연결) ──────────────────────────────
    def _discover(self) -> None:
        """펌웨어의 탐색 로직을 실행해 토폴로지를 자동 구성한다.

        실기에서는 각 CCM이 자기 버스를 스캔해 결과를 올린다. 여기(시뮬레이션)에서는
        서버가 같은 펌웨어 코드(jcc_ccm.discovery)를 VirtualBus로 실행한다.
        """
        try:
            result = _run_discovery()
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": f"탐색 실패: {exc}"}, 500)
        try:
            self.storage.set_discovery(result)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": f"저장 실패: {exc}"}, 500)

        ccms = result.get("ccms") or []
        sensors = [s for c in ccms for s in (c.get("sensors") or [])]
        inferred = sum(1 for s in sensors if s.get("confidence") == "추정")
        return self._json({
            "ok": True,
            "panel_name": result.get("panel_name", ""),
            "ccms": len(ccms),
            "sensors": len(sensors),
            "identified": len(sensors) - inferred,
            "inferred": inferred,
        })

    # ── 판넬 이름 변경 ───────────────────────────────────────
    def _panel_name(self) -> None:
        """{panel, name} — 판넬에 사람이 붙인 이름을 저장. 빈 이름이면 지정 해제."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > 100_000:
            return self._json({"error": "빈 요청"}, 400)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        panel = str(body.get("panel", "")).strip()
        name = str(body.get("name", "")).strip()[:60]
        if not panel:
            return self._json({"error": "panel이 필요합니다"}, 400)
        self.storage.set_panel_name(panel, name)
        self.storage.log_event("", "", "rename",
                               f"판넬 이름 변경: {panel} → {name or '(기본값으로 복귀)'}")
        return self._json({"ok": True, "panel": panel, "name": name})

    # ── 실기 자동 탐색 보고 (CCM 한 대가 자기 인벤토리를 올림) ──
    def _discover_report(self) -> None:
        """실기 경로: 각 CCM이 자기 버스를 스캔해 그 결과를 여기로 보고한다.

        같은 판넬(panel)을 공유하는 CCM들이 각자 보고하면 서버가 판넬 아래로 모은다.
        한 대가 보고해도 다른 CCM의 인벤토리는 건드리지 않는다(장비 단위로만 갱신).

        받는 형태 (둘 다 허용)
          {"panel":"panel-01","panel_name":"스마트 판넬","site":"...",
           "device_id":"ccm-2665","sensors":[{key,name,unit,kind,...}, ...]}
          {"panel":..., "ccms":[{"device_id":..., "sensors":[...]}, ...]}
        """
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > 5_000_000:
            return self._json({"error": "빈 요청이거나 너무 큼"}, 400)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        if not isinstance(body, dict):
            return self._json({"error": "객체가 필요합니다"}, 400)

        ccms = body.get("ccms")
        if not ccms:                                   # 단일 CCM 보고 형태를 정규화
            dev = str(body.get("device_id") or "")
            if not dev:
                return self._json({"error": "device_id 또는 ccms가 필요합니다"}, 400)
            ccms = [{"device_id": dev, "sensors": body.get("sensors") or []}]
        if not isinstance(ccms, list):
            return self._json({"error": "ccms는 배열이어야 합니다"}, 400)

        result = {
            "panel": body.get("panel", ""),
            "panel_name": body.get("panel_name", ""),
            "site": body.get("site", ""),
            "ccms": ccms,
        }
        try:
            n = self.storage.set_discovery(result)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": f"저장 실패: {exc}"}, 500)
        devs = [str(c.get("device_id", "")) for c in ccms if isinstance(c, dict)]
        self.storage.log_event(devs[0] if len(devs) == 1 else "", "", "discover",
                               f"자동 탐색 보고: {', '.join(devs)} · 센서 {n}개")
        return self._json({"ok": True, "devices": devs, "sensors": n,
                           "panel": result["panel"]})

    # ── 센서 채널 활성/비활성 (CCM에 보내는 명령) ────────────
    def _channel(self) -> None:
        """{device_id, sensor_key, enabled} — 센서 채널을 켜고/끈다.

        실기에서는 CCM에 명령이 전달돼 그 채널의 폴링을 멈춘다. 시뮬레이션에서는
        서버가 상태를 기록하고 그 채널의 텔레메트리를 버려, SW와 HW를 일치시킨다.
        """
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > 100_000:
            return self._json({"error": "빈 요청"}, 400)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        dev = str(body.get("device_id", ""))
        key = str(body.get("sensor_key", ""))
        enabled = bool(body.get("enabled", True))
        if not dev or not key:
            return self._json({"error": "device_id와 sensor_key가 필요합니다"}, 400)
        ok = self.storage.set_channel(dev, key, enabled)
        if ok:
            self.storage.log_event(dev, key, "channel_on" if enabled else "channel_off",
                                   "센서 켜기" if enabled else "센서 끄기")
        return self._json({"ok": ok, "device_id": dev, "sensor_key": key, "enabled": enabled})

    # ── 센서 설정 (셋팅값·알람 상/하한) ──────────────────────
    def _setting(self) -> None:
        """{device_id, sensor_key, setpoint?, alarm_min?, alarm_max?} — 사용자 설정 저장.

        넘어온 필드만 갱신한다(누락=변경없음, ""=기본값으로 해제). 재탐색해도 유지된다.
        """
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > 100_000:
            return self._json({"error": "빈 요청"}, 400)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        dev = str(body.get("device_id", ""))
        key = str(body.get("sensor_key", ""))
        if not dev or not key:
            return self._json({"error": "device_id와 sensor_key가 필요합니다"}, 400)
        rm = body.get("relay_modes")
        new = self.storage.set_setting(
            dev, key,
            setpoint=body.get("setpoint"),
            alarm_min=body.get("alarm_min"),
            alarm_max=body.get("alarm_max"),
            alarm_warn=body.get("alarm_warn"),
            relay_modes=rm if isinstance(rm, dict) else None,
        )
        detail = (f"셋팅={new.get('setpoint')} 경고={new.get('alarm_warn')} "
                  f"위험={new.get('alarm_min')}~{new.get('alarm_max')}")
        self.storage.log_event(dev, key, "setting", detail)
        return self._json({"ok": True, "device_id": dev, "sensor_key": key, **new})

    # ── CCM 전원 명령 (재시작/전원끄기) ──────────────────────
    def _device_command(self) -> None:
        """{device_id, action} — CCM에 재시작/전원끄기 명령. 실기=SSH reboot/poweroff."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > 100_000:
            return self._json({"error": "빈 요청"}, 400)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        dev = str(body.get("device_id", ""))
        action = str(body.get("action", ""))
        if not dev or action not in ("restart", "shutdown"):
            return self._json({"error": "device_id와 action(restart|shutdown)이 필요합니다"}, 400)
        ok = self.storage.device_command(dev, action)
        if ok:
            self.storage.log_event(dev, "", action,
                                   "재시작 명령" if action == "restart" else "전원 끄기 명령")
        return self._json({"ok": ok, "device_id": dev, "action": action})

    # ── 자가진단 ────────────────────────────────────────────
    def _diagnose(self) -> None:
        """{device_id, sensor_key?} — 센서/CCM 자가진단 실행 후 리포트 반환."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > 100_000:
            return self._json({"error": "빈 요청"}, 400)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        dev = str(body.get("device_id", ""))
        key = str(body.get("sensor_key", ""))
        if not dev:
            return self._json({"error": "device_id가 필요합니다"}, 400)
        report = self.storage.diagnose(dev, key)
        if report.get("ok"):
            self.storage.log_event(dev, key, "diagnose", "자가진단: " + report.get("summary", ""))
        return self._json(report)

    # ── 경보 확인(ack) ───────────────────────────────────────
    def _ack(self) -> None:
        """{alarm_id, by?} — 활성 경보를 확인 처리(에스컬레이션 중단)."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > 100_000:
            return self._json({"error": "빈 요청"}, 400)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        try:
            alarm_id = int(body.get("alarm_id"))
        except (TypeError, ValueError):
            return self._json({"error": "alarm_id가 필요합니다"}, 400)
        ok = self.storage.ack_alarm(alarm_id, str(body.get("by") or "operator"))
        return self._json({"ok": ok, "alarm_id": alarm_id})

    # ── 알림 테스트 발송 ─────────────────────────────────────
    def _notify_test(self) -> None:
        """설정된 알림 채널로 테스트 메시지를 1건 보낸다(운영자가 직접 누를 때만)."""
        from .notify import configured_channels, dispatch
        ch = configured_channels()
        if not ch:
            return self._json({"ok": False, "channels": [],
                               "error": "설정된 알림 채널이 없습니다. 환경변수(JCC_ALIGO_* / JCC_WEBHOOK)를 확인하세요."}, 400)
        result = dispatch({
            "device_id": "TEST", "kind": "test", "severity": "warn",
            "detail": "테스트 발송입니다. 이 메시지가 보이면 알림 연결이 정상입니다.",
            "raised_at": time.time(),
        }, "테스트")
        self.storage.log_event("", "", "notify_test", f"알림 테스트 발송: {', '.join(ch)}")
        return self._json({"ok": True, "channels": ch, "result": result})

    # ── 이미지 (제품 사진·로고) ──────────────────────────────
    _IMG_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml"}

    def _serve_image(self, name: str) -> None:
        """static/img/ 안의 이미지를 내보낸다(제품 사진, 회사 로고).

        파일이 없으면 404 — 화면은 도식/글자 로고로 자동 대체된다.
        경로 탈출(..)은 파일명만 취해 차단한다.
        """
        # 한글 등 비ASCII 파일명은 URL 인코딩돼 오므로 먼저 푼다(안 풀면 있는 파일도 404).
        safe = os.path.basename(unquote(urlparse(name).path))  # 디렉터리 성분 제거
        ext = os.path.splitext(safe)[1].lower()
        if not safe or ext not in self._IMG_TYPES:
            return self._json({"error": "not found"}, 404)
        full = os.path.join(_STATIC_DIR, "img", safe)
        if not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        try:
            with open(full, "rb") as fh:
                blob = fh.read()
        except OSError:
            return self._json({"error": "읽기 실패"}, 500)
        self.send_response(200)
        self.send_header("Content-Type", self._IMG_TYPES[ext])
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(blob)

    # ── 로그인 ──────────────────────────────────────────────
    def _login(self) -> None:
        """{password} 확인 후 맞으면 세션 쿠키를 심는다."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if 0 < length <= 10000 else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            body = {}
        if not _DASH_PW:                       # 인증 비활성 상태면 그냥 통과
            return self._json({"ok": True, "auth": False})
        pw = str(body.get("password", ""))
        if not hmac.compare_digest(pw, _DASH_PW):
            return self._json({"error": "비밀번호가 올바르지 않습니다"}, 401)
        tok = _session_token()
        out = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        # 7일 유지. HttpOnly로 JS 접근 차단, SameSite=Lax로 CSRF 완화.
        self.send_header("Set-Cookie",
                         f"jcc_session={tok}; HttpOnly; Path=/; Max-Age=604800; SameSite=Lax")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _logout(self) -> None:
        out = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", "jcc_session=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _serve_login(self) -> None:
        self._text(_LOGIN_HTML, 200, "text/html; charset=utf-8")

    # ── 대시보드 ────────────────────────────────────────────
    def _serve_dashboard(self) -> None:
        index = os.path.join(_STATIC_DIR, "index.html")
        try:
            with open(index, "r", encoding="utf-8") as fh:
                html = fh.read()
        except OSError:
            return self._text("대시보드 파일(static/index.html)이 없습니다.", 500)
        self._text(html, 200, "text/html; charset=utf-8")


def make_server(host: str, port: int, storage: Storage) -> ThreadingHTTPServer:
    # 핸들러 클래스에 storage를 붙여 요청마다 공유하게 한다.
    handler = type("BoundHandler", (Handler,), {"storage": storage})
    return ThreadingHTTPServer((host, port), handler)
