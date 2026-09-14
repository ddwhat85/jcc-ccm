"""서버 실행 진입점.

    python -m jcc_server --host 0.0.0.0 --port 8000 --db jcc.db

환경변수 JCC_API_KEY를 설정하면 POST /v1/telemetry에 Bearer 인증을 요구한다.
"""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .app import make_server
from .storage import Storage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jcc_server", description="JCC-CCM 수신 서버")
    parser.add_argument("--host", default="0.0.0.0", help="바인드 주소 (기본 0.0.0.0)")
    parser.add_argument("--port", "-p", type=int, default=8000, help="포트 (기본 8000)")
    parser.add_argument("--db", default="jcc.db", help="SQLite 파일 경로 (기본 jcc.db)")
    parser.add_argument("--version", action="version", version=f"jcc-server {__version__}")
    args = parser.parse_args(argv)

    storage = Storage(args.db)
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
