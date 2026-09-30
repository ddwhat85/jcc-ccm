"""계정 저장소(accounts) 테스트 — 비번 해시·계정·세션·고객사·판넬 배정·알림 번호.

python -m tests.test_accounts   (server/ 에서)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server import accounts as A
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run():
    print("=== 비번 해시 ===")
    h = A.hash_pw("correct horse")
    check("해시는 평문을 담지 않음", "correct" not in h and h.startswith("pbkdf2_sha256$"), h[:30])
    check("같은 비번도 솔트가 달라 해시가 다름", A.hash_pw("correct horse") != h)
    check("맞는 비번 통과", A.check_pw("correct horse", h))
    check("틀린 비번 거부", not A.check_pw("correct horsE", h))
    check("깨진 해시는 예외 없이 거부", not A.check_pw("x", "garbage") and not A.check_pw("x", ""))

    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    ac = st.accounts
    try:
        print("\n=== 고객사·판넬 ===")
        ca = ac.create_customer("A 제조")
        cb = ac.create_customer("B 물류")
        check("고객사 목록", [c["name"] for c in ac.list_customers()] == ["A 제조", "B 물류"])
        try:
            ac.create_customer("  ")
            check("빈 고객사 이름 거부", False)
        except ValueError:
            check("빈 고객사 이름 거부", True)
        ac.assign_panel("panel-01", ca)
        ac.assign_panel("panel-02", cb)
        ac.assign_panel("panel-03", ca)
        ac.assign_panel("panel-03", None)            # 배정 해제
        check("판넬 배정 맵", ac.panel_owner_map() == {"panel-01": ca, "panel-02": cb})
        ac.set_receivers(ca, ["010-1111-2222", "01033334444", "01033334444", "abc"])
        check("알림 번호 정리(숫자만·중복 제거·형식 틀림 제외)", ac.receivers_for(ca) == ["01011112222", "01033334444"],
              str(ac.receivers_for(ca)))

        print("\n=== 계정 ===")
        uid, temp = ac.create_user("kim@a", "manager", ca)
        check("임시 비번 12자 이상", len(temp) >= 12)
        u = ac.authenticate("kim@a", temp)
        check("임시 비번 로그인 → 변경 필요 표시", u and u["role"] == "manager" and u["customer_id"] == ca
              and u["customer"] == "A 제조" and u["must_change"])
        check("틀린 비번 → None", ac.authenticate("kim@a", temp + "x") is None)
        check("없는 계정 → None", ac.authenticate("nobody", temp) is None)
        for bad in (("x", "manager", ca), ("kim@a", "manager", ca), ("lee", "owner", ca), ("lee", "manager", None),
                    ("jcc", "admin", ca)):
            try:
                ac.create_user(*bad)
                check(f"잘못된 계정 거부 {bad}", False)
            except ValueError:
                check(f"잘못된 계정 거부 {bad[:2]}", True)
        aid, atemp = ac.create_user("ops", "admin", None)
        check("admin은 고객사 없음", ac.authenticate("ops", atemp)["customer_id"] is None)

        print("\n=== 세션 ===")
        tok = ac.new_session(uid)
        tok2 = ac.new_session(uid)
        check("세션 토큰 32바이트 난수(64 hex)", len(tok) == 64 and tok != tok2)
        check("세션으로 사용자 확인", ac.session_user(tok)["username"] == "kim@a")
        check("DB엔 토큰 원문이 없음", tok not in open(db, "rb").read().decode("latin-1"))
        check("틀린 토큰 → None", ac.session_user("0" * 64) is None and ac.session_user("") is None)
        why = ac.change_password(uid, "wrong-old", "new-password-123")
        check("비번 변경: 옛 비번 틀리면 거부", isinstance(why, str))
        why = ac.change_password(uid, temp, "short")
        check("비번 변경: 10자 미만 거부", isinstance(why, str) and "10" in why, str(why))
        check("비번 변경 성공", ac.change_password(uid, temp, "new-password-123") is None)
        check("비번 바꾸면 기존 세션 전부 끊김", ac.session_user(tok) is None and ac.session_user(tok2) is None)
        u = ac.authenticate("kim@a", "new-password-123")
        check("새 비번 로그인 · 변경 필요 해제", u and not u["must_change"])
        tok3 = ac.new_session(uid)
        ac.set_disabled(uid, True)
        check("계정 끄면 로그인·세션 모두 막힘", ac.authenticate("kim@a", "new-password-123") is None
              and ac.session_user(tok3) is None)
        ac.set_disabled(uid, False)
        temp2 = ac.reset_password(uid)
        check("초기화 → 새 임시 비번·변경 필요", ac.authenticate("kim@a", temp2)["must_change"]
              and ac.authenticate("kim@a", "new-password-123") is None)
        tok4 = ac.new_session(uid)
        ac.end_session(tok4)
        check("로그아웃하면 세션 끝", ac.session_user(tok4) is None)
        # 만료
        tok5 = ac.new_session(uid)
        with st._lock:
            st._conn.execute("UPDATE sessions SET expires_at=?", (time.time() - 1,))
            st._conn.commit()
        check("만료 세션 거부", ac.session_user(tok5) is None)
        users = ac.list_users()
        check("계정 목록에 비번 해시 없음", users and all("pw_hash" not in x for x in users))
    finally:
        st.close()
        os.unlink(db)

    print()
    if _fails:
        print(f"❌ {len(_fails)} FAIL: {_fails}")
        return 1
    print("✅ 전체 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
