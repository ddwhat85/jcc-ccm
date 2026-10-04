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
CREATE TABLE IF NOT EXISTS signup_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, pw_hash TEXT NOT NULL,
    full_name TEXT DEFAULT '', phone TEXT DEFAULT '', company TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending', created_at REAL, decided_at REAL, customer_id INTEGER);
"""

# 회원가입
#   가입 코드: JCC가 고객사마다 발급해 담당자에게 건넨다. 코드로 가입하면 그 고객사 '보기 전용' 계정이 바로 생긴다.
#   코드 없이: '가입 신청'으로 남고, JCC 관리자가 어느 고객사인지 골라 승인해야 로그인된다.
_CODE_ALPHA = "ACDEFGHJKLMNPQRTUVWXY34679"       # 헷갈리는 글자(0·O·1·I·B·8·S·5·Z·2) 제외
PENDING_MAX = 200                                   # 대기 중 신청 상한(장난 신청으로 DB가 차지 않게)


def norm_code(code) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(code or "").upper())


def _new_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHA) for _ in range(8))
    return raw[:4] + "-" + raw[4:]


RECEIVER_HOURS = ("always", "day", "night")


def is_work_hours(now: float | None = None) -> bool:
    """평일 8~18시(한국 시간). 공휴일은 모른다 — 공휴일에도 받고 싶은 사람은 '항상'으로."""
    from datetime import datetime, timedelta, timezone
    d = datetime.fromtimestamp(time.time() if now is None else now, timezone(timedelta(hours=9)))
    return d.weekday() < 5 and 8 <= d.hour < 18


def _clean_phone(phone) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit() or ch in "+-")[:20]


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
            #   weekly_notify : 주간 안전 요약 문자(기본 꺼짐 — 실제 발송·비용)
            for col in ("monthly_notify", "ai_enabled", "weekly_notify"):
                try:
                    self._conn.execute(f"ALTER TABLE customers ADD COLUMN {col} INTEGER DEFAULT 0")
                except Exception:  # noqa: BLE001 - 이미 있음
                    pass
            #   engineer·engineer_phone: 고객 화면에 보이는 이 고객사 담당 엔지니어와 연락처(없으면 공통 대표 번호)
            #   signup_code: 고객사 가입 코드(비면 코드 가입 꺼짐)
            for col in ("engineer", "engineer_phone", "signup_code"):
                try:
                    self._conn.execute(f"ALTER TABLE customers ADD COLUMN {col} TEXT DEFAULT ''")
                except Exception:  # noqa: BLE001 - 이미 있음
                    pass
            #   keep_years: 데이터 장기 보관(0 = 기본, 3·5년 — 유료 묶음). 경보·활동 기록과 시간별 값을 그 햇수만큼
            try:
                self._conn.execute("ALTER TABLE customers ADD COLUMN keep_years INTEGER DEFAULT 0")
            except Exception:  # noqa: BLE001 - 이미 있음
                pass
            #   알림 받는 사람: 이름·받는 시간(항상/근무시간/야간·주말 당직)·정기 문자(주간 요약·월간 보고서 알림) 받기
            for col, ddl in (("name", "TEXT DEFAULT ''"), ("hours", "TEXT DEFAULT 'always'"), ("reports", "INTEGER DEFAULT 1")):
                try:
                    self._conn.execute(f"ALTER TABLE customer_receivers ADD COLUMN {col} {ddl}")
                except Exception:  # noqa: BLE001 - 이미 있음
                    pass
            #   full_name·phone: 가입할 때 받은 이름·휴대폰(계정 관리에서 누구인지 알아보게)
            for col in ("full_name", "phone"):
                try:
                    self._conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT ''")
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
            rows = self._conn.execute("SELECT id, name, created_at, monthly_notify, ai_enabled, weekly_notify, engineer, "
                                      "engineer_phone, signup_code, keep_years FROM customers ORDER BY id").fetchall()
        return [dict(dict(r), monthly_notify=bool(r["monthly_notify"]), ai_enabled=bool(r["ai_enabled"]),
                     keep_years=int(r["keep_years"] or 0),
                     weekly_notify=bool(r["weekly_notify"]),
                     engineer=r["engineer"] or "", engineer_phone=r["engineer_phone"] or "",
                     signup_code=r["signup_code"] or "") for r in rows]

    def set_keep_years(self, customer_id: int, years: int) -> None:
        if years not in (0, 3, 5):
            raise ValueError("보관 기간은 기본·3년·5년 중 하나입니다")
        with self._lock:
            self._conn.execute("UPDATE customers SET keep_years=? WHERE id=?", (years, customer_id))
            self._conn.commit()

    def set_contact(self, customer_id: int, engineer: str, phone: str) -> dict:
        """고객 화면의 담당 엔지니어·연락처. 전화는 숫자·+·- 만 남긴다(9~13자리, 비우면 공통 번호)."""
        engineer = str(engineer or "").strip()[:40]
        phone = "".join(ch for ch in str(phone or "") if ch.isdigit() or ch in "+-")[:20]
        digits = sum(ch.isdigit() for ch in phone)
        if phone and not 9 <= digits <= 13:
            raise ValueError("전화번호를 확인하세요 (숫자 9~13자리)")
        with self._lock:
            self._conn.execute("UPDATE customers SET engineer=?, engineer_phone=? WHERE id=?", (engineer, phone, customer_id))
            self._conn.commit()
        return {"engineer": engineer, "engineer_phone": phone}

    def set_monthly_notify(self, customer_id: int, on: bool) -> None:
        with self._lock:
            self._conn.execute("UPDATE customers SET monthly_notify=? WHERE id=?", (1 if on else 0, customer_id))
            self._conn.commit()

    def set_weekly_notify(self, customer_id: int, on: bool) -> None:
        with self._lock:
            self._conn.execute("UPDATE customers SET weekly_notify=? WHERE id=?", (1 if on else 0, customer_id))
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
        """번호만 바꾸는 예전 방식(직원 화면) — 남는 번호의 이름·받는 시간 설정은 그대로 둔다."""
        old = {r["number"]: r for r in self.list_receivers(customer_id)}
        items = []
        for n in numbers or []:
            d = re.sub(r"\D", "", str(n))
            items.append(old.get(d) or {"number": d})
        return [r["number"] for r in self.set_receiver_list(customer_id, items)]

    def set_receiver_list(self, customer_id: int, items: list) -> list:
        """알림 받는 사람 전체를 바꾼다(최대 20명). 번호가 이상한 줄은 버리고, 같은 번호는 한 번만."""
        clean, seen = [], set()
        for it in items or []:
            if not isinstance(it, dict):
                continue
            d = re.sub(r"\D", "", str(it.get("number") or ""))
            if not 9 <= len(d) <= 12 or d in seen:
                continue
            seen.add(d)
            hours = it.get("hours") if it.get("hours") in RECEIVER_HOURS else "always"
            clean.append({"number": d, "name": str(it.get("name") or "").strip()[:30], "hours": hours,
                          "reports": bool(it.get("reports", True))})
        clean = clean[:20]
        with self._lock:
            self._conn.execute("DELETE FROM customer_receivers WHERE customer_id=?", (customer_id,))
            self._conn.executemany("INSERT INTO customer_receivers (customer_id, number, name, hours, reports) VALUES (?, ?, ?, ?, ?)",
                                   [(customer_id, r["number"], r["name"], r["hours"], int(r["reports"])) for r in clean])
            self._conn.commit()
        return clean

    def list_receivers(self, customer_id) -> list:
        with self._lock:
            rows = self._conn.execute("SELECT number, name, hours, reports FROM customer_receivers WHERE customer_id=? "
                                      "ORDER BY rowid", (customer_id,)).fetchall()
        return [{"number": r["number"], "name": r["name"] or "", "hours": r["hours"] or "always",
                 "reports": bool(r["reports"] if r["reports"] is not None else 1)} for r in rows]

    def receivers_for(self, customer_id, purpose: str = "all", now: float | None = None) -> list:
        """purpose: 'alarm' = 지금 시각에 받기로 한 사람(근무시간 = 평일 8~18시 한국 시간, 나머지는 야간·주말 당직).
        그 시각에 받을 사람이 아무도 없으면 전원 — 위험 알림을 아무도 못 받게 두지 않는다.
        'report' = 정기 문자(주간 요약·월간 보고서 알림)를 받기로 한 사람. 'all' = 전원."""
        rows = self.list_receivers(customer_id)
        if purpose == "report":
            return [r["number"] for r in rows if r["reports"]]
        if purpose == "alarm":
            day = is_work_hours(now)
            pick = [r["number"] for r in rows if r["hours"] == "always" or (r["hours"] == "day") == day]
            return pick or [r["number"] for r in rows]
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
        keys = r.keys()
        return {"id": r["id"], "username": r["username"], "role": r["role"], "customer_id": r["customer_id"],
                "customer": name, "must_change": bool(r["must_change"]), "disabled": bool(r["disabled"]),
                "full_name": (r["full_name"] if "full_name" in keys else "") or "",
                "phone": (r["phone"] if "phone" in keys else "") or ""}

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

    # ── 회원가입 ───────────────────────────────────────────
    def set_signup_code(self, customer_id: int, on: bool) -> str:
        """가입 코드 새로 발급(on) 또는 끄기. 새로 발급하면 예전 코드는 바로 못 쓴다."""
        with self._lock:
            code = ""
            if on:
                for _ in range(20):
                    code = _new_code()
                    if not self._conn.execute("SELECT 1 FROM customers WHERE signup_code=?", (code,)).fetchone():
                        break
            self._conn.execute("UPDATE customers SET signup_code=? WHERE id=?", (code, customer_id))
            self._conn.commit()
        return code

    def customer_by_code(self, code):
        """가입 코드 → (고객사 id, 이름). 맞는 코드가 없으면 None. 하이픈·대소문자는 무시."""
        want = norm_code(code)
        if len(want) != 8:
            return None
        with self._lock:
            rows = self._conn.execute("SELECT id, name, signup_code FROM customers WHERE signup_code != ''").fetchall()
        for r in rows:
            if secrets.compare_digest(norm_code(r["signup_code"]), want):
                return r["id"], r["name"]
        return None

    def _taken(self, username: str) -> bool:
        return (self._conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone() is not None or
                self._conn.execute("SELECT 1 FROM signup_requests WHERE username=? AND status='pending'",
                                   (username,)).fetchone() is not None)

    def _signup_fields(self, username, pw, full_name, phone):
        username = str(username or "").strip()
        if not _USER_RE.match(username):
            raise ValueError("아이디는 3~40자 영문·숫자·._@- 만 됩니다")
        if not isinstance(pw, str) or len(pw) < MIN_PW:
            raise ValueError(f"비밀번호는 {MIN_PW}자 이상이어야 합니다")
        if pw.strip().lower() == username.lower():
            raise ValueError("비밀번호를 아이디와 다르게 정해 주세요")
        full_name = str(full_name or "").strip()[:40]
        if not full_name:
            raise ValueError("이름을 입력해 주세요")
        phone = _clean_phone(phone)
        if not 9 <= sum(ch.isdigit() for ch in phone) <= 13:
            raise ValueError("휴대폰 번호를 확인해 주세요")
        return username, full_name, phone

    def signup_with_code(self, code, username, pw, full_name, phone) -> dict:
        """가입 코드로 가입 — 그 고객사 '보기 전용' 계정을 바로 만든다(본인이 정한 비번이라 첫 변경 없음)."""
        hit = self.customer_by_code(code)
        if hit is None:
            raise ValueError("가입 코드가 맞지 않습니다 — JCC 담당자에게 받은 코드를 확인해 주세요")
        username, full_name, phone = self._signup_fields(username, pw, full_name, phone)
        with self._lock:
            if self._taken(username):
                raise ValueError("이미 있는 아이디입니다 — 다른 아이디를 정해 주세요")
            cur = self._conn.execute(
                "INSERT INTO users (username, pw_hash, role, customer_id, must_change, disabled, created_at, full_name, phone) "
                "VALUES (?, ?, 'viewer', ?, 0, 0, ?, ?, ?)", (username, hash_pw(pw), hit[0], time.time(), full_name, phone))
            self._conn.commit()
            return {"id": cur.lastrowid, "customer_id": hit[0], "customer": hit[1]}

    def signup_request(self, username, pw, full_name, phone, company) -> int:
        """코드 없이 가입 신청 — JCC 관리자가 고객사를 골라 승인해야 로그인된다."""
        username, full_name, phone = self._signup_fields(username, pw, full_name, phone)
        company = str(company or "").strip()[:80]
        if not company:
            raise ValueError("회사명을 입력해 주세요")
        with self._lock:
            if self._taken(username):
                raise ValueError("이미 있는 아이디입니다 — 다른 아이디를 정해 주세요")
            n = self._conn.execute("SELECT COUNT(*) FROM signup_requests WHERE status='pending'").fetchone()[0]
            if n >= PENDING_MAX:
                raise ValueError("지금은 가입 신청을 받을 수 없습니다 — JCC에 전화로 문의해 주세요")
            cur = self._conn.execute(
                "INSERT INTO signup_requests (username, pw_hash, full_name, phone, company, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?)", (username, hash_pw(pw), full_name, phone, company, time.time()))
            self._conn.commit()
            return cur.lastrowid

    def list_signup_requests(self) -> list:
        with self._lock:
            rows = self._conn.execute("SELECT id, username, full_name, phone, company, created_at FROM signup_requests "
                                      "WHERE status='pending' ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def approve_signup(self, rid: int, customer_id: int, role: str) -> dict:
        """신청 승인 — 신청자가 정한 비번 그대로 계정을 만든다."""
        if role not in ("manager", "viewer"):
            raise ValueError("등급은 담당자·보기 전용 중 하나")
        if not self.customer_exists(customer_id):
            raise ValueError("고객사를 골라 주세요")
        with self._lock:
            r = self._conn.execute("SELECT * FROM signup_requests WHERE id=? AND status='pending'", (rid,)).fetchone()
            if not r:
                raise ValueError("이미 처리했거나 없는 신청입니다")
            if self._conn.execute("SELECT 1 FROM users WHERE username=?", (r["username"],)).fetchone():
                raise ValueError("그 아이디로 이미 계정이 있습니다 — 신청을 거절하고 다른 아이디로 다시 신청받으세요")
            cur = self._conn.execute(
                "INSERT INTO users (username, pw_hash, role, customer_id, must_change, disabled, created_at, full_name, phone) "
                "VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?)",
                (r["username"], r["pw_hash"], role, customer_id, time.time(), r["full_name"], r["phone"]))
            self._conn.execute("UPDATE signup_requests SET status='approved', decided_at=?, customer_id=?, pw_hash='' "
                               "WHERE id=?", (time.time(), customer_id, rid))
            self._conn.commit()
            return {"id": cur.lastrowid, "username": r["username"]}

    def reject_signup(self, rid: int) -> str:
        with self._lock:
            r = self._conn.execute("SELECT username FROM signup_requests WHERE id=? AND status='pending'", (rid,)).fetchone()
            if not r:
                raise ValueError("이미 처리했거나 없는 신청입니다")
            # 거절하면 신청(이름·휴대폰·비번 해시)을 통째로 지운다 — 동의서의 '거절되면 지체 없이 삭제'
            self._conn.execute("DELETE FROM signup_requests WHERE id=?", (rid,))
            self._conn.commit()
            return r["username"]

    def signup_state(self, username: str, pw: str):
        """로그인이 안 될 때 안내용: 비번까지 맞는 대기 중 신청이면 'pending'. 아이디만으로는 알려 주지 않는다."""
        with self._lock:
            r = self._conn.execute("SELECT pw_hash FROM signup_requests WHERE username=? AND status='pending' "
                                   "ORDER BY id DESC LIMIT 1", ((username or "").strip(),)).fetchone()
        if r and r["pw_hash"] and check_pw(pw or "", r["pw_hash"]):
            return "pending"
        return None

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
