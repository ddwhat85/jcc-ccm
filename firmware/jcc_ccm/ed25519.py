"""Ed25519 서명 검증(순수 파이썬) — 원격 업데이트(OTA) 번들이 JCC가 서명한 것인지 확인한다.

CCM의 파이썬 표준 라이브러리엔 Ed25519가 없고, 추가 패키지(cryptography)는 현장 장비에
깔기 부담스럽다. 그래서 RFC 8032 §6의 참조 알고리즘을 그대로 옮겼다(테스트가 RFC 공식
시험 벡터로 검증한다).

보안 모델:
  · 서명(sign)은 JCC 사무실의 오프라인 PC에서만 한다(개인키는 서버·CCM에 두지 않는다).
  · CCM은 공개키로 검증(verify)만 한다 → 서버가 뚫려도 가짜 펌웨어를 서명할 수 없다.
  · 이 구현은 상수시간이 아니다. 검증은 공개 데이터라 문제없지만, 서명(sign)은 남이
    시간을 잴 수 없는 오프라인 PC에서만 쓸 것.
"""
from __future__ import annotations

import hashlib

_p = 2 ** 255 - 19
_q = 2 ** 252 + 27742317777372353535851937790883648493


def _inv(x: int) -> int:
    return pow(x, _p - 2, _p)


_d = -121665 * _inv(121666) % _p
_sqrt_m1 = pow(2, (_p - 1) // 4, _p)


def _sha512(b: bytes) -> bytes:
    return hashlib.sha512(b).digest()


def _sha512_modq(b: bytes) -> int:
    return int.from_bytes(_sha512(b), "little") % _q


def _add(P, Q):
    """확장 좌표(X, Y, Z, T) 점 덧셈."""
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % _p
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % _p
    C = 2 * P[3] * Q[3] * _d % _p
    D = 2 * P[2] * Q[2] % _p
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F, G * H, F * G, E * H)


def _mul(s: int, P):
    Q = (0, 1, 1, 0)                # 항등원
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _equal(P, Q) -> bool:
    return ((P[0] * Q[2] - Q[0] * P[2]) % _p == 0 and
            (P[1] * Q[2] - Q[1] * P[2]) % _p == 0)


def _recover_x(y: int, sign: int):
    if y >= _p:
        return None
    x2 = (y * y - 1) * _inv(_d * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_p + 3) // 8, _p)
    if (x * x - x2) % _p != 0:
        x = x * _sqrt_m1 % _p
    if (x * x - x2) % _p != 0:
        return None
    if (x & 1) != sign:
        x = _p - x
    return x


_gy = 4 * _inv(5) % _p
_gx = _recover_x(_gy, 0)
_G = (_gx, _gy, 1, _gx * _gy % _p)


def _compress(P) -> bytes:
    zinv = _inv(P[2])
    x, y = P[0] * zinv % _p, P[1] * zinv % _p
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _p)


def _expand(secret: bytes):
    if len(secret) != 32:
        raise ValueError("개인키는 32바이트여야 합니다")
    h = _sha512(secret)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(secret: bytes) -> bytes:
    a, _ = _expand(secret)
    return _compress(_mul(a, _G))


def sign(secret: bytes, msg: bytes) -> bytes:
    """오프라인 서명 도구 전용(상수시간 아님 — 모듈 설명 참고)."""
    a, prefix = _expand(secret)
    A = _compress(_mul(a, _G))
    r = _sha512_modq(prefix + msg)
    Rs = _compress(_mul(r, _G))
    h = _sha512_modq(Rs + A + msg)
    s = (r + h * a) % _q
    return Rs + int.to_bytes(s, 32, "little")


def verify(public: bytes, msg: bytes, signature: bytes) -> bool:
    """서명이 맞으면 True. 형식이 틀리면 예외 없이 False."""
    if len(public) != 32 or len(signature) != 64:
        return False
    A = _decompress(public)
    if A is None:
        return False
    Rs = signature[:32]
    R = _decompress(Rs)
    if R is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _q:
        return False
    h = _sha512_modq(Rs + public + msg)
    return _equal(_mul(s, _G), _add(R, _mul(h, A)))
