"""CCM 원격 설정(예지 기준) 테스트 — 검증·적용·거부·저장·재부팅 복원·첫 판정 오류 시 되돌림.

    cd firmware && python tests/test_tuning.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from jcc_ccm.edge import build_edge
from jcc_ccm.predict import params as P
from jcc_ccm.transport import parse_tuning
from test_edge import Rig, _BASE, _PREDICT, _load, _noisy


def _edge(state_dir=None):
    text = _BASE + _PREDICT
    if state_dir:
        path = os.path.join(state_dir, "predict_state.json").replace("\\", "/")
        text = text.replace('state_file = ""', f'state_file = "{path}"')
    return build_edge(_load(text), force_log=True)


def with_(**kv):
    d = P.defaults()
    for k, v in kv.items():
        d[k.replace("__", ".")] = v
    return d


def test_fresh_edge_reports_v0():
    e = _edge()
    assert e.tuning_report() == {"version": 0, "last": None}, e.tuning_report()


def test_apply_valid_config():
    e = _edge()
    out = e.apply_tuning({"version": 3, "params": with_(contact__res_warn=6.0, fire__open_fri=60.0)})
    assert out["status"] == "ok", out
    assert e._pred.contact_cfg.res_warn == 6.0 and e._pred.fire_cfg.open_fri == 60.0
    rep = e.tuning_report()
    assert rep["version"] == 3 and rep["last"] == {"version": 3, "status": "ok"}, rep


def test_reject_out_of_limit_keeps_previous():
    e = _edge()
    e.apply_tuning({"version": 1, "params": with_(contact__res_warn=6.0)})
    out = e.apply_tuning({"version": 2, "params": with_(fire__open_fri=95.0)})
    assert out["status"] == "rejected" and "한계" in out["error"], out
    assert e._pred.fire_cfg.open_fri == 55.0 and e._pred.contact_cfg.res_warn == 6.0
    rep = e.tuning_report()
    assert rep["version"] == 1 and rep["last"]["version"] == 2 and rep["last"]["status"] == "rejected", rep


def test_reject_malformed():
    e = _edge()
    for msg in ({"version": 2, "params": dict(P.defaults(), bogus=1.0)},
                {"version": 2, "params": {"fire.open_fri": 50.0}},
                {"version": "2", "params": P.defaults()},
                {"version": True, "params": P.defaults()},
                {"params": P.defaults()}, "junk", None):
        out = e.apply_tuning(msg)
        assert out["status"] == "rejected", (msg, out)
    assert e._pred.params() == P.defaults() and e.tuning_report()["version"] == 0


def test_saved_after_first_judgment_and_restored_on_reboot():
    d = tempfile.mkdtemp()
    try:
        e = _edge(d)
        e.apply_tuning({"version": 5, "params": with_(dew__rh_high=85.0)})
        f = os.path.join(d, "tuning.json")
        assert not os.path.exists(f), "첫 판정 전엔 저장하지 않는다"
        Rig(e).cycle(_noisy(0))
        saved = json.load(open(f, encoding="utf-8"))
        assert saved["version"] == 5 and saved["params"]["dew.rh_high"] == 85.0
        e2 = _edge(d)
        assert e2.tuning_report()["version"] == 5 and e2._pred.dew_cfg.rh_high == 85.0
    finally:
        shutil.rmtree(d)


def test_bad_saved_file_ignored():
    d = tempfile.mkdtemp()
    try:
        f = os.path.join(d, "tuning.json")
        for content in ("{not json", json.dumps({"version": 9, "params": with_(fire__open_fri=99.0)}),
                        json.dumps([1, 2])):
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(content)
            e = _edge(d)
            assert e.tuning_report()["version"] == 0 and e._pred.params() == P.defaults(), content
    finally:
        shutil.rmtree(d)


def test_revert_if_first_judgment_fails():
    e = _edge()
    e.apply_tuning({"version": 1, "params": with_(contact__res_warn=6.0)})
    Rig(e).cycle(_noisy(0))                           # v1 확정
    e.apply_tuning({"version": 2, "params": with_(contact__res_warn=7.0)})
    real = e._pred.assess_panel
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("가상 판정 오류")
        return real(*a, **k)
    e._pred.assess_panel = boom
    rep = Rig(e).cycle(_noisy(1))                     # 첫 판정 실패 → 되돌리고 옛 설정으로 다시 판정
    assert rep["fire"]["stage"] is not None
    assert e._pred.contact_cfg.res_warn == 6.0
    t = e.tuning_report()
    assert t["version"] == 1 and t["last"]["status"] == "rejected" and "첫 판정" in t["last"]["error"], t


def test_parse_tuning_from_server_response():
    good = {"ok": True, "tuning": {"version": 4, "params": P.defaults()}}
    assert parse_tuning(good) == good["tuning"]
    for bad in ({"ok": True}, {"tuning": "x"}, {"tuning": {"version": 4}}, [], None, {"tuning": {"params": {}}}):
        assert parse_tuning(bad) is None, bad


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); passed += 1; print(f"  PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            import traceback
            print(f"  FAIL {fn.__name__}: {exc}")
            traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
