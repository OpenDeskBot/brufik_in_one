"""本地 ROM 烧录与串口工具（需服务端与 PC 串口同机运行）。"""

from deskbot_server.infrastructure.flash.rom_flash import flash_manager

__all__ = ["flash_manager"]
