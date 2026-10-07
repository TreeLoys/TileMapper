"""Запуск: python -m tilemapper"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from tilemapper.window import MainWindow, apply_theme

def _icon_path() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "icon.ico"
    return Path(__file__).resolve().parent / "icon.ico"


ICON = _icon_path()


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Разметчик тайлсетов")
    icon = QIcon(str(ICON))
    app.setWindowIcon(icon)
    apply_theme(app)
    window = MainWindow()
    window.setWindowIcon(icon)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
