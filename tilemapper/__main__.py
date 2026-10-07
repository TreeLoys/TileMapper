"""Запуск: python -m tilemapper"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from tilemapper.window import MainWindow, apply_theme


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Разметчик тайлсетов")
    apply_theme(app)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
