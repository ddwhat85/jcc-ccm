"""계정·세션·고객사·판넬 배정·알림 번호.

등급: admin(JCC 관리자, 전체) · manager(고객 담당자, 자기 고객사 판넬 보기·경보 확인·수동 조작) ·
      viewer(고객 보기 전용). 계정은 JCC만 발급한다(고객 자가 가입 없음).

보안 원칙
  · 비번은 PBKDF2-SHA256(200,000회, 16바이트 솔트)로만 저장 — 복원 불가, 비교는 상수시간
  · 세션: 쿠키엔 32바이트 난수, DB엔 그 SHA-256만(DB가 새도 세션을 훔칠 수 없음), 7일 만료
  · 비번 변경·초기화·계정 끄기 → 그 사용자 세션 전부 삭제(즉시 로그아웃)
  · 새 계정·초기화는 임시 비번을 한 번만 돌려주고, 첫 로그인에 반드시 바꾸게 한다(must_change)

storage의 SQLite 연결과 락을 같이 쓴다(한 파일 DB).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

ROLES = ("admin", "manager", "viewer")
ITERATIONS = 200_000
SESSION_TTL = 7 * 86400
MIN_PW = 10
_USER_RE = re.compile(r"^[A-Za-z0-9._@-]{3,40}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created_at REAL);
CREATE TABLE IF NOT EXISTS panel_owner (
    panel TEXT PRIMARY KEY, customer_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS customer_receivers (
    customer_id INTEGER NOT NULL, number TEXT NOT NULL, PRIMARY KEY (customer_id, number));
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, pw_hash TEXT NOT NULL,
    role TEXT NOT NULL, customer_id INTEGER, must_change INTEGER DEFAULT 1, disabled INTEGER DEFAULT 0,
    created_at REAL);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at REAL, expires_at REAL);
"""


def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${dk.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        algo, it, salt, dk = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        got = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(got.hex(), dk)
    except (ValueError, AttributeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _temp_pw() -> str:
    """사람이 받아 적기 쉬운 임시 비번(헷갈리는 글자 제외, 14자)."""
    alphabet = "abcdefghjkmnpqrstuvwxyzACDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(14))


class Accounts:
    def __init__(self, conn, lock):
        self._conn, self._lock = conn, lock
        with self._lock:
            self._conn.executescript(SCHEMA)
            # 구버전 DB 열 추가(이미 있으면 넘어감)
            #   monthly_notify: 월간 리포트 알림(기본 꺼짐 — 실제 문자가 나가므로 JCC가 켠 고객사만)
            #   ai_enabled    : AI에게 물어보기(기본 꺼짐 — 데이터가 외부 AI로 나가므로 계약·동의 확인 후 JCC가 켬)
            for col in ("monthly_notify", "ai_enabled"):
                try:
                    self._conn.execute(f"ALTER TABLE customers ADD COLUMN {col} INTEGER DEFAULT 0")
                except Exception:  # noqa: BLE001 - 이미 있음
                    pass
            self._conn.commit()

    # ── 고객사·판넬 ─────────────────────────────────────────
    def create_customer(self, name: str) -> int:
        name = (name or "").strip()[:80]
        if not name:
            raise ValueError("고객사 이름이 필요합니다")
        with self._lock:
            cur = self._conn.execute("INSERT INTO customers (name, created_at) VALUES (?, ?)", (name, time.time()))
            self._conn.commit()
            return cur.lastrowid

    def list_customers(self) -> list:
        with self._lock:
            rows = self._conn.execute("SELECT id, name, created_at, monthly_notify, ai_enabled "
                                      "FROM customers ORDER BY id").fetchall()
        return [dict(dict(r), monthly_notify=bool(r["monthly_notify"]), ai_enabled=bool(r["ai_enabled"]))
                for r in rows]

    def set_monthly_notify(self, customer_id: int, on: bool) -> None:
        with self._lock:
            self._conn.execute("UPDATE customers SET monthly_notify=? WHERE id=?", (1 if on else 0, customer_id))
            self._conn.commit()

    def set_ai(self, customer_id: int, on: bool) -> None:
        with self._lock:
            self._conn.execute("UPDATE customers SET ai_enabled=? WHERE id=?", (1 if on else 0, customer_id))
            self._conn.commit()

    def ai_customers(self) -> set:
        """AI가 켜진 고객사 id."""
        with self._lock:
            rows = self._conn.execute("SELECT id FROM customers WHERE ai_enabled=1").fetchall()
        return {r["id"] for r in rows}

    def customer_exists(self, cid) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM customers WHERE id=?", (cid,)).fetchone() is not None

    def assign_panel(self, panel: str, customer_id) -> None:
        """판넬을 고객사에 배정(None이면 해제 — 그 판넬은 JCC만 본다)."""
        with self._lock:
            if customer_id is None:
                self._conn.execute("DELETE FROM panel_owner WHERE panel=?", (panel,))
            else:
                self._conn.execute("INSERT INTO panel_owner (panel, customer_id) VALUES (?, ?) "
                                   "ON CONFLICT(panel) DO UPDATE SET customer_id=excluded.customer_id",
                                   (panel, int(customer_id)))
            self._conn.commit()

    def panel_owner_map(self) -> dict:
        with self._lock:
            rows = self._conn.execute("SELECT panel, customer_id FROM panel_owner").fetchall()
        return {r["panel"]: r["customer_id"] for r in rows}

    def set_receivers(self, customer_id: int, numbers: list) -> list:
        clean = []
        for n in numbers or []:
            d = re.sub(r"\D", "", str(n))
            if 9 <= len(d) <= 12 and d not in clean:
                clean.append(d)
        with self._lock:
            self._conn.execute("DELETE FROM customer_receivers WHERE customer_id=?", (customer_id,))
            self._conn.executemany("INSERT INTO customer_receivers (customer_id, number) VALUES (?, ?)",
                                   [(customer_id, n) for n in clean[:20]])
            self._conn.commit()
        return clean[:20]

    def receivers_for(self, customer_id) -> list:
        with self._lock:
            rows = self._conn.execute("SELECT number FROM customer_receivers WHERE customer_id=? ORDER BY rowid",
                                      (customer_id,)).fetchall()
        return [r["number"] for r in rows]

    # ── 계정 ───────────────────────────────────────────────
    def has_users(self) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    def _user_view(self, r) -> dict:
        name = None
        if r["customer_id"] is not None:
            c = self._conn.execute("SELECT name FROM customers WHERE id=?", (r["customer_id"],)).fetchone()
            name = c["name"] if c else None
        return {"id": r["id"], "username": r["username"], "role": r["role"], "customer_id": r["customer_id"],
                "customer": name, "must_change": bool(r["must_change"]), "disabled": bool(r["disabled"])}

    def create_user(self, username: str, role: str, customer_id):
        """새 계정 + 임시 비번(한 번만 돌려줌). admin은 고객사 없음, 고객 등급은 고객사 필수."""
        username = (username or "").strip()
        if not _USER_RE.match(username):
            raise ValueError("아이디는 3~40자 영문·숫자·._@- 만 됩니다")
        if role not in ROLES:
            raise ValueError("등급은 admin·manager·viewer 중 하나")
        if role == "admin" and customer_id is not None:
            raise ValueError("JCC 관리자는 고객사에 속하지 않습니다")
        if role != "admin" and (customer_id is None or not self.customer_exists(customer_id)):
            raise ValueError("고객 계정은 고객사를 지정해야 합니다")
        temp = _temp_pw()
        with self._lock:
            if self._conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise ValueError("이미 있는 아이디입니다")
            cur = self._conn.execute(
                "INSERT INTO users (username, pw_hash, role, customer_id, must_change, disabled, created_at) "
                "VALUES (?, ?, ?, ?, 1, 0, ?)", (username, hash_pw(temp), role, customer_id, time.time()))
            self._conn.commit()
            return cur.lastrowid, temp

    def list_users(self) -> list:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM users ORDER BY id").fetchall()
            return [self._user_view(r) for r in rows]

    def get_user(self, uid: int):
        with self._lock:
            r = self._conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            return self._user_view(r) if r else None

    def _end_user_sessions(self, uid: int) -> None:
        self._conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))

    def reset_password(self, uid: int) -> str:
        temp = _temp_pw()
        with self._lock:
            n = self._conn.execute("UPDATE users SET pw_hash=?, must_change=1 WHERE id=?", (hash_pw(temp), uid)).rowcount
            if not n:
                raise ValueError("없는 계정입니다")
            self._end_user_sessions(uid)
            self._conn.commit()
        return temp

    def set_disabled(self, uid: int, disabled: bool) -> None:
        with self._lock:
            n = self._conn.execute("UPDATE users SET disabled=? WHERE id=?", (1 if disabled else 0, uid)).rowcount
            if not n:
                raise ValueError("없는 계정입니다")
            if disabled:
                self._end_user_sessions(uid)
            self._conn.commit()

    def change_password(self, uid: int, old: str, new: str):
        """본인 비번 변경. None=성공, 문자열=거부 사유. 성공하면 다른 세션 전부 끊김."""
        if not isinstance(new, str) or len(new) < MIN_PW:
            return f"새 비번은 {MIN_PW}자 이상이어야 합니다"
        with self._lock:
            r = self._conn.execute("SELECT pw_hash FROM users WHERE id=?", (uid,)).fetchone()
        if not r or not check_pw(old or "", r["pw_hash"]):
            return "지금 비번이 맞지 않습니다"
        if old == new:
            return "지금 비번과 달라야 합니다"
        with self._lock:
            self._conn.execute("UPDATE users SET pw_hash=?, must_change=0 WHERE id=?", (hash_pw(new), uid))
            self._end_user_sessions(uid)
            self._conn.commit()
        return None

    def authenticate(self, username: str, pw: str):
        with self._lock:
            r = self._conn.execute("SELECT * FROM users WHERE username=?", ((username or "").strip(),)).fetchone()
        if not r:
            check_pw(pw or "", hash_pw("timing-equalizer"))   # 없는 아이디도 비슷한 시간이 걸리게
            return None
        if r["disabled"] or not check_pw(pw or "", r["pw_hash"]):
            return None
        with self._lock:
            return self._user_view(r)

    # ── 세션 ───────────────────────────────────────────────
    def new_session(self, uid: int) -> str:
        token = secrets.token_hex(32)
        now = time.time()
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            self._conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                               (_token_hash(token), uid, now, now + SESSION_TTL))
            self._conn.commit()
        return token

    def session_user(self, token: str):
        if not token or len(token) != 64:
            return None
        with self._lock:
            r = self._conn.execute(
                "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash=? AND s.expires_at > ? AND u.disabled = 0",
                (_token_hash(token), time.time())).fetchone()
            return self._user_view(r) if r else None

    def end_session(self, token: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token or ""),))
            self._conn.commit()
