"""고객사별 알림 수신 — 경보가 난 판넬의 고객사 번호 + JCC 운영 번호로(중복 제거). 실제 발송은 하지 않는다.

python -m tests.test_notify_receivers   (server/ 에서)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server import notify
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run():
    sent = []
    notify._post_form = lambda url, fields: (sent.append(dict(fields)) or {"result_code": "1", "message": "ok"})  # 네트워크 차단
    env = {"JCC_ALIGO_KEY": "k-test", "JCC_ALIGO_USER": "u-test", "JCC_ALIGO_SENDER": "0212345678",
           "JCC_ALIGO_RECEIVERS": "01000000001,01000000002"}
    old = {k: os.environ.get(k) for k in list(env) + ["JCC_ALIGO_SENDERKEY", "JCC_ALIGO_TPL", "JCC_WEBHOOK"]}
    os.environ.update(env)
    for k in ("JCC_ALIGO_SENDERKEY", "JCC_ALIGO_TPL", "JCC_WEBHOOK"):
        os.environ.pop(k, None)
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    try:
        ca = st.accounts.create_customer("A 제조")
        st.accounts.assign_panel("panel-A", ca)
        st.accounts.set_receivers(ca, ["01011112222", "01000000002"])       # 하나는 JCC 번호와 겹침
        now = time.time()
        for dev, panel in (("ccm-a1", "panel-A"), ("ccm-x1", "panel-X")):
            st.ingest({"device_id": dev, "panel": panel, "readings": [
                {"key": "t", "name": "t", "unit": "", "value": 1, "ok": True, "ts": now}]})
        check("판넬 고객사 번호", st.receivers_for_device("ccm-a1") == ["01011112222", "01000000002"])
        check("미배정 판넬은 고객 번호 없음", st.receivers_for_device("ccm-x1") == [])
        check("모르는 기기도 예외 없이 빈 목록", st.receivers_for_device("nope") == [])

        out = notify.dispatch({"device_id": "ccm-a1", "severity": "crit", "detail": "화재 징조"}, "발생",
                              extra_receivers=st.receivers_for_device("ccm-a1"))
        rc = sent[-1]["receiver"].split(",")
        check("문자: JCC 운영 + 고객사 번호(중복 제거)", rc == ["01000000001", "01000000002", "01011112222"], str(rc))
        check("발송 결과 수", out and out[0].get("sent") == 3, str(out))
        notify.dispatch({"device_id": "ccm-x1", "severity": "crit", "detail": "x"}, "발생",
                        extra_receivers=st.receivers_for_device("ccm-x1"))
        check("미배정 판넬은 JCC 운영 번호만", sent[-1]["receiver"].split(",") == ["01000000001", "01000000002"])
        os.environ["JCC_ALIGO_RECEIVERS"] = ""
        notify.dispatch({"device_id": "ccm-a1", "severity": "crit", "detail": "y"}, "발생",
                        extra_receivers=["01011112222"])
        check("JCC 번호가 없어도 고객 번호로는 감", sent[-1]["receiver"] == "01011112222")
        n = len(sent)
        notify.dispatch({"device_id": "ccm-x1", "severity": "crit", "detail": "z"}, "발생", extra_receivers=[])
        check("받을 사람이 아무도 없으면 발송 안 함", len(sent) == n)
    finally:
        st.close()
        os.unlink(db)
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print()
    if _fails:
        print(f"❌ {len(_fails)} FAIL: {_fails}")
        return 1
    print("✅ 전체 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
