"""서버 실행 진입점.

    python -m jcc_server --host 0.0.0.0 --port 8000 --db jcc.db

환경변수 JCC_API_KEY를 설정하면 POST /v1/telemetry에 Bearer 인증을 요구한다.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .app import make_server
from .storage import Storage


def main(argv: list[str] | None = None) -> int:
    # 클라우드 호스팅(Render/Railway 등)은 리스닝 포트를 환경변수 PORT로 준다.
    # 환경변수가 있으면 그것을 기본값으로 쓰고, 명령행 인자가 있으면 그게 우선한다.
    env_port = int(os.environ.get("PORT") or os.environ.get("JCC_PORT") or 8000)
    env_host = os.environ.get("HOST") or "0.0.0.0"
    env_db = os.environ.get("JCC_DB") or "jcc.db"
    parser = argparse.ArgumentParser(prog="jcc_server", description="JCC-CCM 수신 서버")
    parser.add_argument("--host", default=env_host, help="바인드 주소 (기본 0.0.0.0, 환경변수 HOST)")
    parser.add_argument("--port", "-p", type=int, default=env_port, help="포트 (기본 8000, 환경변수 PORT)")
    parser.add_argument("--db", default=env_db, help="SQLite 파일 경로 (기본 jcc.db, 환경변수 JCC_DB)")
    parser.add_argument("--version", action="version", version=f"jcc-server {__version__}")
    args = parser.parse_args(argv)

    storage = Storage(args.db)

    # 데모 모드(JCC_DEMO): 배포 서버가 스스로 센서값을 생성해 공유 링크가 살아있게 한다.
    if os.environ.get("JCC_DEMO"):
        from .demo import start as start_demo
        start_demo(storage)
        print("데모 모드 ON — 서버가 시뮬레이션 텔레메트리를 자동 생성합니다")

    httpd = make_server(args.host, args.port, storage)
    print(f"JCC-CCM 서버 시작 → http://{args.host}:{args.port}  (DB: {args.db})")
    print("  수집:      POST /v1/telemetry")
    print("  대시보드:  GET  /")
    print("  Ctrl+C 로 종료")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        httpd.server_close()
        storage.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
