"""실행 진입점.

    python -m jcc_ccm --config /opt/jcc-ccm/config.toml

CCM에서는 systemd 서비스가 이 명령을 돌린다(scripts/jcc-ccm.service).
개발 PC에서는 --simulate 로 센서·전송을 흉내 내 로컬에서 루프를 확인할 수 있다.
"""
from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .config import load, ConfigError


def _setup_logging(level: str, file: str) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if file:
        try:
            handlers.append(logging.FileHandler(file))
        except OSError:
            pass  # 파일 못 열어도 콘솔 로그는 살린다
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jcc_ccm", description="JCC-CCM 수집 에이전트")
    parser.add_argument("--config", "-c", default="/opt/jcc-ccm/config.toml",
                        help="설정 파일 경로 (기본: /opt/jcc-ccm/config.toml)")
    parser.add_argument("--simulate", action="store_true",
                        help="센서·전송을 흉내 내 개발 PC에서 루프를 확인 (실기 불필요)")
    parser.add_argument("--version", action="version", version=f"jcc-ccm {__version__}")
    args = parser.parse_args(argv)

    try:
        cfg = load(args.config)
    except ConfigError as exc:
        print(f"[설정 오류] {exc}", file=sys.stderr)
        return 2

    _setup_logging(cfg.log_level, cfg.log_file)

    if args.simulate:
        from .simulate import run_simulation
        return run_simulation(cfg)

    from .agent import Agent
    Agent(cfg).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
