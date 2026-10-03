"""HTTP 앱 — 라우팅과 엔드포인트.

표준 라이브러리 http.server 위에 얇은 라우터를 얹었다. ThreadingHTTPServer로
여러 CCM이 동시에 POST해도 처리한다.

엔드포인트
  POST /v1/telemetry             펌웨어가 보내는 수집 데이터 (http_transport와 짝)
  GET  /api/devices              CCM 목록 + 각 센서 최신값
  GET  /api/panels               판넬 단위로 묶은 목록 (판넬 1개 = CCM 여러 대)
  GET  /api/devices/{id}/history?sensor=KEY&limit=N   센서 이력
  GET  /health                   상태 확인
  GET  /                         고객 화면 JCC GUARD (static/guard.html) — 누구든 여기로 먼저
  GET  /ops                      운영자 화면 (static/index.html) — 운영자 계정으로만 데이터가 열린다
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

from .accounts import MIN_PW
from .ota_server import bundle_path, offer_for
from .storage import Storage

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

# 자동 탐색은 펌웨어(jcc_ccm.discovery)의 실제 코드를 재사용한다. 같은 저장소의
# firmware 패키지를 경로에 얹어, 실기 CCM이 돌릴 코드와 동일한 것을 시뮬레이션한다.
_FIRMWARE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "firmware")


_SCN_CACHE: list = []


def _scenarios_json() -> str:
    """튜닝 시나리오 전체(JSON, 수백 KB). 결정적이라 한 번 만들어 둔다."""
    if not _SCN_CACHE:
        from .scenarios import export_all
        _SCN_CACHE.append(json.dumps({"scenarios": export_all()}, ensure_ascii=False, separators=(",", ":")))
    return _SCN_CACHE[0]


def _profiles() -> list:
    """노드 추가(직접 지정)의 제품 목록 — 자동 탐색이 쓰는 프로파일 지식 베이스 그대로."""
    if _FIRMWARE_DIR not in sys.path:
        sys.path.insert(0, _FIRMWARE_DIR)
    from jcc_ccm.discovery.profiles import PROFILES
    keep = ("key", "name", "unit", "kind", "alarm_min", "alarm_warn", "alarm_max")
    return [{"ident": p["ident"], "brand": p.get("brand", ""), "product": p.get("product", ""),
             "part_no": p.get("part_no", ""), "manual": p.get("manual", ""), "photo": p.get("photo", ""),
             "emits": [{k: e.get(k) for k in keep} for e in p.get("emits", [])]}
            for p in PROFILES if not p["ident"].startswith("TURCK-CCM")]     # CCM 내장 센서는 직접 지정 대상 아님


def _run_discovery() -> dict:
    if _FIRMWARE_DIR not in sys.path:
        sys.path.insert(0, _FIRMWARE_DIR)
    from jcc_ccm.discovery import discover_sim
    return discover_sim()

# 선택적 인증: 환경변수 JCC_API_KEY가 설정되면 POST에 Bearer 토큰을 요구한다.
_API_KEY = os.environ.get("JCC_API_KEY", "")
# 운영 서버 잠금: JCC_REQUIRE_AUTH=1이면 비번·기기 키를 깜빡해도 '누구나 열림'이 되지 않는다.
#   대시보드 → 로그인 필수(비번도 계정도 없으면 아무도 못 들어옴 = 잠김),
#   텔레메트리·OTA → JCC_API_KEY가 없으면 거부(503, 설정하라는 안내)
_REQUIRE_AUTH = os.environ.get("JCC_REQUIRE_AUTH", "") == "1"

# 대시보드 접근 계정(공용 1개). 환경변수 JCC_DASHBOARD_PW가 설정됐을 때만 인증이 켜진다.
# 비워두면(개발·지인 데모) 인증 없이 바로 열린다.
#   운영 서버:  JCC_DASHBOARD_PW=원하는비번  (JCC_DASHBOARD_USER=아이디, 기본 jcc)
#   — 코드·저장소에 넣지 않는다.
# 로그인 화면은 대시보드의 터미널 화면 하나뿐이다. 서버는 화면 껍데기(HTML)는 누구에게나
# 주되, 데이터 API는 로그인 전엔 전부 401로 막는다. 세션은 비밀번호로 서명한 토큰을
# 쿠키에 담아 유지한다(별도 시크릿 불필요 — 비번을 바꾸면 기존 세션은 모두 무효).
_DASH_PW = os.environ.get("JCC_DASHBOARD_PW", "")
_DASH_USER = os.environ.get("JCC_DASHBOARD_USER", "jcc")

# 무차별 대입 방지: 같은 IP에서 LOGIN_WINDOW초 안에 LOGIN_MAX_FAILS번 틀리면 잠근다.
LOGIN_MAX_FAILS = 5
LOGIN_WINDOW = 300.0
_login_fails: dict = {}          # ip -> [실패 시각...]
_login_lock = threading.Lock()


def _session_token() -> str:
    """비밀번호를 키로 서명한 세션 토큰. 비번을 모르면 위조 불가."""
    return hmac.new(_DASH_PW.encode("utf-8"), f"jcc-auth-v2:{_DASH_USER}".encode("utf-8"),
                    hashlib.sha256).hexdigest()


# ── 권한 표(단일 정책) ────────────────────────────────────────
# 모든 경로를 여기서 분류한다. **표에 없는 경로는 거부(403)** — 새 API를 만들고 분류를 잊으면
# 고객에게 새는 대신 막힌다(test_scope가 코드의 모든 경로가 표에 있는지 검사).
#   public : 로그인 불필요(화면 껍데기·상태·로그인)       device : 기기 키로 따로 인증(텔레메트리·OTA)
#   self   : 로그인만(비번 변경 전이어도)                read   : 모든 등급, 응답을 계정 범위로 거름
#   operate: JCC 관리자·고객 담당자(대상이 범위 안일 때)  admin  : JCC 관리자만
ROUTES = [
    ("GET", "/", "public"), ("GET", "/index.html", "public"), ("GET", "/guard", "public"), ("GET", "/ops", "public"), ("GET", "/predict-core.js", "public"),
    ("GET", "/manifest.webmanifest", "public"), ("GET", "/sw.js", "public"),
    ("GET", "/health", "public"), ("GET", "/api/auth/status", "public"),
    ("POST", "/api/login", "public"), ("POST", "/api/logout", "public"), ("POST", "/api/signup", "public"), ("GET", r"/img/.+", "public"), ("GET", r"/fonts/.+", "public"),
    ("GET", r"/ota/.+", "device"), ("POST", "/v1/telemetry", "device"),
    ("POST", "/api/me/password", "self"),
    ("GET", "/api/devices", "read"), ("GET", "/api/panels", "read"), ("GET", "/api/alarms", "read"),
    ("GET", "/api/incidents", "read"), ("GET", "/api/report", "read"), ("GET", "/api/predict", "read"),
    ("GET", "/api/events", "read"), ("GET", r"/api/devices/[^/]+/history", "read"),
    ("POST", "/api/alarm/ack", "operate"), ("POST", "/api/predict/actuator", "operate"),
    ("GET", "/api/notify/status", "admin"), ("GET", "/api/healthcheck", "admin"), ("GET", "/api/heal/config", "admin"),
    ("POST", "/api/heal/config", "admin"), ("GET", "/api/tuning/params", "admin"),
    ("GET", "/api/tuning/scenarios", "admin"), ("GET", "/api/tuning/config", "admin"),
    ("POST", "/api/tuning/evaluate", "admin"), ("POST", "/api/tuning/apply", "admin"),
    ("POST", "/api/tuning/rollback", "admin"), ("POST", "/api/discover", "admin"),
    ("POST", "/api/discover/report", "admin"), ("POST", "/api/channel", "admin"),
    ("POST", "/api/channels/enable-all", "admin"), ("POST", "/api/setting", "admin"),
    ("POST", "/api/device/command", "admin"), ("POST", "/api/diagnose", "admin"),
    ("POST", "/api/notify/test", "admin"), ("POST", "/api/panel/name", "admin"),
    ("POST", "/api/predict/baseline", "admin"),
    ("GET", "/api/admin/accounts", "admin"), ("POST", r"/api/admin/[a-z_/]+", "admin"),
    ("GET", "/api/admin/backups", "admin"), ("GET", r"/api/admin/backup/[A-Za-z0-9_.-]+", "admin"),
    ("GET", "/api/commission/check", "admin"), ("POST", "/api/commission/output_test", "admin"),
    ("POST", "/api/commission/complete", "admin"), ("GET", "/api/commission/reports", "read"),
    ("GET", "/api/monthly", "read"), ("POST", "/api/monthly/issue", "admin"),
    ("GET", "/api/ai/status", "read"), ("POST", "/api/ai/ask", "read"), ("GET", "/api/rul", "read"),
    ("GET", "/api/sensor/manual", "admin"), ("POST", "/api/sensor/manual", "admin"),
    ("GET", "/api/sensor/profiles", "admin"), ("GET", "/api/fleet", "read"), ("GET", "/api/alarms/history", "read"),
    ("GET", "/api/handover", "read"), ("POST", "/api/handover/seen", "read"),
    ("GET", "/api/guard", "read"), ("GET", "/api/guard/month", "read"), ("GET", "/api/guard/panel", "read"),
    ("GET", "/api/guard/alarm", "read"), ("GET", "/api/guard/incidents", "read"), ("GET", "/api/guard/incident", "read"),
    ("GET", "/api/inspection", "admin"), ("GET", "/api/inspections", "read"),
    ("GET", r"/api/inspection/\d+", "read"), ("GET", r"/api/inspection/photo/\d+", "read"),
    ("POST", r"/api/inspection/[a-z_]+", "admin"),
    ("GET", "/api/export/alarms.csv", "read"), ("GET", "/api/export/readings.csv", "read"),
]
_ROUTE_RE = [(m, re.compile((p if any(c in p for c in "[+") else re.escape(p)) + r"\Z"), pol)   # 일반 경로는 글자 그대로
             for m, p, pol in ROUTES]


def route_policy(method: str, path: str):
    """이 요청의 정책. 표에 없으면 None(= 거부)."""
    for m, rx, pol in _ROUTE_RE:
        if m == method and rx.match(path):
            return pol
    return None


_ROLE_OK = {"read": ("admin", "manager", "viewer"), "operate": ("admin", "manager"), "admin": ("admin",)}


# 같은 아이디로는 IP가 달라도 LOGIN_USER_MAX번 틀리면 잠근다(프록시 헤더를 속여 IP 잠금을 피해도 막히게)
LOGIN_USER_MAX = 10


def _trust_proxy() -> bool:
    """Render 등 프록시 뒤인가. 그렇다면 소켓 주소는 프록시라 모든 손님이 한 IP로 보인다."""
    return bool(os.environ.get("RENDER")) or os.environ.get("JCC_TRUST_PROXY", "").strip() in ("1", "on", "true")


def client_ip(handler) -> str:
    """잠금·남용 방지용 손님 IP. 프록시 뒤면 X-Forwarded-For 맨 앞(손님) — 아니면 소켓 주소.
    (그 값은 손님이 속일 수 있으므로 아이디별 잠금을 함께 건다.)"""
    if _trust_proxy():
        first = (handler.headers.get("X-Forwarded-For", "") or "").split(",")[0].strip()
        if first and len(first) <= 64:
            return first
    return handler.client_address[0]


def _login_locked(ip: str, now: float, limit: int = LOGIN_MAX_FAILS) -> float:
    """잠겨 있으면 남은 초, 아니면 0. (ip 자리에 'u:아이디'를 넣으면 아이디별 잠금)"""
    with _login_lock:
        fails = [t for t in _login_fails.get(ip, []) if now - t < LOGIN_WINDOW]
        _login_fails[ip] = fails
        if len(fails) >= limit:
            return LOGIN_WINDOW - (now - fails[0])
    return 0.0


# 회원가입 남용 방지: 같은 IP에서 SIGNUP_WINDOW초 안에 '실패(틀린 코드 등)·가입 신청'이 SIGNUP_MAX번이면 막는다.
# 코드로 성공한 가입은 세지 않는다 — 공장 직원들은 대개 한 인터넷 주소를 같이 쓰므로 여럿이 연달아 가입해도 막히지 않게.
SIGNUP_MAX = 6
SIGNUP_WINDOW = 3600.0
_signup_hits: dict = {}


def _signup_allowed(ip: str, now: float) -> bool:
    with _login_lock:
        hits = [t for t in _signup_hits.get(ip, []) if now - t < SIGNUP_WINDOW]
        _signup_hits[ip] = hits
        return len(hits) < SIGNUP_MAX


def _signup_count(ip: str, now: float) -> None:
    with _login_lock:
        _signup_hits.setdefault(ip, []).append(now)


def _signup_on() -> bool:
    """회원가입 받기(기본 켬). 끄려면 JCC_SIGNUP=off."""
    return os.environ.get("JCC_SIGNUP", "on").strip().lower() not in ("off", "0", "false", "no")


def _login_record(ip: str, ok: bool, now: float) -> None:
    with _login_lock:
        if ok:
            _login_fails.pop(ip, None)
        else:
            _login_fails.setdefault(ip, []).append(now)


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

    _ENV_ADMIN = None   # 환경변수 비상 admin(요청마다 새로 만들지 않게)

    def _auth_on(self) -> bool:
        """인증이 켜졌나: 환경변수 비번이 있거나 DB 계정이 하나라도 있으면. 둘 다 없으면 개발 모드(전부 허용)."""
        return bool(_DASH_PW) or _REQUIRE_AUTH or self.storage.accounts.has_users()

    def _user(self):
        """이 요청의 사용자(없으면 None). {id, username, role, customer_id, customer, must_change}."""
        if hasattr(self, "_u_cache"):
            return self._u_cache
        u = None
        if not self._auth_on():
            u = {"id": 0, "username": "local", "role": "admin", "customer_id": None, "customer": None,
                 "must_change": False, "env": True}
        else:
            tok = self._cookies().get("jcc_session", "")
            if _DASH_PW and hmac.compare_digest(tok, _session_token()):
                u = {"id": 0, "username": _DASH_USER, "role": "admin", "customer_id": None, "customer": None,
                     "must_change": False, "env": True}
            elif tok:
                u = self.storage.accounts.session_user(tok)
        self._u_cache = u
        return u

    def _authed(self) -> bool:
        return self._user() is not None

    def _device_denied(self):
        """기기(CCM) 인증. 통과면 None, 막으면 (응답, 상태). 키 비교는 상수시간."""
        if not _API_KEY:
            if _REQUIRE_AUTH:
                return {"error": "서버에 기기 키(JCC_API_KEY)가 설정되지 않아 받지 않습니다"}, 503
            return None
        got = self.headers.get("Authorization", "")
        if not hmac.compare_digest(got.encode("utf-8"), f"Bearer {_API_KEY}".encode("utf-8")):
            return {"error": "unauthorized"}, 401
        return None

    def _gate(self, method: str, path: str) -> bool:
        """권한 표로 요청을 거른다. 통과면 True, 막았으면 응답을 이미 보냈으므로 False."""
        self.__dict__.pop("_u_cache", None)          # 연결 재사용 시 이전 요청의 사용자·범위를 쓰지 않게
        self.__dict__.pop("_scope_cache", None)
        pol = route_policy(method, path)
        if pol is None:
            self._json({"error": "허용되지 않은 경로입니다"}, 403)
            return False
        if pol in ("public", "device"):
            return True
        u = self._user()
        if u is None:
            self._json({"error": "unauthorized"}, 401)
            return False
        if pol == "self":
            return True
        if u.get("must_change"):
            self._json({"error": "임시 비번입니다 — 먼저 비번을 바꾸세요", "must_change": True}, 403)
            return False
        if u["role"] not in _ROLE_OK[pol]:
            self._json({"error": "이 계정 등급으로는 할 수 없는 작업입니다"}, 403)
            return False
        return True

    def _scope(self):
        """고객 계정이 볼 수 있는 것: {"devices": set, "panels": set}. JCC 관리자는 None(전부)."""
        if hasattr(self, "_scope_cache"):
            return self._scope_cache
        u = self._user()
        sc = None
        if u is not None and u["role"] != "admin":
            owner = self.storage.accounts.panel_owner_map()
            panels = {p for p, c in owner.items() if c == u["customer_id"]}
            devs = {d["device_id"] for d in self.storage.list_devices() if (d.get("panel") or d["device_id"]) in panels}
            sc = {"devices": devs, "panels": panels}
        self._scope_cache = sc
        return sc

    def _ai_scope(self) -> dict:
        """AI에 보내도 되는 판넬. 데이터가 외부 AI로 나가므로 고객사 스위치(ai_enabled)가 켜진 것만.
        JCC 관리자: 켜진 고객사 판넬 + 미배정(JCC 자체) 판넬. 고객: 자기 고객사가 켜져 있을 때 자기 판넬."""
        u, ac = self._user(), self.storage.accounts
        on, owner = ac.ai_customers(), ac.panel_owner_map()
        every = {p["panel"] for p in self.storage.list_panels()}
        if u["role"] == "admin":
            allowed = {p for p in every if owner.get(p) is None or owner[p] in on}
            return {"panels": allowed, "customer_id": 0, "blocked": "", "excluded": len(every - allowed)}
        if u.get("customer_id") not in on:
            return {"panels": set(), "customer_id": u.get("customer_id") or -1, "excluded": 0,
                    "blocked": "이 고객사는 AI 기능이 꺼져 있습니다(JCC에 문의하세요)"}
        return {"panels": set(self._scope()["panels"]) & every, "customer_id": u["customer_id"],
                "blocked": "", "excluded": 0}

    def _guard(self, path: str, q: dict, sc) -> None:
        """고객 화면 데이터. 고객은 자기 고객사 판넬만, JCC 직원은 customer_id로 미리보기(없으면 전체)."""
        from . import guard
        from .monthly import valid_period
        rows = {c["id"]: c for c in self.storage.accounts.list_customers()}
        custs = {k: v["name"] for k, v in rows.items()}
        cust = None
        if sc is not None:
            panels, site = set(sc["panels"]), custs.get(self._user().get("customer_id"), "우리 공장")
            cust = rows.get(self._user().get("customer_id"))
        else:
            try:
                cid = int((q.get("customer_id") or [""])[0])
            except ValueError:
                cid = None
            if cid in custs:
                owner = self.storage.accounts.panel_owner_map()
                panels, site, cust = {p for p, c in owner.items() if c == cid}, custs[cid], rows[cid]
            else:
                panels, site = None, "전체 현장 · JCC 미리보기"
        if path == "/api/guard":
            return self._json(guard.guard_view(self.storage, panels, site, customer=cust))
        if path == "/api/guard/month":
            period = (q.get("period") or [""])[0]
            if not valid_period(period):
                return self._json({"error": "달은 YYYY-MM 형식입니다"}, 400)
            return self._json(guard.month_view(self.storage, panels, period))
        if path == "/api/guard/alarm":           # 알림 링크로 들어온 경보 한 건(고객은 자기 범위만)
            try:
                aid = int((q.get("id") or [""])[0])
            except ValueError:
                return self._json({"error": "경보 번호를 확인하세요"}, 400)
            a = guard.alarm_view(self.storage, aid)
            if a is None or (panels is not None and a["panel"] not in panels):
                return self._json({"error": "볼 수 없는 경보입니다"}, 404)
            return self._json(a)
        if path == "/api/guard/incidents":       # 그 달 경보 목록(사건 보고서 고르기)
            period = (q.get("period") or [""])[0]
            if not valid_period(period):
                return self._json({"error": "달은 YYYY-MM 형식입니다"}, 400)
            return self._json(guard.incidents(self.storage, panels, period))
        if path == "/api/guard/incident":        # 사건 보고서 한 장(고객은 자기 범위만)
            try:
                aid = int((q.get("id") or [""])[0])
            except ValueError:
                return self._json({"error": "경보 번호를 확인하세요"}, 400)
            r = guard.incident_report(self.storage, aid)
            if r is None or (panels is not None and r["panel"] not in panels):
                return self._json({"error": "볼 수 없는 경보입니다"}, 404)
            return self._json(r)
        pid = (q.get("panel") or [""])[0]
        d = guard.panel_detail(self.storage, pid) if panels is None or pid in panels else None
        return self._json(d) if d else self._json({"error": "볼 수 없는 판넬입니다"}, 404)

    def _inspection(self, action: str) -> None:
        """정기 점검 쓰기(JCC 관리자): start·save·photo·photo_delete·complete."""
        from . import inspection as insp
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            b = json.loads(self.rfile.read(length).decode("utf-8")) if 0 < length <= self.MAX_BODY else {}
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        if not isinstance(b, dict):
            return self._json({"error": "잘못된 요청"}, 400)
        me = self._user()["username"]
        iid = b.get("id") if isinstance(b.get("id"), int) and not isinstance(b.get("id"), bool) else None
        try:
            if action == "start":
                return self._json(insp.start(self.storage, str(b.get("panel", "")), me))
            if iid is None:
                return self._json({"error": "점검 id가 필요합니다"}, 400)
            if action == "save":
                return self._json(insp.save(self.storage, iid, b))
            if action == "photo":
                return self._json(insp.add_photo(self.storage, iid, b.get("data"), b.get("caption", ""), me))
            if action == "photo_delete":
                ok = insp.del_photo(self.storage, iid, int(b.get("photo_id") or 0))
                return self._json({"ok": True}) if ok else self._json({"error": "지울 수 없는 사진입니다"}, 400)
            if action == "complete":
                return self._json(insp.complete(self.storage, iid, b.get("signer", ""), b.get("signature", ""), me))
        except (ValueError, TypeError) as exc:          # TypeError: photo_id 같은 값이 엉뚱한 모양일 때
            return self._json({"error": str(exc) if isinstance(exc, ValueError) else "잘못된 요청 형식입니다"}, 400)
        return self._json({"error": "없는 작업입니다"}, 404)

    def _manual_sensor(self) -> None:
        """{action:"add", device_id, spec, meta?} | {action:"remove", device_id, key} — JCC 관리자만."""
        from . import manual_sensors
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            b = json.loads(self.rfile.read(length).decode("utf-8")) if 0 < length <= 20_000 else {}
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        if not isinstance(b, dict):
            return self._json({"error": "잘못된 요청"}, 400)
        dev, me = str(b.get("device_id", "")), self._user()["username"]
        try:
            if b.get("action") == "add":
                meta = b.get("meta") if isinstance(b.get("meta"), dict) else {}
                return self._json({"ok": True, "sensor": manual_sensors.add(self.storage, dev, b.get("spec"), meta, me)})
            if b.get("action") == "remove":
                ok = manual_sensors.remove(self.storage, dev, str(b.get("key", "")), me)
                return self._json({"ok": True}) if ok else self._json({"error": "없는 수동 센서입니다"}, 404)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        return self._json({"error": "action은 add 또는 remove"}, 400)

    def _ai_status(self) -> None:
        from .ai import MODEL
        a, sc = self.storage.ai, self._ai_scope()
        return self._json({"available": a.available, "reason": a.reason, "enabled": not sc["blocked"],
                           "blocked": sc["blocked"], "panels": len(sc["panels"]), "excluded_panels": sc["excluded"],
                           "model": MODEL, "usage": a.usage(sc["customer_id"])})

    def _ai_ask(self) -> None:
        from .ai import AIError
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            b = json.loads(self.rfile.read(length).decode("utf-8")) if 0 < length <= 60_000 else {}
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        if not isinstance(b, dict):
            return self._json({"error": "잘못된 요청"}, 400)
        sc = self._ai_scope()
        if sc["blocked"]:
            return self._json({"error": sc["blocked"]}, 403)
        hist = b.get("history") if isinstance(b.get("history"), list) else []
        q = str(b.get("question", ""))
        try:
            res = self.storage.ai.ask(q, sc["panels"], sc["customer_id"], hist)
        except AIError as exc:
            return self._json({"error": str(exc)}, exc.status)
        except Exception as exc:  # noqa: BLE001 - 예상 밖 오류도 화면엔 이유를(연결이 그냥 끊기지 않게)
            return self._json({"error": f"AI 처리 중 오류({type(exc).__name__}) — 잠시 뒤 다시 시도해 주세요"}, 502)
        self.storage.log_event("", "", "ai_ask", f"AI 질문: {q.strip()[:80]} ({self._user()['username']})",
                               source="user")
        return self._json(res)

    def _in_scope(self, key: str) -> bool:
        sc = self._scope()
        return sc is None or key in sc["devices"] or key in sc["panels"]

    # ── GET ────────────────────────────────────────────────
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        # 권한 표(ROUTES)로 거른다. 화면 껍데기(/ — 터미널 로그인이 그 안에 있다)·이미지·상태는 공개,
        # 데이터는 로그인 + 등급, 고객 계정은 아래에서 자기 고객사 판넬만 돌려준다.
        if not self._gate("GET", path):
            return
        sc = self._scope()

        if path in ("/", "/guard"):             # 첫 화면은 무조건 고객 화면(JCC GUARD) — 로그인이 그 안에 있다
            return self._serve_dashboard("guard.html")
        if path in ("/ops", "/index.html"):     # 운영자 화면 — 껍데기는 공개, 데이터·조작은 운영자 로그인 뒤(고객 계정은 / 로 돌려보냄)
            return self._serve_dashboard()
        if path == "/predict-core.js":          # 판정 코어(JS) — 화면 코드일 뿐 현장 데이터는 없다
            return self._serve_static_js("predict-core.js")
        if path == "/sw.js":                    # 고객 화면 홈 화면 앱(껍데기만 보관 — 데이터는 저장 안 함)
            return self._serve_static_js("sw.js")
        if path == "/manifest.webmanifest":
            try:
                with open(os.path.join(_STATIC_DIR, "manifest.webmanifest"), "r", encoding="utf-8") as fh:
                    return self._text(fh.read(), 200, "application/manifest+json; charset=utf-8")
            except OSError:
                return self._json({"error": "not found"}, 404)
        if path == "/health":
            return self._json({"ok": True, "ts": time.time()})
        if path == "/api/devices":
            return self._json({"devices": [d for d in self.storage.list_devices()
                                           if sc is None or d["device_id"] in sc["devices"]]})
        if path == "/api/panels":
            return self._json({"panels": [p for p in self.storage.list_panels()
                                          if sc is None or p["panel"] in sc["panels"]]})
        if path in ("/api/alarms", "/api/incidents"):
            al = [a for a in self.storage.list_active_alarms()
                  if sc is None or a.get("device_id") in sc["devices"] or a.get("device_id") in sc["panels"]]
            if path == "/api/alarms":
                return self._json({"alarms": al})
            return self._json({"incidents": self.storage.list_incidents(al), "alarms": al})
        if path == "/api/healthcheck":
            rep = self.storage.diagnose_all()
            c = rep.get("counts", {})
            self.storage.log_event("", "", "healthcheck",
                                   f"전체 점검: {rep.get('summary','')} "
                                   f"(정상 {c.get('pass',0)}·주의 {c.get('warn',0)}·이상 {c.get('fail',0)})")
            return self._json(rep)
        if path == "/api/report":
            q = parse_qs(parsed.query)
            try:
                days = float((q.get("days") or ["7"])[0])
            except ValueError:
                days = 7
            return self._json(self.storage.build_report(days, devices=None if sc is None else sc["devices"]))
        if path == "/api/heal/config":
            return self._json(self._heal_config())
        if path == "/api/predict":
            return self._json({"panels": [p for p in self.storage.predict_state()
                                          if sc is None or p.get("panel") in sc["panels"]]})
        if path == "/api/auth/status":
            u = self._user()
            pub = None if u is None else dict({k: u[k] for k in ("username", "role", "customer", "must_change")},
                                               env=bool(u.get("env")))
            return self._json({"enabled": self._auth_on(), "authed": u is not None, "user": pub,
                               "role": u["role"] if u else None, "customer": u["customer"] if u else None,
                               # 로그인 화면의 '아이디·비밀번호 찾기' 안내용 공통 문의 번호(공개 정보)
                               "support_phone": os.environ.get("JCC_SUPPORT_PHONE", "").strip(),
                               "signup": self._auth_on() and _signup_on(), "min_pw": MIN_PW})
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
            keys = None if sc is None else (sc["devices"] | sc["panels"])
            return self._json({"events": self.storage.list_events(dev, key, limit, devices=keys)})

        if path == "/api/commission/check":
            from .commission import check_panel
            rep = check_panel(self.storage, (parse_qs(parsed.query).get("panel") or [""])[0])
            return self._json(rep) if rep else self._json({"error": "없는 판넬입니다"}, 404)
        if path == "/api/monthly":
            u = self._user()
            if u["role"] == "admin":
                q = (parse_qs(parsed.query).get("customer_id") or [""])[0]
                cid = int(q) if q.isdigit() else None
            else:
                cid = u["customer_id"]                  # 고객은 무엇을 물어도 자기 고객사만
            names = {c["id"]: c["name"] for c in self.storage.accounts.list_customers()}
            reps = self.storage.list_monthly(cid)
            for r in reps:
                r["customer"] = names.get(r["customer_id"], "")
            return self._json({"reports": reps})
        if path == "/api/inspection":                 # 점검 화면 준비(관리자): 볼 곳·초안·이력
            from . import inspection as insp
            panel = (parse_qs(parsed.query).get("panel") or [""])[0]
            if not any(p["panel"] == panel for p in self.storage.list_panels()):
                return self._json({"error": "없는 판넬입니다"}, 404)
            return self._json({"focus": insp.focus(self.storage, panel), "draft": insp.draft_for(self.storage, panel),
                               "history": insp.history(self.storage, {panel})})
        if path == "/api/inspections":
            from . import inspection as insp
            return self._json({"inspections": insp.history(self.storage, None if sc is None else sc["panels"],
                                                          include_drafts=sc is None)})
        mi = re.fullmatch(r"/api/inspection/(\d+)", path)
        if mi:
            from . import inspection as insp
            d = insp.view(self.storage, int(mi.group(1)))
            if d is None or (sc is not None and (d["status"] != "done" or d["panel"] not in sc["panels"])):
                return self._json({"error": "볼 수 없는 점검입니다"}, 404)
            return self._json(d)
        mp = re.fullmatch(r"/api/inspection/photo/(\d+)", path)
        if mp:
            from . import inspection as insp
            ph = insp.photo_bytes(self.storage, int(mp.group(1)))
            if ph is None or (sc is not None and (ph[3] != "done" or ph[2] not in sc["panels"])):
                return self._json({"error": "볼 수 없는 사진입니다"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", ph[0])
            self.send_header("Content-Length", str(len(ph[1])))
            self.send_header("Cache-Control", "private, max-age=86400")
            self.end_headers()
            self.wfile.write(ph[1])
            return
        if path in ("/api/export/alarms.csv", "/api/export/readings.csv"):
            from . import export
            q = parse_qs(parsed.query)
            try:
                days = min(366.0, max(1.0, float((q.get("days") or ["30"])[0])))
            except ValueError:
                days = 30.0
            stamp = time.strftime("%Y%m%d")
            if path.endswith("alarms.csv"):
                body = export.alarms_csv(self.storage, days, None if sc is None else (sc["devices"] | sc["panels"]))
                fname = f"jcc-alarms-{stamp}.csv"
            else:
                dev, key = (q.get("device_id") or [""])[0], (q.get("sensor") or [""])[0]
                if not dev or not key:
                    return self._json({"error": "device_id와 sensor가 필요합니다"}, 400)
                if not self._in_scope(dev):
                    return self._json({"error": "이 계정 범위 밖의 기기입니다"}, 403)
                body = export.readings_csv(self.storage, dev, key, days)
                fname = f"jcc-{re.sub(r'[^A-Za-z0-9_.-]', '_', dev)}-{re.sub(r'[^A-Za-z0-9_.-]', '_', key)}-{stamp}.csv"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/api/guard", "/api/guard/month", "/api/guard/panel", "/api/guard/alarm",
                    "/api/guard/incidents", "/api/guard/incident"):   # 고객 화면 데이터
            return self._guard(path, parse_qs(parsed.query), sc)
        if path == "/api/handover":                   # 근무 인계 요약(내가 마지막으로 확인한 뒤)
            from .handover import summary
            return self._json(summary(self.storage, self._user()["username"],
                                      None if sc is None else (sc["devices"] | sc["panels"]),
                                      None if sc is None else sc["panels"]))
        if path == "/api/alarms/history":             # 지난 경보 + 오경보 잦은 센서
            from .alarm_review import history
            q = parse_qs(parsed.query)
            try:
                days = float((q.get("days") or ["30"])[0])
            except ValueError:
                days = 30.0
            return self._json(history(self.storage, None if sc is None else (sc["devices"] | sc["panels"]), days,
                                      (q.get("cause") or [""])[0]))
        if path == "/api/fleet":
            from .fleet import fleet
            return self._json({"panels": fleet(self.storage, None if sc is None else sc["panels"])})
        if path == "/api/sensor/manual":
            from .manual_sensors import status_list
            return self._json({"sensors": status_list(self.storage, (parse_qs(parsed.query).get("device_id") or [""])[0])})
        if path == "/api/sensor/profiles":
            return self._json({"profiles": _profiles()})
        if path == "/api/rul":
            from .rul import panel_view
            return self._json({"panels": panel_view(self.storage, None if sc is None else sc["panels"])})
        if path == "/api/ai/status":
            return self._ai_status()
        if path == "/api/commission/reports":
            return self._json({"reports": self.storage.list_commission_reports(None if sc is None else sc["panels"])})
        if path == "/api/admin/backups":              # 데이터 백업 목록(운영자)
            from . import backup
            return self._json({"backups": backup.list_backups(self.storage), "keep_days": backup._keep(),
                               "dir": backup.backup_dir(self.storage)})
        mb = re.fullmatch(r"/api/admin/backup/([A-Za-z0-9_.-]+)", path)
        if mb:                                        # 사본 내려받기 — 이름 형식·존재 확인(경로 탈출 차단)
            from . import backup
            fp = backup.path_for(self.storage, mb.group(1))
            if not fp:
                return self._json({"error": "없는 백업입니다"}, 404)
            with open(fp, "rb") as fh:
                blob = fh.read()
            self.storage.log_event("", "", "account", f"데이터 백업 내려받음: {mb.group(1)} ({self._user()['username']})", source="user")
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{mb.group(1)}"')
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
            return
        if path == "/api/admin/accounts":
            ac = self.storage.accounts
            owner = ac.panel_owner_map()
            custs = [dict(c, panels=sorted(p for p, o in owner.items() if o == c["id"]),
                          receivers=ac.receivers_for(c["id"])) for c in ac.list_customers()]
            panels = [{"panel": p["panel"], "panel_name": p["panel_name"], "customer_id": owner.get(p["panel"]),
                       "online": p["online"]} for p in self.storage.list_panels()]
            return self._json({"customers": custs, "users": ac.list_users(), "panels": panels,
                               "env_admin": bool(_DASH_PW) and _DASH_USER,
                               "signup_requests": ac.list_signup_requests(), "signup_on": _signup_on()})
        if path == "/api/tuning/params":
            from .params import registry_view
            return self._json(registry_view())
        if path == "/api/tuning/scenarios":
            return self._text(_scenarios_json(), 200, "application/json; charset=utf-8")
        if path == "/api/tuning/config":
            from .params import defaults
            return self._json({"active": self.storage.tuning_active(), "history": self.storage.tuning_history(),
                               "edge": self.storage.tuning_edge_status(), "defaults": defaults()})
        if path.startswith("/ota/"):
            return self._serve_ota(path[len("/ota/"):])
        if path.startswith("/img/"):
            return self._serve_image(path[len("/img/"):])
        if path.startswith("/fonts/"):
            return self._serve_font(path[len("/fonts/"):])

        m = re.fullmatch(r"/api/devices/([^/]+)/history", path)
        if m:
            device_id = m.group(1)
            if not self._in_scope(device_id):
                return self._json({"error": "이 계정 범위 밖의 기기입니다"}, 403)
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
    MAX_BODY = 5_000_000      # 가장 큰 요청(탐색 보고)과 같은 한도

    def do_POST(self) -> None:
        # 본문을 먼저 끝까지 읽어 둔다. 본문을 남긴 채 거절(401·403)하고 연결을 닫으면 윈도우 등에서
        # 연결이 강제로 끊겨(RST) 클라이언트가 거절 응답 대신 '연결 끊김'을 본다(test_scope에서 간헐적으로 발견).
        # 각 처리기는 지금처럼 self.rfile에서 읽으면 된다 — 미리 읽은 내용을 그대로 돌려준다.
        length = int(self.headers.get("Content-Length", 0) or 0)
        if 0 < length <= self.MAX_BODY:
            self.rfile = io.BytesIO(self.rfile.read(length))
        parsed = urlparse(self.path)
        # 로그인/로그아웃은 인증 이전에 처리한다.
        if parsed.path == "/api/login":
            return self._login()
        if parsed.path == "/api/logout":
            return self._logout()
        if parsed.path == "/api/signup":
            return self._signup()
        # 그 밖은 권한 표로(장비 텔레메트리는 Bearer 키로 따로 인증)
        if not self._gate("POST", parsed.path):
            return
        if parsed.path == "/api/me/password":
            return self._change_password()
        if parsed.path.startswith("/api/admin/"):
            return self._admin(parsed.path[len("/api/admin/"):])
        if parsed.path == "/api/monthly/issue":
            from .monthly import issue, valid_period
            length = int(self.headers.get("Content-Length", 0) or 0)
            try:
                b = json.loads(self.rfile.read(length).decode("utf-8")) if 0 < length <= 10_000 else {}
            except (ValueError, UnicodeDecodeError):
                return self._json({"error": "잘못된 JSON"}, 400)
            cid, period = b.get("customer_id"), str(b.get("period", ""))
            if not isinstance(cid, int) or isinstance(cid, bool) or not valid_period(period):
                return self._json({"error": "고객사와 월(YYYY-MM)을 확인하세요"}, 400)
            rep = issue(self.storage, cid, period, self._user()["username"])
            return self._json({"ok": True, "report": rep}) if rep else self._json({"error": "없는 고객사입니다"}, 400)
        if parsed.path == "/api/ai/ask":
            return self._ai_ask()
        if parsed.path == "/api/handover/seen":       # 인계 확인 — 다음 요약은 지금부터
            from .handover import mark_seen
            return self._json({"ok": True, "ts": mark_seen(self.storage, self._user()["username"])})
        if parsed.path == "/api/sensor/manual":
            return self._manual_sensor()
        if parsed.path.startswith("/api/inspection/"):
            return self._inspection(parsed.path[len("/api/inspection/"):])
        if parsed.path.startswith("/api/commission/"):
            return self._commission(parsed.path[len("/api/commission/"):])
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
        if parsed.path == "/api/heal/config":
            return self._set_heal_config()
        if parsed.path == "/api/predict/actuator":
            return self._set_actuator()
        if parsed.path in ("/api/tuning/evaluate", "/api/tuning/apply", "/api/tuning/rollback"):
            return self._tuning(parsed.path.rsplit("/", 1)[1])
        if parsed.path == "/api/predict/baseline":
            length = int(self.headers.get("Content-Length", 0) or 0)
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            except (ValueError, UnicodeDecodeError):
                return self._json({"error": "잘못된 JSON"}, 400)
            if body.get("action") != "relearn" or not body.get("panel"):
                return self._json({"error": "{panel, action:'relearn'}이 필요합니다"}, 400)
            return self._json(self.storage.relearn_baseline(str(body["panel"])))
        if parsed.path != "/v1/telemetry":
            return self._json({"error": "not found"}, 404)

        denied = self._device_denied()
        if denied:
            return self._json(*denied)

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
        dev = str(payload.get("device_id"))
        resp = {"ok": True, "stored": n}
        try:
            # 엣지 보고(CCM이 직접 판정·구동한 결과) 반영 + 대기 중인 수동 조작 명령을 응답으로 하향
            self.storage.ingest_edge(dev, payload.get("edge"))
            cmds = self.storage.take_edge_commands(dev)
            if cmds:
                resp["commands"] = cmds
            # 원격 업데이트: 펌웨어 버전·시험 부팅·롤백 기록, 배포 대상이면 새 버전 제안
            fw = payload.get("fw")
            self.storage.note_firmware(dev, fw, payload.get("ota"))
            offer = offer_for(dev, fw)
            if offer:
                resp["ota"] = offer
                self.storage.note_ota_offer(dev, offer["version"])
            # 예지 기준 설정: CCM의 적용 상태 기록, 옛 버전이면 활성 설정을 내려보냄(CCM이 한계 검증 후 적용)
            self.storage.note_tuning(dev, payload.get("tuning"))
            tun = self.storage.tuning_offer_for(dev, payload.get("tuning"))
            if tun:
                resp["tuning"] = tun
            # 수동 센서(노드 추가에서 직접 지정): CCM 적용 결과 기록, 옛 버전이면 목록을 내려보냄
            from . import manual_sensors
            manual_sensors.note_report(self.storage, dev, payload.get("sensor_config"))
            mo = manual_sensors.offer_for(self.storage, dev, payload.get("sensor_config"))
            if mo:
                resp["sensor_config"] = mo
        except Exception:  # noqa: BLE001 - 엣지 연동 오류가 수집 응답을 깨면 안 된다
            pass
        return self._json(resp)

    # ── 튜닝 콘솔: 평가·적용·되돌리기 (관문은 서버가 쥔다) ──────
    TUNING_TIMEOUT = 10.0

    def _tuning(self, action: str) -> None:
        """evaluate: 저장 없이 파이썬 정본 성적표 / apply: 새 설정 / rollback: 예전 버전(또는 기본값).
        apply·rollback은 같은 관문 — 형식·절대 한계·관계 검사 → 전 시나리오 재검증 → 놓친 사고 0건만."""
        from . import params as P
        from .tuning_eval import scorecard
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if 0 < length <= 200_000 else {}
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        if not isinstance(body, dict):
            return self._json({"error": "잘못된 요청"}, 400)
        note = str(body.get("note") or "")[:200]
        if action == "rollback":
            v = body.get("version")
            ok_v = v == "default" or (isinstance(v, int) and not isinstance(v, bool))
            target = self.storage.tuning_get(0 if v == "default" else v) if ok_v else None
            if target is None:
                return self._json({"error": "없는 설정 버전입니다"}, 400)
            params = target["params"]
            note = note or ("코드 기본값으로" if target["version"] == 0 else f"v{target['version']}로")
        else:
            params = body.get("params")
        why = P.validate(params)
        if why:
            return self._json({"error": why}, 400)
        t0 = time.time()
        card = scorecard(params)
        if time.time() - t0 > self.TUNING_TIMEOUT:
            return self._json({"error": "평가 시간 초과 — 적용하지 않았습니다"}, 503)
        if action == "evaluate":
            return self._json({"scorecard": card})
        author = _DASH_USER if _DASH_PW else "local"
        if card["missed"]:
            missed = [n for n, r in card["per"].items() if r["kind"] == "accident" and not r["ok"]]
            self.storage.log_event("", "", "tuning_reject",
                                   f"예지 기준 적용 거부 — 사고 {card['missed']}건 놓침({', '.join(missed)}) ({author})",
                                   source="user")
            return self._json({"error": f"사고 시나리오 {card['missed']}건을 놓쳐 적용할 수 없습니다",
                               "scorecard": card}, 409)
        base = body.get("base_version")
        base = base if isinstance(base, int) and not isinstance(base, bool) else None
        ver = self.storage.tuning_activate(params, author, note, card,
                                           "rollback" if action == "rollback" else "apply", base_version=base)
        if ver is None:
            return self._json({"error": "그 사이 다른 설정이 먼저 적용됐습니다 — 새 설정을 확인하세요",
                               "active": self.storage.tuning_active()}, 409)
        return self._json({"ok": True, "version": ver, "scorecard": card})

    # ── 자동 탐색 (AI 자동연결) ──────────────────────────────
    def _discover(self) -> None:
        """펌웨어의 탐색 로직을 실행해 토폴로지를 자동 구성한다.

        실기에서는 각 CCM이 자기 버스를 스캔해 결과를 올린다. 여기(시뮬레이션)에서는
        서버가 같은 펌웨어 코드(jcc_ccm.discovery)를 VirtualBus로 실행한다.
        """
        if _REQUIRE_AUTH and not os.environ.get("JCC_DEMO"):
            # 운영 서버: 가상 버스를 돌리면 가짜 장비가 실데이터에 섞인다. 실제 CCM이 보고한 인벤토리를 요약만 한다.
            devs = self.storage.list_devices()
            sens = [s for d in devs for s in d.get("latest") or []]
            inferred = sum(1 for s in sens if s.get("confidence") == "추정")
            return self._json({"ok": True, "mode": "live", "panel_name": "", "ccms": len(devs),
                               "sensors": len(sens), "identified": len(sens) - inferred, "inferred": inferred})
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

    # ── 자가치유 런타임 제어 (켜기/끄기 · L1/L2) ─────────────
    def _heal_config(self) -> dict:
        h = getattr(self.storage, "healer", None)
        if h is None:
            # 감시 스레드가 아직 안 붙었거나 꺼진 환경 — 안전한 기본값.
            return {"available": False, "enabled": True, "l1_enabled": True, "l2_enabled": True}
        return {"available": True, "enabled": bool(h.enabled),
                "l1_enabled": bool(h.l1_enabled), "l2_enabled": bool(h.l2_enabled)}

    def _set_heal_config(self) -> None:
        """{enabled?, l1_enabled?, l2_enabled?} — 자가치유를 런타임에 켜고 끈다."""
        h = getattr(self.storage, "healer", None)
        if h is None:
            return self._json({"error": "자가치유 제어를 사용할 수 없습니다"}, 503)
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        changed = []
        for field in ("enabled", "l1_enabled", "l2_enabled"):
            if field in body:
                new = bool(body[field])
                if getattr(h, field) != new:
                    setattr(h, field, new)
                    changed.append((field, new))
        if changed:
            label = {"enabled": "자가치유 전체", "l1_enabled": "채널 재시작(L1)",
                     "l2_enabled": "CCM 재시작(L2)"}
            detail = ", ".join(f"{label[f]} {'켜짐' if v else '꺼짐'}" for f, v in changed)
            self.storage.log_event("", "", "heal_config", f"자가치유 설정 변경: {detail}", source="user")
        return self._json(self._heal_config())

    # ── 예지 액추에이터 수동 조작 (벤트·히터·팬) ──────────────
    def _set_actuator(self) -> None:
        """{panel, actuator(vent|heater|fan), action(open|close|on|off|auto)} — 수동 오버라이드."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        panel = str(body.get("panel", ""))
        actuator = str(body.get("actuator", ""))
        action = str(body.get("action", ""))
        if actuator not in ("vent", "heater", "fan") or \
           action not in ("open", "close", "on", "off", "auto"):
            return self._json({"error": "actuator/action 값이 올바르지 않습니다"}, 400)
        if not self._in_scope(panel):
            return self._json({"error": "이 계정 범위 밖의 판넬입니다"}, 403)
        return self._json(self.storage.set_actuator(panel, actuator, action, by=self._user()["username"]))

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
        """{alarm_id, note?, cause?} — 활성 경보를 확인 처리(에스컬레이션 중단). 이미 확인한 경보엔 메모만 고친다."""
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
        al = self.storage.get_alarm(alarm_id)
        if al is None or not self._in_scope(al.get("device_id") or ""):
            return self._json({"error": "이 계정 범위 밖의 경보입니다"}, 403)
        ok = self.storage.ack_alarm(alarm_id, self._user()["username"], str(body.get("note") or ""),
                                    str(body.get("cause") or ""))
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

    def _serve_ota(self, name: str) -> None:
        """CCM이 받아갈 OTA 번들. 대시보드 로그인이 아니라 기기 키(Bearer)로 막는다.
        번들 자체는 비밀이 아니다 — 위조는 CCM의 서명 검증이 막는다."""
        denied = self._device_denied()
        if denied:
            return self._json(*denied)
        p = bundle_path(unquote(name))
        if not p:
            return self._json({"error": "not found"}, 404)
        try:
            with open(p, "rb") as fh:
                blob = fh.read()
        except OSError:
            return self._json({"error": "읽기 실패"}, 500)
        self.send_response(200)
        self.send_header("Content-Type", "application/gzip")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

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

    _FONT_TYPES = {".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}

    def _serve_font(self, name: str) -> None:
        """static/fonts/ 의 글꼴(woff2)과 라이선스 문서. 파일명만 취해 경로 탈출을 막는다. 1년 캐시."""
        safe = os.path.basename(unquote(urlparse(name).path))
        ext = os.path.splitext(safe)[1].lower()
        full = os.path.join(_STATIC_DIR, "fonts", safe)
        if not safe or ext not in self._FONT_TYPES or not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        with open(full, "rb") as fh:
            blob = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", self._FONT_TYPES[ext])
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(blob)

    # ── 로그인 ──────────────────────────────────────────────
    def _login(self) -> None:
        """{user, password} 확인 후 맞으면 세션 쿠키를 심는다. 같은 IP 연속 실패는 잠근다."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if 0 < length <= 10000 else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            body = {}
        if not self._auth_on():                # 인증 비활성(개발 모드)이면 그냥 통과
            return self._json({"ok": True, "auth": False})
        ip, now = client_ip(self), time.time()
        user, pw = str(body.get("user", "")), str(body.get("password", ""))
        ukey = "u:" + user.strip().lower()[:64]
        wait = max(_login_locked(ip, now), _login_locked(ukey, now, LOGIN_USER_MAX))
        if wait > 0:
            return self._json({"error": f"로그인 시도가 너무 많습니다 — {int(wait) + 1}초 뒤 다시 시도하세요",
                               "retry_after": int(wait) + 1}, 429)
        # 비상 admin(환경변수): 아이디·비번 둘 다 항상 비교(시간 차로도 어느 쪽이 틀렸는지 새지 않게)
        env_ok = (bool(_DASH_PW) and hmac.compare_digest(user.encode("utf-8"), _DASH_USER.encode("utf-8"))
                  and hmac.compare_digest(pw.encode("utf-8"), _DASH_PW.encode("utf-8")))
        acct = None if env_ok else self.storage.accounts.authenticate(user, pw)
        _login_record(ip, bool(env_ok or acct), now)
        _login_record(ukey, bool(env_ok or acct), now)
        if not (env_ok or acct):
            if self.storage.accounts.signup_state(user, pw) == "pending":
                return self._json({"error": "가입 신청을 확인하고 있습니다 — JCC가 승인하면 이 아이디로 로그인할 수 있습니다",
                                   "pending": True}, 403)
            left = LOGIN_MAX_FAILS - len(_login_fails.get(ip, []))
            return self._json({"error": "아이디 또는 비밀번호가 올바르지 않습니다"
                                        + (f" (남은 시도 {left}회)" if 0 < left <= 2 else "")}, 401)
        if env_ok:
            tok = _session_token()
            pub = {"username": _DASH_USER, "role": "admin", "customer": None, "must_change": False, "env": True}
        else:
            tok = self.storage.accounts.new_session(acct["id"])
            pub = dict({k: acct[k] for k in ("username", "role", "customer", "must_change")}, env=False)
        out = json.dumps({"ok": True, "user": pub}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        # '로그인 상태 유지'면 7일, 아니면 브라우저를 닫을 때까지(공용 PC). HttpOnly로 JS 접근 차단,
        # SameSite=Lax로 CSRF 완화, https(Render 등 프록시 뒤)면 Secure.
        remember = body.get("remember", True) is not False
        https = (self.headers.get("X-Forwarded-Proto", "") or "").lower() == "https"
        self.send_header("Set-Cookie", f"jcc_session={tok}; HttpOnly; Path=/; SameSite=Lax"
                         + ("; Max-Age=604800" if remember else "") + ("; Secure" if https else ""))
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _signup(self) -> None:
        """회원가입: 가입 코드가 맞으면 그 고객사 '보기 전용' 계정을 바로, 코드가 없으면 가입 신청(승인 대기)."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if 0 < length <= 10000 else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict):
            return self._json({"error": "잘못된 요청"}, 400)
        if not (self._auth_on() and _signup_on()):
            return self._json({"error": "지금은 회원가입을 받지 않습니다 — JCC에 문의해 주세요"}, 403)
        if not body.get("agree"):
            return self._json({"error": "개인정보 수집·이용에 동의해 주세요"}, 400)
        ip, now = client_ip(self), time.time()
        if not _signup_allowed(ip, now):
            return self._json({"error": "가입 시도가 너무 많습니다 — 한 시간 뒤 다시 시도하거나 JCC에 문의해 주세요"}, 429)
        ac = self.storage.accounts
        username, pw = str(body.get("username", "")).strip(), body.get("password", "")
        if bool(_DASH_PW) and username == _DASH_USER:
            return self._json({"error": "이미 있는 아이디입니다 — 다른 아이디를 정해 주세요"}, 400)
        code = str(body.get("code", "") or "").strip()
        try:
            if code:
                try:
                    got = ac.signup_with_code(code, username, pw, body.get("name", ""), body.get("phone", ""))
                except ValueError:
                    _signup_count(ip, now)
                    raise
                self.storage.log_event("", "", "account", f"회원가입(가입 코드): {username} → {got['customer']} 보기 전용",
                                       source="user")
                return self._json({"ok": True, "mode": "created", "customer": got["customer"]})
            _signup_count(ip, now)                 # 신청은 성공해도 센다(관리자 목록을 장난 신청으로 채우지 못하게)
            ac.signup_request(username, pw, body.get("name", ""), body.get("phone", ""), body.get("company", ""))
            self.storage.log_event("", "", "account", f"가입 신청: {username} ({str(body.get('company', ''))[:40]})",
                                   source="user")
            return self._json({"ok": True, "mode": "requested"})
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)

    def _logout(self) -> None:
        tok = self._cookies().get("jcc_session", "")
        if tok and len(tok) == 64:
            self.storage.accounts.end_session(tok)     # 서버 세션도 지운다(쿠키만 지우면 토큰은 살아 있음)
        out = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", "jcc_session=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _admin(self, action: str) -> None:
        """계정 관리(JCC 관리자만 — 권한 표에서 이미 걸렀다). 비번·토큰은 기록에 남기지 않는다."""
        ac, me = self.storage.accounts, self._user()
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            b = json.loads(self.rfile.read(length).decode("utf-8")) if 0 < length <= 100_000 else {}
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        if not isinstance(b, dict):
            return self._json({"error": "잘못된 요청"}, 400)

        def audit(text):
            self.storage.log_event("", "", "account", f"{text} ({me['username']})", source="user")

        def as_id(v):
            return v if isinstance(v, int) and not isinstance(v, bool) else None
        try:
            if action == "customer":
                cid = ac.create_customer(str(b.get("name", "")))
                audit(f"고객사 추가: {b.get('name')}")
                return self._json({"ok": True, "id": cid})
            if action == "panel":
                panel, cid = str(b.get("panel", "")), b.get("customer_id")
                if not panel or (cid is not None and (as_id(cid) is None or not ac.customer_exists(cid))):
                    return self._json({"error": "판넬과 고객사를 확인하세요"}, 400)
                ac.assign_panel(panel, cid)
                audit(f"판넬 {panel} → " + ("배정 해제(JCC만)" if cid is None else f"고객사 #{cid}"))
                return self._json({"ok": True})
            if action == "monthly_notify":
                cid = as_id(b.get("customer_id"))
                if cid is None or not ac.customer_exists(cid):
                    return self._json({"error": "고객사를 확인하세요"}, 400)
                on = bool(b.get("on"))
                ac.set_monthly_notify(cid, on)
                audit(f"고객사 #{cid} 월간 리포트 알림 {'켬' if on else '끔'}")
                return self._json({"ok": True})
            if action == "backup_now":            # 위험한 작업 전 등 — 지금 사본 하나
                from . import backup
                try:
                    got = backup.make_backup(self.storage, manual=True)
                except OSError as exc:            # 디스크 가득 참 등 — 연결을 끊지 말고 이유를
                    return self._json({"error": f"백업을 만들지 못했습니다({exc.__class__.__name__}) — 디스크 공간을 확인하세요"}, 500)
                audit(f"데이터 백업 만듦: {got['name']}")
                return self._json(dict(got, ok=True))
            if action == "contact":               # 고객 화면 담당 엔지니어·연락처
                cid = as_id(b.get("customer_id"))
                if cid is None or not ac.customer_exists(cid):
                    return self._json({"error": "고객사를 확인하세요"}, 400)
                try:
                    got = ac.set_contact(cid, b.get("engineer", ""), b.get("phone", ""))
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                audit(f"고객사 #{cid} 담당·연락처: {got['engineer'] or '-'} {got['engineer_phone'] or '(공통 번호)'}")
                return self._json(dict(got, ok=True))
            if action == "ai":
                cid = as_id(b.get("customer_id"))
                if cid is None or not ac.customer_exists(cid):
                    return self._json({"error": "고객사를 확인하세요"}, 400)
                on = bool(b.get("on"))
                ac.set_ai(cid, on)
                audit(f"고객사 #{cid} AI에게 물어보기 {'켬' if on else '끔'}")
                return self._json({"ok": True})
            if action == "receivers":
                cid = as_id(b.get("customer_id"))
                if cid is None or not ac.customer_exists(cid) or not isinstance(b.get("numbers"), list):
                    return self._json({"error": "고객사와 번호 목록이 필요합니다"}, 400)
                nums = ac.set_receivers(cid, b["numbers"])
                audit(f"고객사 #{cid} 알림 번호 {len(nums)}개")
                return self._json({"ok": True, "numbers": nums})
            if action == "user":
                cid = b.get("customer_id")
                if cid is not None and as_id(cid) is None:
                    return self._json({"error": "고객사를 확인하세요"}, 400)
                if bool(_DASH_PW) and str(b.get("username", "")).strip() == _DASH_USER:
                    return self._json({"error": "비상 관리자와 같은 아이디는 쓸 수 없습니다"}, 400)
                uid, temp = ac.create_user(str(b.get("username", "")), str(b.get("role", "")), cid)
                audit(f"계정 추가: {b.get('username')} ({b.get('role')})")
                return self._json({"ok": True, "id": uid, "temp_password": temp})   # 이번 한 번만 보여 준다
            if action == "signup_code":            # 고객사 가입 코드 발급·새로 발급·끄기
                cid = as_id(b.get("customer_id"))
                if cid is None or not ac.customer_exists(cid):
                    return self._json({"error": "고객사를 확인하세요"}, 400)
                code = ac.set_signup_code(cid, bool(b.get("on")))
                audit(f"고객사 #{cid} 가입 코드 " + ("새로 발급" if code else "끔"))
                return self._json({"ok": True, "code": code})
            if action in ("signup/approve", "signup/reject"):
                rid = as_id(b.get("request_id"))
                if rid is None:
                    return self._json({"error": "신청을 확인하세요"}, 400)
                if action == "signup/reject":
                    name = ac.reject_signup(rid)
                    audit(f"가입 신청 거절: {name}")
                    return self._json({"ok": True})
                cid = as_id(b.get("customer_id"))
                if cid is None:
                    return self._json({"error": "고객사를 골라 주세요"}, 400)
                got = ac.approve_signup(rid, cid, str(b.get("role", "viewer")))
                audit(f"가입 신청 승인: {got['username']} → 고객사 #{cid} ({b.get('role', 'viewer')})")
                return self._json(dict(got, ok=True))
            uid = as_id(b.get("user_id"))
            target = ac.get_user(uid) if uid is not None else None
            if target is None:
                return self._json({"error": "없는 계정입니다"}, 400)
            if action == "user/reset":
                temp = ac.reset_password(uid)
                audit(f"비번 초기화: {target['username']}")
                return self._json({"ok": True, "temp_password": temp})
            if action == "user/disable":
                if uid == me.get("id") and not me.get("env"):
                    return self._json({"error": "자기 계정은 끌 수 없습니다"}, 400)
                off = bool(b.get("disabled"))
                ac.set_disabled(uid, off)
                audit(f"계정 {'끔' if off else '켬'}: {target['username']}")
                return self._json({"ok": True})
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        return self._json({"error": "없는 관리 작업입니다"}, 404)

    def _commission(self, action: str) -> None:
        """설치 점검: 출력 시험 시작·완료 기록(JCC 관리자 — 권한 표에서 걸렀다)."""
        from .commission import check_panel, start_output_test
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            b = json.loads(self.rfile.read(length).decode("utf-8")) if 0 < length <= 100_000 else {}
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        panel, me = str(b.get("panel", "")), self._user()["username"]
        if action == "output_test":
            err = start_output_test(self.storage, panel, str(b.get("actuator", "")), me)
            return self._json({"error": err}, 400) if err else self._json({"ok": True})
        if action == "complete":
            rep = check_panel(self.storage, panel)
            if rep is None:
                return self._json({"error": "없는 판넬입니다"}, 404)
            rep["note"] = str(b.get("note", ""))[:500]
            rep["by"] = me
            verdict = {"pass": "합격", "warn": "조건부 합격", "wait": "미완료 항목 있음", "fail": "불합격"}[rep["overall"]]
            rid = self.storage.add_commission_report(panel, me, rep["overall"], rep)
            self.storage.log_event(panel, "", "commission", f"설치 점검 완료 기록: {rep['panel_name']} — {verdict} ({me})",
                                   source="user")
            return self._json({"ok": True, "id": rid, "overall": rep["overall"], "verdict": verdict})
        return self._json({"error": "없는 작업입니다"}, 404)

    def _change_password(self) -> None:
        """{old, new} — 본인 비번 변경. 성공하면 모든 세션이 끊기므로 다시 로그인한다."""
        u = self._user()
        if u.get("env"):
            return self._json({"error": "비상 관리자 계정의 비번은 서버 환경변수에서 바꿉니다"}, 400)
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if 0 < length <= 10000 else {}
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        why = self.storage.accounts.change_password(u["id"], str(body.get("old", "")), body.get("new"))
        if why:
            return self._json({"error": why}, 400)
        self.storage.log_event("", "", "account", f"{u['username']} 비번 변경", source="user")
        return self._json({"ok": True, "relogin": True})

    # ── 대시보드 ────────────────────────────────────────────
    def _serve_static_js(self, name: str) -> None:
        try:
            with open(os.path.join(_STATIC_DIR, name), "r", encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            return self._json({"error": "not found"}, 404)
        self._text(src, 200, "application/javascript; charset=utf-8")

    def _serve_dashboard(self, name: str = "index.html") -> None:
        index = os.path.join(_STATIC_DIR, name)
        try:
            with open(index, "r", encoding="utf-8") as fh:
                html = fh.read()
        except OSError:
            return self._text(f"화면 파일(static/{name})이 없습니다.", 500)
        self._text(html, 200, "text/html; charset=utf-8")


def make_server(host: str, port: int, storage: Storage) -> ThreadingHTTPServer:
    # 핸들러 클래스에 storage를 붙여 요청마다 공유하게 한다.
    if getattr(storage, "ai", None) is None:      # AI에게 물어보기(키·SDK 없으면 꺼진 채로 둔다)
        from .ai import Assistant, default_client
        storage.ai = Assistant(storage, *default_client())
    handler = type("BoundHandler", (Handler,), {"storage": storage})
    return ThreadingHTTPServer((host, port), handler)
