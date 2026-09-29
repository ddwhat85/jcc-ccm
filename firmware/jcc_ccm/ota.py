"""원격 업데이트(OTA) — 서명 확인 → 안전 설치 → 새 버전 자체 점검 → 전환 → 시험 부팅 확정.

설치 구조(/opt/jcc-ccm):
  releases/<버전>/jcc_ccm/   버전별 펌웨어(서로 덮어쓰지 않는다)
  current                    지금 쓰는 버전 이름 한 줄(없거나 비면 최초 설치 위치 /opt/jcc-ccm/jcc_ccm)
  ota/state.json             {"pending": 시험 중인 버전, "bad": 실패한 버전들, "last": 마지막 확정}
  launcher.py                고정 런처 — OTA로 바뀌지 않는다. 시험 부팅이 실패하면 되돌린다.

흐름:
  1) 서버가 텔레메트리 응답에 새 버전 제안(manifest)을 싣는다.
  2) 서명(Ed25519, JCC 오프라인 개인키)을 공개키로 확인 — 서버가 뚫려도 가짜를 못 넣는다.
  3) 번들을 받아 SHA-256·크기 확인 → 안전 압축 해제(절대경로·'..'·링크 거부)
  4) 새 버전을 별도 프로세스로 import해 스스로 점검(버전 문자열까지 일치해야)
  5) state에 pending 기록 → current 교체(원자적) → 재시작 요청(systemd가 런처로 다시 띄움)
  6) 새 버전이 전송에 HEALTHY_SENDS번 성공하면 확정. 기한 안에 못 하면 재시작 →
     런처가 이전 버전으로 되돌리고 그 버전은 'bad'로 기록해 다시 받지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request

from . import __version__, ed25519

log = logging.getLogger("jcc_ccm.ota")

MANIFEST_TAG = "jcc-ccm-ota-v1"
HEALTHY_SENDS = 3          # 새 버전이 이만큼 전송에 성공하면 확정
TRIAL_DEADLINE = 900.0     # 초. 런처(launcher.py)의 기한과 같아야 한다
MAX_BUNDLE = 50 * 1024 * 1024


# ── 버전·서명 ────────────────────────────────────────────
def parse_version(v):
    try:
        parts = tuple(int(x) for x in str(v).strip().split("."))
    except (TypeError, ValueError):
        return None
    return parts if parts and all(p >= 0 for p in parts) else None


def is_newer(a, b) -> bool:
    pa, pb = parse_version(a), parse_version(b)
    return pa is not None and pb is not None and pa > pb


def manifest_message(m: dict) -> bytes:
    """서명 대상 — 이름·버전·해시·크기를 고정 순서로(필드 순서·공백에 흔들리지 않게)."""
    return f"{MANIFEST_TAG}|{m['name']}|{m['version']}|{m['sha256']}|{int(m['size'])}".encode("utf-8")


def verify_manifest(m, public_key_hex: str):
    """문제없으면 None, 있으면 사유 문자열."""
    if not isinstance(m, dict):
        return "manifest 형식 오류"
    for k in ("name", "version", "sha256", "size", "file", "sig"):
        if k not in m:
            return f"manifest에 {k} 없음"
    if m["name"] != "jcc-ccm" or parse_version(m["version"]) is None:
        return "manifest 이름·버전 오류"
    if os.path.basename(str(m["file"])) != m["file"]:
        return "manifest 파일명 오류"
    try:
        pub, sig = bytes.fromhex(public_key_hex), bytes.fromhex(str(m["sig"]))
    except ValueError:
        return "공개키·서명 형식 오류"
    if not ed25519.verify(pub, manifest_message(m), sig):
        return "서명 불일치 — JCC가 서명한 번들이 아님"
    return None


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── 안전 압축 해제 ────────────────────────────────────────
def safe_extract(tar_path: str, dest: str) -> None:
    """jcc_ccm/ 아래 일반 파일·폴더만 푼다. 절대경로·'..'·링크·장치파일이 하나라도 있으면
    아무것도 풀지 않고 예외(부분 설치 방지 — 먼저 전부 검사한 뒤 푼다)."""
    with tarfile.open(tar_path, "r:gz") as tf:
        members = tf.getmembers()
        for m in members:
            parts = m.name.replace("\\", "/").split("/")
            if m.name.startswith(("/", "\\")) or ".." in parts or (len(parts[0]) == 2 and parts[0][1] == ":"):
                raise ValueError(f"위험한 경로: {m.name}")
            if not (m.isfile() or m.isdir()):
                raise ValueError(f"허용되지 않는 항목(링크·장치): {m.name}")
            if parts[0] != "jcc_ccm":
                raise ValueError(f"jcc_ccm/ 밖의 항목: {m.name}")
        for m in members:
            target = os.path.join(dest, *m.name.replace("\\", "/").split("/"))
            if m.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = tf.extractfile(m)
            with open(target, "wb") as out:
                shutil.copyfileobj(src, out)


# ── 설치 상태 ─────────────────────────────────────────────
def _state_path(base: str) -> str:
    return os.path.join(base, "ota", "state.json")


def load_state(base: str) -> dict:
    try:
        with open(_state_path(base), "r", encoding="utf-8") as fh:
            s = json.load(fh)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def save_state(base: str, state: dict) -> None:
    _atomic_write(_state_path(base), json.dumps(state, ensure_ascii=False))


def read_current(base: str) -> str:
    try:
        with open(os.path.join(base, "current"), "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def write_current(base: str, version: str) -> None:
    _atomic_write(os.path.join(base, "current"), version + "\n")


def release_dir(base: str, version: str) -> str:
    return os.path.join(base, "releases", version) if version else base


def selftest(rel_dir: str, expect_version: str, python: str = sys.executable) -> str | None:
    """새 버전을 별도 프로세스로 import해 본다(문법 오류·빠진 모듈·버전 불일치). 문제 없으면 None."""
    env = dict(os.environ, PYTHONPATH=rel_dir, PYTHONUTF8="1")
    code = "import jcc_ccm, jcc_ccm.agent, jcc_ccm.edge, jcc_ccm.ota; print(jcc_ccm.__version__)"
    try:
        r = subprocess.run([python, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"자체 점검 실행 실패: {exc}"
    if r.returncode != 0:
        return "자체 점검 실패: " + (r.stderr.strip().splitlines() or ["?"])[-1][:200]
    got = r.stdout.strip()
    if got != expect_version:
        return f"자체 점검: 버전 불일치({got} ≠ {expect_version})"
    return None


def stage(base: str, manifest: dict, bundle_path: str, python: str = sys.executable) -> str:
    """releases/<버전>에 설치하고 자체 점검까지. 성공 시 설치 경로."""
    v = manifest["version"]
    final = release_dir(base, v)
    tmp = final + ".staging"
    for p in (tmp, final):
        if os.path.isdir(p):
            shutil.rmtree(p)
    os.makedirs(tmp)
    safe_extract(bundle_path, tmp)
    err = selftest(tmp, v, python)
    if err:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(err)
    os.replace(tmp, final)
    return final


def activate(base: str, version: str, now: float) -> None:
    """시험 부팅 준비: pending 기록 → current 교체. (순서 중요: pending 없이 current만 바뀌면
    런처가 실패해도 되돌릴 근거가 없다)"""
    st = load_state(base)
    st["pending"] = {"version": version, "prev": read_current(base), "boots": 0, "since": now}
    save_state(base, st)
    write_current(base, version)


def commit(base: str, version: str, now: float) -> bool:
    st = load_state(base)
    p = st.get("pending")
    if not p or p.get("version") != version:
        return False
    st["pending"] = None
    st["last"] = {"version": version, "committed_at": now}
    save_state(base, st)
    return True


# ── 에이전트용 관리자 ─────────────────────────────────────
class OtaManager:
    def __init__(self, cfg, clock=time.time, python: str = sys.executable,
                 current_version: str = __version__):
        o = cfg.ota
        self.base, self.pub = o.base_dir, o.public_key
        self.api_key = cfg.http.api_key
        u = urllib.parse.urlsplit(cfg.http.url or "")
        self.origin = f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else ""
        self.clock, self.python, self.version = clock, python, current_version
        self.sends = 0
        self.last_error = ""

    def status(self) -> dict:
        st = load_state(self.base)
        p = st.get("pending") or {}
        s = {"fw": self.version}
        if p.get("version") == self.version:
            s["state"] = "trial"
        if st.get("rolled_back"):
            s["rolled_back"] = st["rolled_back"]
        if self.last_error:
            s["error"] = self.last_error
        return s

    def note_send_ok(self) -> None:
        """전송 성공 1회. 시험 중인 새 버전이 충분히 성공하면 확정한다."""
        self.sends += 1
        p = load_state(self.base).get("pending") or {}
        if p.get("version") == self.version and self.sends >= HEALTHY_SENDS:
            if commit(self.base, self.version, self.clock()):
                log.info("OTA 확정: %s — 시험 부팅 통과(전송 %d회 성공)", self.version, self.sends)

    def trial_expired(self) -> bool:
        """시험 기한 안에 확정 못 했으면 True → 에이전트가 재시작해 런처가 되돌리게 한다."""
        p = load_state(self.base).get("pending") or {}
        return (p.get("version") == self.version
                and self.clock() - float(p.get("since", self.clock())) > TRIAL_DEADLINE)

    def _mark_bad(self, version: str, why: str) -> None:
        st = load_state(self.base)
        bad = [b for b in st.get("bad", []) if b != version] + [version]
        st["bad"] = bad[-20:]
        save_state(self.base, st)
        self.last_error = f"{version}: {why}"

    def _download(self, url: str, dest: str, size: int) -> None:
        if url.startswith("/"):
            url = self.origin + url
        if not url.startswith(("http://", "https://")):
            raise ValueError("다운로드 주소 오류")
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        req = urllib.request.Request(url, headers=headers)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        got = 0
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as out:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                got += len(chunk)
                if got > min(size, MAX_BUNDLE):
                    raise ValueError("번들 크기 초과")
                out.write(chunk)

    def handle_offer(self, offer) -> bool:
        """서버 제안을 처리한다. 새 버전으로 전환해 재시작이 필요하면 True."""
        if not isinstance(offer, dict) or not self.pub:
            return False
        v = str(offer.get("version", ""))
        if not is_newer(v, self.version) or v in load_state(self.base).get("bad", []):
            return False
        err = verify_manifest(offer, self.pub)
        if err:
            log.error("OTA 거부(%s): %s", v, err)
            self._mark_bad(v, err)
            return False
        bundle = os.path.join(self.base, "ota", "downloads", offer["file"])
        try:
            self._download(str(offer.get("url") or ""), bundle, int(offer["size"]))
            if os.path.getsize(bundle) != int(offer["size"]) or sha256_file(bundle) != offer["sha256"]:
                self._mark_bad(v, "해시·크기 불일치")
                log.error("OTA 거부(%s): 해시·크기 불일치", v)
                return False
        except Exception as exc:  # noqa: BLE001 - 회선 문제는 다음 제안 때 재시도(bad로 안 찍음)
            self.last_error = f"{v}: 다운로드 실패 {exc}"
            log.warning("OTA 다운로드 실패(%s, 다음에 재시도): %s", v, exc)
            return False
        try:
            stage(self.base, offer, bundle, self.python)
        except Exception as exc:  # noqa: BLE001
            self._mark_bad(v, str(exc))
            log.error("OTA 설치 실패(%s): %s", v, exc)
            return False
        finally:
            try:
                os.remove(bundle)
            except OSError:
                pass
        activate(self.base, v, self.clock())
        log.warning("OTA: %s → %s 전환, 재시작합니다(시험 부팅)", self.version, v)
        return True
