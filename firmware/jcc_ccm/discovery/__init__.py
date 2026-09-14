"""자동 탐색 (Plug & Play).

CCM에 물린 센서를 스스로 찾아 식별해 노드 그래프를 자동 구성하기 위한 패키지.
profiles(지식 베이스) + scanner(버스 탐색·식별)로 이뤄진다.
"""
from .scanner import discover_sim, identify_inventory, scan_modbus, VirtualBus
from . import profiles

__all__ = ["discover_sim", "identify_inventory", "scan_modbus", "VirtualBus", "profiles"]
