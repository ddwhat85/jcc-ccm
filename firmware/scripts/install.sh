#!/bin/sh
# JCC-CCM 펌웨어 설치 스크립트 (CCM Debian에서 실행)
#
# 사용법 (CCM에 SSH로 접속한 뒤):
#   scp -P 1522 -r firmware sshu@<device-ip>:/home/temp/jcc-ccm
#   ssh -p 1522 sshu@<device-ip>
#   cd /home/temp/jcc-ccm && sudo sh scripts/install.sh
#
# 매뉴얼 권장대로 사용자 프로그램은 /opt 파티션에 둔다.
set -eu

APP_DIR=/opt/jcc-ccm
SERVICE=/etc/systemd/system/jcc-ccm.service

echo "[1/5] /opt/jcc-ccm 준비"
sudo mkdir -p "$APP_DIR"
sudo cp -r jcc_ccm "$APP_DIR/"

echo "[2/5] 설정 파일"
if [ ! -f "$APP_DIR/config.toml" ]; then
    sudo cp config/config.example.toml "$APP_DIR/config.toml"
    echo "    → $APP_DIR/config.toml 생성됨. 현장 값으로 수정 후 재시작하세요."
else
    echo "    → 기존 config.toml 유지 (덮어쓰지 않음)."
fi

echo "[3/5] 파이썬 의존성 (오프라인이면 건너뜀)"
if command -v pip3 >/dev/null 2>&1; then
    sudo pip3 install -r requirements.txt || \
        echo "    ! pip 실패 — 표준 라이브러리+HTTP 전송만으로도 동작합니다."
else
    echo "    ! pip3 없음 — 표준 라이브러리로 동작. MQTT/Modbus 필요 시 수동 설치."
fi

echo "[4/5] systemd 서비스 등록"
sudo cp scripts/jcc-ccm.service "$SERVICE"
sudo systemctl daemon-reload

echo "[5/5] 서비스 시작"
sudo systemctl enable --now jcc-ccm
echo ""
echo "완료. 상태 확인:  sudo systemctl status jcc-ccm"
echo "로그 보기:        journalctl -u jcc-ccm -f"
