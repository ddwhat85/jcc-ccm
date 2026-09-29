"""원격 업데이트(OTA) 테스트 — 서명·번들 위변조·위험한 압축파일·런처 롤백.

    cd firmware && python tests/test_ota.py
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
ROOT = os.path.dirname(FW)
sys.path.insert(0, FW)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from jcc_ccm import ed25519, ota


def _launcher():
    spec = importlib.util.spec_from_file_location("launcher", os.path.join(FW, "scripts", "launcher.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _key(tmp):
    seed = bytes(range(32))
    path = os.path.join(tmp, "k.key")
    with open(path, "w") as fh:
        fh.write(seed.hex())
    return path, ed25519.public_key(seed).hex()


def _tar(members):
    """[(이름, 데이터|None=폴더|'LINK')] → tar.gz 파일 경로."""
    fd, path = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members:
            ti = tarfile.TarInfo(name)
            if data is None:
                ti.type = tarfile.DIRTYPE
                tf.addfile(ti)
            elif data == "LINK":
                ti.type, ti.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tf.addfile(ti)
            else:
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
    return path


# ── 서명 ─────────────────────────────────────────────────
def test_ed25519_rfc8032_vector1():
    sk = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    pk = ed25519.public_key(sk)
    sig = ed25519.sign(sk, b"")
    assert pk.hex() == "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    assert sig.hex() == ("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bac"
                         "c61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    assert ed25519.verify(pk, b"", sig)
    assert not ed25519.verify(pk, b"x", sig)
    assert not ed25519.verify(pk, b"", sig[:-1] + bytes([sig[-1] ^ 1]))
    assert not ed25519.verify(b"\x00" * 31, b"", sig)          # 형식 오류는 예외 없이 False


def test_manifest_signature_and_tampering():
    import ota_build
    tmp = tempfile.mkdtemp()
    try:
        key, pub = _key(tmp)
        m = ota_build.build(key, tmp, version="9.9.9")
        assert ota.verify_manifest(m, pub) is None
        other = ed25519.public_key(b"\x07" * 32).hex()
        assert "서명" in ota.verify_manifest(m, other)                    # 다른 키
        for field, val in (("version", "9.9.10"), ("size", m["size"] + 1), ("sha256", "0" * 64)):
            bad = dict(m, **{field: val})
            assert ota.verify_manifest(bad, pub), field                   # 한 글자만 바꿔도 거부
        assert ota.verify_manifest(dict(m, file="../x.tar.gz"), pub)      # 경로 들어간 파일명
        assert ota.verify_manifest(dict(m, sig="zz"), pub)
    finally:
        shutil.rmtree(tmp)


def test_build_is_deterministic():
    import ota_build
    tmp = tempfile.mkdtemp()
    try:
        key, _ = _key(tmp)
        a = ota_build.build(key, os.path.join(tmp, "a"), version="1.2.3")
        b = ota_build.build(key, os.path.join(tmp, "b"), version="1.2.3")
        assert a["sha256"] == b["sha256"] and a["size"] == b["size"]
    finally:
        shutil.rmtree(tmp)


# ── 위험한 압축 파일 ──────────────────────────────────────
def test_safe_extract_rejects_hostile_archives():
    cases = [
        [("jcc_ccm/__init__.py", b"x"), ("jcc_ccm/../../evil.py", b"x")],
        [("/etc/cron.d/evil", b"x")],
        [("jcc_ccm/__init__.py", b"x"), ("jcc_ccm/link", "LINK")],
        [("jcc_ccm/__init__.py", b"x"), ("other/evil.py", b"x")],
        [("C:/evil.py", b"x")],
    ]
    for members in cases:
        path, dest = _tar(members), tempfile.mkdtemp()
        try:
            try:
                ota.safe_extract(path, dest)
            except ValueError:
                assert os.listdir(dest) == [], "거부하면 아무것도 풀지 않아야 한다"
                continue
            raise AssertionError(f"위험한 압축이 통과됨: {members}")
        finally:
            os.unlink(path)
            shutil.rmtree(dest)


def test_safe_extract_accepts_normal_bundle():
    path, dest = _tar([("jcc_ccm", None), ("jcc_ccm/__init__.py", b'__version__ = "1"'),
                       ("jcc_ccm/sub/a.py", b"A")]), tempfile.mkdtemp()
    try:
        ota.safe_extract(path, dest)
        assert open(os.path.join(dest, "jcc_ccm", "sub", "a.py"), "rb").read() == b"A"
    finally:
        os.unlink(path)
        shutil.rmtree(dest)


def test_selftest_blocks_broken_release():
    """JCC가 서명했더라도 코드가 깨졌으면(문법 오류) 설치·전환하지 않는다 — 자체 점검의 몫."""
    base = tempfile.mkdtemp()
    path = _tar([("jcc_ccm/__init__.py", b'__version__ = "5.0.0"\n'),
                 ("jcc_ccm/agent.py", b"def broken(:\n"),
                 ("jcc_ccm/edge.py", b""), ("jcc_ccm/ota.py", b"")])
    try:
        try:
            ota.stage(base, {"version": "5.0.0"}, path)
        except RuntimeError as exc:
            assert "자체 점검" in str(exc), exc
            assert not os.path.exists(os.path.join(base, "releases", "5.0.0"))
            assert ota.read_current(base) == ""                      # 전환도 안 됨
        else:
            raise AssertionError("깨진 버전이 설치됨")
        # 버전 문자열이 manifest와 다른 번들도 거부
        path2 = _tar([("jcc_ccm/__init__.py", b'__version__ = "4.0.0"\n'),
                      ("jcc_ccm/agent.py", b""), ("jcc_ccm/edge.py", b""), ("jcc_ccm/ota.py", b"")])
        try:
            ota.stage(base, {"version": "5.0.0"}, path2)
        except RuntimeError as exc:
            assert "버전 불일치" in str(exc), exc
        else:
            raise AssertionError("버전 불일치 번들이 설치됨")
        finally:
            os.unlink(path2)
    finally:
        os.unlink(path)
        shutil.rmtree(base)


def test_version_compare():
    assert ota.is_newer("1.10.0", "1.9.9") and not ota.is_newer("1.2", "1.2") and not ota.is_newer("x", "1")


# ── 런처: 시험 부팅·자동 롤백 ─────────────────────────────
def test_launcher_trial_then_rollback_after_repeated_boots():
    L = _launcher()
    st = {"pending": {"version": "2.0.0", "prev": "1.0.0", "boots": 0, "since": 1000.0}}
    cur = "2.0.0"
    actions = []
    for i in range(L.MAX_BOOTS + 1):
        st, cur, act = L.decide(st, cur, 1000.0 + i)
        actions.append(act)
    assert actions == ["trial"] * L.MAX_BOOTS + ["rollback"], actions
    assert cur == "1.0.0" and st["pending"] is None and "2.0.0" in st["bad"]
    assert st["rolled_back"]["version"] == "2.0.0"


def test_launcher_rollback_on_deadline_and_ignores_unrelated_state():
    L = _launcher()
    st = {"pending": {"version": "2.0.0", "prev": "", "boots": 0, "since": 0.0}}
    st2, cur, act = L.decide(st, "2.0.0", L.TRIAL_DEADLINE + 1)
    assert act == "rollback" and cur == "" and "기한" in st2["rolled_back"]["reason"]
    assert L.decide({}, "1.0.0", 5)[2] == "run"
    assert L.decide({"pending": {"version": "3.0.0"}}, "1.0.0", 5)[2] == "run"   # 다른 버전 시험 기록


def test_launcher_prepare_rewrites_current_on_rollback():
    L = _launcher()
    base = tempfile.mkdtemp()
    try:
        for v in ("1.0.0", "2.0.0"):
            os.makedirs(os.path.join(base, "releases", v, "jcc_ccm"))
        ota.write_current(base, "2.0.0")
        ota.save_state(base, {"pending": {"version": "2.0.0", "prev": "1.0.0", "boots": 3, "since": 0}})
        act, rel = L.prepare(base, 10.0)
        assert act == "rollback" and ota.read_current(base) == "1.0.0"
        assert rel == os.path.join(base, "releases", "1.0.0")
        assert "2.0.0" in ota.load_state(base)["bad"]
        # 버전 폴더가 없으면 최초 설치 위치로
        ota.write_current(base, "9.9.9")
        assert L.prepare(base, 11.0)[1] == base
    finally:
        shutil.rmtree(base)


def test_ota_config_validation():
    from jcc_ccm import config as cfgmod
    base = """[device]
id = "c"
[collection]
interval_seconds = 5
[transport]
kind = "http"
[transport.http]
url = "http://x/v1/telemetry"
[[sensors]]
key = "t"
name = "t"
driver = "ambient"
metric = "temp"
"""
    def load(extra):
        fd, p = tempfile.mkstemp(suffix=".toml")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(base + extra)
        try:
            return cfgmod.load(p)
        finally:
            os.unlink(p)
    pub = ed25519.public_key(b"\x01" * 32).hex()
    assert load(f'[ota]\nenabled = true\npublic_key = "{pub}"\n').ota.enabled
    for bad in ('[ota]\nenabled = true\npublic_key = "abc"\n', '[ota]\nenabled = true\n'):
        try:
            load(bad)
        except cfgmod.ConfigError:
            continue
        raise AssertionError("잘못된 공개키가 통과됨")


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
