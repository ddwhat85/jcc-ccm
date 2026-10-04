"""오프라인 보관함 — 순서, 재부팅 뒤 이어 보내기, 깨진 줄, 상한, 평소엔 안 씀.

python tests/test_spool.py   (firmware/ 에서)
"""
from __future__ import annotations

import collections
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_ccm.spool import Spool  # noqa: E402


class _Transport:
    def __init__(self, ok=True):
        self.ok, self.sent = ok, []

    def send(self, p):
        if self.ok:
            self.sent.append(p)
        return self.ok


def _agent(spool_dir, transport):
    from jcc_ccm.agent import Agent
    a = Agent.__new__(Agent)
    a._transport = transport
    a._queue = collections.deque(maxlen=2000)
    a._spool = Spool(spool_dir)
    a._offline = False
    a._stop = False
    return a


def _segs(d):
    return [n for n in os.listdir(d) if n.startswith("seg-")]


def test_order_and_resume_after_reboot():
    d = tempfile.mkdtemp()
    try:
        s = Spool(d, seg_lines=500)
        for i in range(1200):
            s.append({"seq": i})
        assert len(s) == 1200 and len(_segs(d)) == 3
        items = s.peek(50)
        assert [p["seq"] for _, _, p in items] == list(range(50))
        s.commit((items[-1][0], items[-1][1]))
        s2 = Spool(d, seg_lines=500)                 # 재부팅
        assert len(s2) == 1150 and s2.peek(1)[0][2]["seq"] == 50
        while len(s2):
            it = s2.peek(200)
            s2.commit((it[-1][0], it[-1][1]))
        assert len(s2) == 0 and _segs(d) == []
        s2.append({"seq": "new"})                    # 비운 뒤에도 다시 쓴다
        assert len(s2) == 1 and s2.peek(1)[0][2]["seq"] == "new"
    finally:
        shutil.rmtree(d)


def test_broken_last_line_is_skipped():
    d = tempfile.mkdtemp()
    try:
        s = Spool(d)
        s.append({"seq": 1})
        with open(os.path.join(d, _segs(d)[0]), "a", encoding="utf-8") as fh:
            fh.write('{"seq": 2, "readi')               # 쓰다가 전원이 나감
        s = Spool(d)
        s.append({"seq": 3})
        got = [p for _, _, p in s.peek(10)]
        assert got[0] == {"seq": 1} and None in got and {"seq": 3} in got, got
    finally:
        shutil.rmtree(d)


def test_cap_drops_oldest_and_counts():
    d = tempfile.mkdtemp()
    try:
        s = Spool(d, max_bytes=4000, seg_lines=20)
        for i in range(400):
            s.append({"seq": i, "pad": "x" * 30})
        assert sum(os.path.getsize(os.path.join(d, n)) for n in _segs(d)) <= 4000 + 2000
        assert s.dropped > 0 and s.peek(1)[0][2]["seq"] > 0
        assert s.peek(400)[-1][2]["seq"] == 399        # 가장 새것은 남는다
    finally:
        shutil.rmtree(d)


def test_agent_offline_then_reboot_then_sends_in_order():
    d = tempfile.mkdtemp()
    try:
        a = _agent(d, _Transport(ok=False))
        for i in range(5):
            a._enqueue({"seq": i}); a._flush()
        assert len(a._queue) == 0 and len(a._spool) == 5      # 메모리가 아니라 보관함에(전원이 나가도 남게)
        t = _Transport(ok=True)
        b = _agent(d, t)                                       # 재부팅(메모리 큐는 비어 있음)
        b._enqueue({"seq": 99}); b._flush()
        assert [p["seq"] for p in t.sent] == [0, 1, 2, 3, 4, 99], t.sent
        assert len(b._spool) == 0 and b._offline is False
    finally:
        shutil.rmtree(d)


def test_online_writes_nothing_to_disk():
    d = tempfile.mkdtemp()
    try:
        a = _agent(d, _Transport(ok=True))
        for i in range(30):
            a._enqueue({"seq": i}); a._flush()
        assert _segs(d) == [] and len(a._transport.sent) == 30
    finally:
        shutil.rmtree(d)


def test_partial_send_keeps_rest_in_order():
    d = tempfile.mkdtemp()
    try:
        class Flaky(_Transport):
            def __init__(self):
                super().__init__(True)
                self.n = 0

            def send(self, p):
                self.n += 1
                if self.n == 3:
                    return False                               # 셋째에서 다시 끊김
                return super().send(p)
        a = _agent(d, _Transport(ok=False))
        for i in range(5):
            a._enqueue({"seq": i}); a._flush()
        f = Flaky()
        a._transport = f
        a._flush()
        assert [p["seq"] for p in f.sent] == [0, 1] and len(a._spool) == 3
        a._flush()
        assert [p["seq"] for p in f.sent] == [0, 1, 2, 3, 4]
    finally:
        shutil.rmtree(d)


def test_flush_has_time_budget():
    import jcc_ccm.agent as ag
    d = tempfile.mkdtemp()
    old = ag._FLUSH_SECONDS
    try:
        a = _agent(d, _Transport(ok=False))
        for i in range(120):
            a._enqueue({"seq": i}); a._flush()
        class Slow(_Transport):
            def send(self, p):
                import time
                time.sleep(0.01)
                return super().send(p)
        a._transport = Slow(ok=True)
        ag._FLUSH_SECONDS = 0.15
        a._flush()
        n1 = len(a._transport.sent)
        assert 0 < n1 < 120, n1                              # 한 주기에 다 보내지 않고 멈춤(예지 주기를 막지 않게)
        a._enqueue({"seq": "new"})
        assert len(a._queue) == 0                            # 새 묶음은 순서대로 보관함 뒤에
        for _ in range(30):
            a._flush()
        seqs = [p["seq"] for p in a._transport.sent]
        assert seqs == list(range(120)) + ["new"], seqs[-3:]
    finally:
        ag._FLUSH_SECONDS = old
        shutil.rmtree(d)


def test_payload_reports_backlog():
    d = tempfile.mkdtemp()
    try:
        a = _agent(d, _Transport(ok=False))
        for i in range(4):
            a._enqueue({"seq": i}); a._flush()
        from jcc_ccm.config import load  # noqa: F401
        a._cfg = type("C", (), {"device_id": "x", "site": "", "panel": "", "panel_name": ""})()
        p = a._build_payload([])
        assert p["backlog"] == 4 and "boot_ts" in p
    finally:
        shutil.rmtree(d)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); passed += 1; print(f"  PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"  FAIL {fn.__name__}: {exc}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
