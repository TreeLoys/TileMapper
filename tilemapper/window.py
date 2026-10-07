"""Окно разметчика: список тайлов, инспектор, сетки и превью двух кадров."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QImage,
    QKeySequence,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from tilemapper.canvas import Canvas
from tilemapper.images import IMAGE_FILTER, IMAGE_SUFFIXES, AsepriteError, load_pixmap
from tilemapper.model import (
    GRID_COLORS,
    TILE_FIELDS,
    Document,
    Grid,
    Tile,
    format_prop_value,
    parse_prop_value,
    unique_name,
)
from tilemapper.recent import forget, load_projects, remember

JSON_FILTER = "JSON (*.json)"
RECENT_INLINE = 5


def reveal_targets(document_path: Path | None, image_path: Path | None) -> list[Path]:
    """Файлы, которые стоит показать в проводнике: одна папка — один раз."""
    chosen: list[Path] = []
    folders: set[str] = set()

    def add(path: Path | None) -> None:
        if path is None:
            return
        folder = path if path.is_dir() else path.parent
        try:
            key = str(folder.resolve()).casefold()
        except OSError:
            key = str(folder).casefold()
        if key in folders:
            return
        folders.add(key)
        if path.exists():
            chosen.append(path)
        elif folder.exists():
            chosen.append(folder)

    add(document_path)
    add(image_path)
    return chosen


def available_json_path(image_path: Path) -> Path:
    """Имя для нового JSON: не то, что уже лежит рядом с картинкой."""
    image_path = Path(image_path)
    candidate = image_path.with_suffix(".json")
    index = 2
    while candidate.exists():
        candidate = image_path.with_name(f"{image_path.stem}_{index}.json")
        index += 1
    return candidate


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f7f7f8"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#1a1a1a"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f3f4f6"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#1a1a1a"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#1a1a1a"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#1a73e8"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#1a1a1a"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#6b7280"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#9aa0a6"))
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#9aa0a6")
    )
    app.setPalette(palette)


class AnimPreview(QWidget):
    """Два тайла друг на друге: прозрачность верхнего или мигание кадров."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(200, 140)
        self._pixmap = QPixmap()
        self._tile_a: Tile | None = None
        self._tile_b: Tile | None = None
        self._opacity = 0.5
        self._blinking = False
        self._show_a = True
        self._sequence: list[Tile] = []
        self._sequence_index = 0

    def set_state(
        self,
        pixmap: QPixmap,
        tile_a: Tile | None,
        tile_b: Tile | None,
        opacity: float,
        blinking: bool,
        show_a: bool,
        sequence: list[Tile] | None = None,
        sequence_index: int = 0,
    ) -> None:
        self._pixmap = pixmap
        self._tile_a = tile_a
        self._tile_b = tile_b
        self._opacity = opacity
        self._blinking = blinking
        self._show_a = show_a
        self._sequence = list(sequence or [])
        self._sequence_index = sequence_index
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        self._paint_checker(painter)
        if not self._sequence and self._tile_a is None and self._tile_b is None:
            painter.setPen(QColor("#5f6368"))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Два кадра друг на друге",
            )
            painter.end()
            return
        box_w, box_h = self._box_size()
        avail_w = max(1, self.width() - 16)
        avail_h = max(1, self.height() - 16)
        scale = max(1, min(avail_w // box_w, avail_h // box_h))
        origin_x = (self.width() - box_w * scale) // 2
        origin_y = (self.height() - box_h * scale) // 2
        if self._sequence:
            index = self._sequence_index % len(self._sequence)
            self._blit(painter, self._sequence[index], origin_x, origin_y, scale, 1.0)
        elif self._blinking:
            tile = self._tile_a if self._show_a else self._tile_b
            self._blit(painter, tile, origin_x, origin_y, scale, 1.0)
        else:
            self._blit(painter, self._tile_a, origin_x, origin_y, scale, 1.0)
            self._blit(painter, self._tile_b, origin_x, origin_y, scale, self._opacity)
        painter.end()

    def _box_size(self) -> tuple[int, int]:
        tiles = list(self._sequence) or [tile for tile in (self._tile_a, self._tile_b) if tile is not None]
        if not tiles:
            return 1, 1
        return max(1, max(tile.w for tile in tiles)), max(1, max(tile.h for tile in tiles))

    def _paint_checker(self, painter: QPainter) -> None:
        dark = QColor("#d9d9d9")
        light = QColor("#ffffff")
        step = 8
        for y in range(0, self.height(), step):
            for x in range(0, self.width(), step):
                color = dark if ((x // step) + (y // step)) % 2 == 0 else light
                painter.fillRect(x, y, step, step, color)

    def _blit(
        self,
        painter: QPainter,
        tile: Tile | None,
        origin_x: int,
        origin_y: int,
        scale: int,
        opacity: float,
    ) -> None:
        if tile is None or self._pixmap.isNull() or opacity <= 0:
            return
        bounds = self._pixmap.rect()
        rect = tile_rect(tile).intersected(bounds)
        if rect.isEmpty():
            return
        chunk = self._pixmap.copy(rect)
        scaled = chunk.scaled(
            rect.width() * scale,
            rect.height() * scale,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        local_x = (rect.x() - tile.x) * scale
        local_y = (rect.y() - tile.y) * scale
        painter.setOpacity(opacity)
        painter.drawPixmap(origin_x + local_x, origin_y + local_y, scaled)
        painter.setOpacity(1.0)


def tile_rect(tile: Tile) -> QRect:
    return QRect(tile.x, tile.y, tile.w, tile.h)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.doc = Document()
        self._pixmap = QPixmap()
        self._selected_id = -1
        self._dirty = False
        self._loading_ui = False
        self._last_cell = (32, 32)
        self._cursor: tuple[int, int] | None = None
        self._hint = ""
        self._zoom_text = "100%"
        self._anim_ids = {"a": -1, "b": -1}
        self._blink_show_a = True
        self._recording_anim = False
        self._anim_draft: list[int] = []
        self._sequence_ids: list[int] = []
        self._sequence_index = 0
        self._rebuild_queued = False
        self._tool_actions: dict[str, QAction] = {}
        self._recent_slots: list[QAction] = []
        self._reload_tries = 0
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_watched_file)
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.timeout.connect(self._reload_image_from_disk)

        self.canvas = Canvas()
        self._build_ui()
        self._connect()
        self._apply_document(fit=False)
        self._update_title()
        self.resize(1280, 820)
        self.setAcceptDrops(True)

    def _build_ui(self) -> None:
        self._build_toolbar()
        self._build_file_menu()
        self._build_view_menu()
        self.tile_list = QListWidget()
        self.tile_list.setMinimumHeight(120)
        self.tile_list.setMaximumHeight(220)
        self.tiles_box = QGroupBox("Тайлы (0)")
        tiles_layout = QVBoxLayout(self.tiles_box)
        tiles_layout.addWidget(self.tile_list)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("имя")
        self.id_spin = self._spin(0, 1_000_000)
        self.x_spin = self._spin(-100_000, 100_000)
        self.y_spin = self._spin(-100_000, 100_000)
        self.w_spin = self._spin(1, 100_000)
        self.h_spin = self._spin(1, 100_000)
        self.props = QTableWidget(0, 2)
        self.props.setHorizontalHeaderLabels(["поле", "значение"])
        self.props.horizontalHeader().setStretchLastSection(True)
        self.props.setMinimumHeight(120)
        add_prop = QPushButton("Добавить поле")
        del_prop = QPushButton("Удалить поле")
        add_prop.clicked.connect(self._add_prop_row)
        del_prop.clicked.connect(self._delete_prop_row)
        prop_buttons = QHBoxLayout()
        prop_buttons.addWidget(add_prop)
        prop_buttons.addWidget(del_prop)

        inspector = QGroupBox("Свойства")
        self._inspector = inspector
        form = QFormLayout(inspector)
        form.addRow("Имя", self.name_edit)
        form.addRow("id", self.id_spin)
        form.addRow("x", self.x_spin)
        form.addRow("y", self.y_spin)
        form.addRow("w", self.w_spin)
        form.addRow("h", self.h_spin)
        form.addRow("Кастом", self.props)
        form.addRow(prop_buttons)
        self._inspector_widgets = [
            self.name_edit,
            self.id_spin,
            self.x_spin,
            self.y_spin,
            self.w_spin,
            self.h_spin,
            self.props,
            add_prop,
            del_prop,
        ]

        self.grid_list = QListWidget()
        self.grid_list.setMinimumHeight(72)
        self.grid_list.setMaximumHeight(140)
        self.grid_name = QLineEdit()
        self.grid_x = self._spin(-100_000, 100_000)
        self.grid_y = self._spin(-100_000, 100_000)
        self.grid_cw = self._spin(1, 4096)
        self.grid_ch = self._spin(1, 4096)
        self.grid_cols = self._spin(1, 10_000)
        self.grid_rows = self._spin(1, 10_000)
        self.grid_color_btn = QPushButton("Цвет")
        self.grid_delete_btn = QPushButton("Удалить сетку")
        self.grid_color_btn.clicked.connect(self._pick_grid_color)
        self.grid_delete_btn.clicked.connect(self._delete_grid)
        grids = QGroupBox("Сетки")
        self._grids_box = grids
        grid_form = QFormLayout(grids)
        grid_form.addRow(self.grid_list)
        grid_form.addRow("Имя", self.grid_name)
        grid_form.addRow("Начало x", self.grid_x)
        grid_form.addRow("Начало y", self.grid_y)
        grid_form.addRow("Ширина ячейки", self.grid_cw)
        grid_form.addRow("Высота ячейки", self.grid_ch)
        grid_form.addRow("Колонки", self.grid_cols)
        grid_form.addRow("Ряды", self.grid_rows)
        grid_form.addRow(self.grid_color_btn)
        grid_form.addRow(self.grid_delete_btn)
        hint = QLabel("Инструмент «Сетка»: протяни область на картинке. Галка в списке прячет сетку.")
        hint.setWordWrap(True)
        grid_form.addRow(hint)
        self._grid_editors = [
            self.grid_name,
            self.grid_x,
            self.grid_y,
            self.grid_cw,
            self.grid_ch,
            self.grid_cols,
            self.grid_rows,
            self.grid_color_btn,
            self.grid_delete_btn,
        ]

        self.layer_list = QListWidget()
        self.layer_list.setMinimumHeight(72)
        self.layer_list.setMaximumHeight(120)
        self.layer_name = QLineEdit()
        self.layer_name.setPlaceholderText("имя набора")
        add_layer = QPushButton("Новый набор")
        del_layer = QPushButton("Удалить набор")
        add_layer.clicked.connect(self._add_layer)
        del_layer.clicked.connect(self._delete_layer)
        layer_buttons = QHBoxLayout()
        layer_buttons.addWidget(add_layer)
        layer_buttons.addWidget(del_layer)
        layers = QGroupBox("Наборы")
        layer_form = QFormLayout(layers)
        layer_form.addRow(self.layer_list)
        layer_form.addRow("Имя", self.layer_name)
        layer_form.addRow(layer_buttons)
        layer_hint = QLabel("Одна картинка, несколько наборов по смыслу. Галка скрывает набор.")
        layer_hint.setWordWrap(True)
        layer_form.addRow(layer_hint)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(8, 8, 8, 8)
        self._anim_box = self._build_anim_panel()
        side_layout.addWidget(layers)
        side_layout.addWidget(grids)
        side_layout.addWidget(self.tiles_box)
        side_layout.addWidget(inspector)
        side_layout.addWidget(self._anim_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(side)
        scroll.setMinimumWidth(320)
        self._side_scroll = scroll

        splitter = QSplitter()
        splitter.addWidget(self.canvas)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([900, 340])

        anim = self._build_anim()
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter, 1)
        layout.addWidget(anim)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())

    def _build_toolbar(self) -> None:
        bar = QToolBar()
        bar.setMovable(False)
        self.addToolBar(bar)

        self.act_open_image = QAction("Открыть картинку", self)
        self.act_open_image.setShortcut(QKeySequence("Ctrl+Shift+O"))
        self.act_open_image.setToolTip("Открыть картинку как новый документ")
        self.act_open_image.triggered.connect(self._pick_image)
        self.act_open_json = QAction("Открыть json", self)
        self.act_open_json.setShortcut(QKeySequence("Ctrl+O"))
        self.act_open_json.triggered.connect(self._pick_json)
        self.act_save = QAction("Сохранить", self)
        self.act_save.setShortcut(QKeySequence("Ctrl+S"))
        self.act_save.setToolTip("Рабочий файл: тайлы и временные сетки")
        self.act_save.triggered.connect(lambda: self.save_document())
        self.act_save_as = QAction("Сохранить как", self)
        self.act_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.act_save_as.triggered.connect(self.save_document_as)
        self.act_export = QAction("Экспорт чистый", self)
        self.act_export.setShortcut(QKeySequence("Ctrl+E"))
        self.act_export.setToolTip("JSON без блока editor — то, что ест игра")
        self.act_export.triggered.connect(lambda: self.export_document())
        self.file_button = QToolButton()
        self.file_button.setText("Файл")
        self.file_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._file_menu = QMenu(self.file_button)
        self.file_button.setMenu(self._file_menu)
        self.view_button = QToolButton()
        self.view_button.setText("Вид")
        self.view_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._view_menu = QMenu(self.view_button)
        self.view_button.setMenu(self._view_menu)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Массив", "Словарь"])
        self.mode_combo.setFixedWidth(110)
        self.mode_combo.setToolTip("Словарь: имя тайла — уникальный ключ")

        group = QActionGroup(self)
        group.setExclusive(True)
        tools = (
            ("select", "Выбор", "Клик, перетаскивание. Стрелки — 1 px, Shift — 8. Ctrl+D дубль, Delete удалить. Esc снимает выделение, пустое имя удаляет тайл. Клавиша 1"),
            ("rect", "Прямоугольник", "Протяни произвольный кусок. Клавиша 2"),
            ("grid", "Сетка", "Протяни область, потом задай ширину и высоту ячейки. Клавиша 3"),
            ("cell", "Ячейка", "Клик — тайл и имя, протяг — пачка prefix_N, Enter — следующая ячейка. Клавиша 4"),
        )
        for name, text, tip in tools:
            action = QAction(text, self)
            action.setCheckable(True)
            action.setToolTip(tip)
            action.setData(name)
            group.addAction(action)
            self._tool_actions[name] = action
        self._tool_actions["select"].setChecked(True)
        group.triggered.connect(self._on_tool_action)

        self.snap_action = QAction("Прилипание", self)
        self.snap_action.setCheckable(True)
        self.snap_action.setToolTip("Прилипать к активной сетке. Клавиша G")
        actual = QAction("1:1", self)
        actual.setToolTip("Один пиксель картинки к одному пикселю. Ctrl+1")
        actual.triggered.connect(self.canvas.zoom_actual)
        fit = QAction("Вписать", self)
        fit.setToolTip("Вписать картинку. Клавиша F")
        fit.triggered.connect(self.canvas.zoom_fit)
        self.prefix_edit = QLineEdit("tile")
        self.prefix_edit.setFixedWidth(90)
        self.prefix_edit.setToolTip("Имена пачки ячеек: prefix_0, prefix_1, …")

        bar.addWidget(self.file_button)
        bar.addWidget(self.view_button)
        bar.addSeparator()
        bar.addWidget(self.mode_combo)
        bar.addSeparator()
        bar.addAction(self._tool_actions["select"])
        bar.addAction(self._tool_actions["rect"])
        bar.addSeparator()
        bar.addAction(self._tool_actions["grid"])
        bar.addAction(self._tool_actions["cell"])
        bar.addSeparator()
        bar.addAction(self.snap_action)
        bar.addAction(actual)
        bar.addAction(fit)
        bar.addSeparator()
        bar.addWidget(QLabel(" Префикс "))
        bar.addWidget(self.prefix_edit)

    def _build_file_menu(self) -> None:
        menu = self._file_menu
        self._last_project_action = QAction("Нет проектов", self)
        self._last_project_action.setEnabled(False)
        self._last_project_action.triggered.connect(self._open_last_project)
        menu.addAction(self._last_project_action)
        menu.addSeparator()
        menu.addAction(self.act_open_image)
        menu.addAction(self.act_open_json)
        menu.addSeparator()
        menu.addAction(self.act_save)
        menu.addAction(self.act_save_as)
        menu.addAction(self.act_export)
        self.act_reveal = QAction("Показать в папке", self)
        self.act_reveal.setToolTip("Открыть проводник на картинке и JSON")
        self.act_reveal.triggered.connect(self._reveal_in_folder)
        menu.addAction(self.act_reveal)
        menu.addSeparator()
        self._all_recent_menu = menu.addMenu("Все недавние")
        self._all_recent_menu.aboutToShow.connect(self._fill_all_recent)
        forget_action = menu.addAction("Убрать из недавних…")
        forget_action.triggered.connect(self._manage_recent)
        for action in (
            self.act_open_image,
            self.act_open_json,
            self.act_save,
            self.act_save_as,
            self.act_export,
        ):
            self.addAction(action)
        menu.aboutToShow.connect(self._fill_file_recent)

    def _build_view_menu(self) -> None:
        menu = self._view_menu
        tiles_header = menu.addAction("Прямоугольники")
        tiles_header.setEnabled(False)
        self._tile_label_actions: dict[str, QAction] = {}
        tile_group = QActionGroup(self)
        tile_group.setExclusive(True)
        for key, text in (("none", "Без подписей"), ("id", "id"), ("name", "Имена")):
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setData(key)
            tile_group.addAction(action)
            self._tile_label_actions[key] = action
        self._tile_label_actions["none"].setChecked(True)
        tile_group.triggered.connect(self._on_tile_labels)
        menu.addSeparator()
        grids_header = menu.addAction("Сетки")
        grids_header.setEnabled(False)
        self._grid_label_actions: dict[str, QAction] = {}
        grid_group = QActionGroup(self)
        grid_group.setExclusive(True)
        for key, text in (("none", "Без подписи"), ("name", "Имя")):
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setData(key)
            grid_group.addAction(action)
            self._grid_label_actions[key] = action
        self._grid_label_actions["none"].setChecked(True)
        grid_group.triggered.connect(self._on_grid_labels)

    def _on_tile_labels(self, action: QAction) -> None:
        if self._loading_ui:
            return
        self.doc.tile_labels = str(action.data())
        self.canvas.set_label_mode(self.doc.tile_labels, self.doc.grid_labels)
        self._schedule_rebuild()

    def _on_grid_labels(self, action: QAction) -> None:
        if self._loading_ui:
            return
        self.doc.grid_labels = str(action.data())
        self.canvas.set_label_mode(self.doc.tile_labels, self.doc.grid_labels)
        self._schedule_rebuild()

    def _sync_view_menu(self) -> None:
        tile = self._tile_label_actions.get(self.doc.tile_labels, self._tile_label_actions["none"])
        grid = self._grid_label_actions.get(self.doc.grid_labels, self._grid_label_actions["none"])
        self._loading_ui = True
        tile.setChecked(True)
        grid.setChecked(True)
        self._loading_ui = False
        self.canvas.set_label_mode(self.doc.tile_labels, self.doc.grid_labels)

    def _build_anim(self) -> QGroupBox:
        box = QGroupBox("Анимация")
        self.anim_a = QComboBox()
        self.anim_b = QComboBox()
        to_a = QPushButton("В кадр A")
        to_b = QPushButton("В кадр B")
        to_a.setToolTip("Положить выбранный тайл в нижний кадр")
        to_b.setToolTip("Положить выбранный тайл в верхний кадр")
        to_a.clicked.connect(lambda: self._assign_anim("a"))
        to_b.clicked.connect(lambda: self._assign_anim("b"))
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(50)
        self.blink_btn = QPushButton("Мигать")
        self.blink_btn.setCheckable(True)
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 30)
        self.fps_spin.setValue(8)
        self.fps_spin.setSuffix(" fps")
        self.preview = AnimPreview()
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._on_blink_tick)

        controls = QFormLayout()
        row_a = QHBoxLayout()
        row_a.addWidget(self.anim_a, 1)
        row_a.addWidget(to_a)
        row_b = QHBoxLayout()
        row_b.addWidget(self.anim_b, 1)
        row_b.addWidget(to_b)
        controls.addRow("Кадр A", row_a)
        controls.addRow("Кадр B", row_b)
        controls.addRow("Прозрачность B", self.opacity_slider)
        blink_row = QHBoxLayout()
        blink_row.addWidget(self.blink_btn)
        blink_row.addWidget(self.fps_spin)
        controls.addRow(blink_row)
        record_row = QHBoxLayout()
        self.anim_record_btn = QPushButton("Набрать")
        self.anim_record_btn.setCheckable(True)
        self.anim_record_btn.setToolTip("Кликай тайлы на холсте в порядке кадров")
        self.anim_commit_btn = QPushButton("Готово")
        self.anim_commit_btn.setToolTip("Зациклить набранный порядок и записать его в кастом")
        self.anim_clear_btn = QPushButton("Сброс")
        record_row.addWidget(self.anim_record_btn)
        record_row.addWidget(self.anim_commit_btn)
        record_row.addWidget(self.anim_clear_btn)
        controls.addRow(record_row)
        self.anim_draft_label = QLabel("Кликни тайлы по порядку")
        self.anim_draft_label.setWordWrap(True)
        controls.addRow(self.anim_draft_label)
        wrap = QWidget()
        wrap.setLayout(controls)
        wrap.setMinimumWidth(280)
        layout = QHBoxLayout(box)
        layout.addWidget(wrap)
        layout.addWidget(self.preview, 1)
        return box

    def _build_anim_panel(self) -> QGroupBox:
        box = QGroupBox("Анимации")
        box.setVisible(False)
        self.anim_list = QListWidget()
        self.anim_list.setMaximumHeight(120)
        new_btn = QPushButton("Новая")
        del_btn = QPushButton("Удалить")
        new_btn.setToolTip("Набрать другую анимацию кликами по тайлам")
        del_btn.setToolTip("Снять animation_name и animation_id с тайлов этой анимации")
        new_btn.clicked.connect(self._start_animation_recording)
        del_btn.clicked.connect(self._delete_selected_animation)
        self.anim_list.currentRowChanged.connect(self._on_anim_list_row)
        buttons = QHBoxLayout()
        buttons.addWidget(new_btn)
        buttons.addWidget(del_btn)
        layout = QVBoxLayout(box)
        layout.addWidget(self.anim_list)
        layout.addLayout(buttons)
        return box

    def _spin(self, low: int, high: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setKeyboardTracking(False)
        return spin

    def _connect(self) -> None:
        self.canvas.rect_ready.connect(self._on_rect)
        self.canvas.grid_ready.connect(self._on_grid)
        self.canvas.cells_ready.connect(self._on_cells)
        self.canvas.selection_changed.connect(self._on_canvas_selection)
        self.canvas.background_picked.connect(self._on_background_tile)
        self.canvas.tile_moved.connect(self._on_tile_moved)
        self.canvas.cursor_moved.connect(self._on_cursor)
        self.canvas.zoom_changed.connect(self._on_zoom)
        self.canvas.hint.connect(self._on_hint)
        self.canvas.tool_hotkey.connect(self.set_tool)
        self.canvas.snap_hotkey.connect(self.snap_action.toggle)
        self.canvas.fit_hotkey.connect(self.canvas.zoom_fit)
        self.canvas.duplicate_pressed.connect(self._duplicate)
        self.canvas.delete_pressed.connect(self._delete_selected)
        self.canvas.nudge_pressed.connect(self._nudge)
        self.snap_action.toggled.connect(self.canvas.set_snap)

        self.tile_list.currentRowChanged.connect(self._on_tile_row)
        delete_list = QAction(self.tile_list)
        delete_list.setShortcut(QKeySequence(Qt.Key.Key_Delete))
        delete_list.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        delete_list.triggered.connect(self._delete_selected)
        self.tile_list.addAction(delete_list)

        self.name_edit.textChanged.connect(self._on_name_changed)
        self.name_edit.returnPressed.connect(self._on_name_enter)
        self.id_spin.valueChanged.connect(self._on_id_changed)
        for spin in (self.x_spin, self.y_spin, self.w_spin, self.h_spin):
            spin.valueChanged.connect(self._on_geom_changed)
        self.props.itemChanged.connect(self._on_props_changed)
        self.layer_list.currentRowChanged.connect(self._on_layer_row)
        self.layer_list.itemChanged.connect(self._on_layer_item_changed)
        self.layer_name.textChanged.connect(self._on_layer_name)
        self.layer_name.returnPressed.connect(self.canvas.setFocus)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        self.grid_list.currentRowChanged.connect(self._on_grid_row)
        self.grid_list.itemClicked.connect(lambda _item: self._jump_to_active_grid())
        self.grid_list.itemChanged.connect(self._on_grid_item_changed)
        self.grid_name.textChanged.connect(self._on_grid_name)
        self.grid_x.valueChanged.connect(self._on_grid_origin)
        self.grid_y.valueChanged.connect(self._on_grid_origin)
        self.grid_cw.valueChanged.connect(self._on_grid_cell)
        self.grid_ch.valueChanged.connect(self._on_grid_cell)
        self.grid_cols.valueChanged.connect(self._on_grid_span)
        self.grid_rows.valueChanged.connect(self._on_grid_span)

        self.anim_a.currentIndexChanged.connect(lambda index: self._on_anim_combo("a", index))
        self.anim_b.currentIndexChanged.connect(lambda index: self._on_anim_combo("b", index))
        self.opacity_slider.valueChanged.connect(lambda _value: self._update_preview())
        self.blink_btn.toggled.connect(self._on_blink_toggled)
        self.fps_spin.valueChanged.connect(lambda _value: self._restart_blink())
        self.anim_record_btn.toggled.connect(self._on_record_toggled)
        self.anim_commit_btn.clicked.connect(self._commit_animation)
        self.anim_clear_btn.clicked.connect(self._clear_anim_draft)

    def set_tool(self, name: str) -> None:
        action = self._tool_actions.get(name)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        self.canvas.set_tool(name)
        self.canvas.setFocus()

    def open_image(self, path: Path, *, confirm: bool = True) -> bool:
        path = Path(path)
        if confirm and not self._confirm_replace("Открыть картинку как новый документ?"):
            return False
        if not path.is_file():
            QMessageBox.warning(self, "Картинка", f"Файл не найден:\n{path}")
            return False
        try:
            pixmap, note = load_pixmap(path)
        except AsepriteError as exc:
            QMessageBox.warning(self, "Картинка", str(exc))
            return False
        if pixmap.isNull():
            QMessageBox.warning(self, "Картинка", f"Не удалось прочитать:\n{path}")
            return False
        self._pixmap = pixmap
        self.doc = Document.from_image(path)
        self._arm_watch(path)
        self._reset_session()
        self._apply_document(fit=True)
        self._dirty = False
        self._update_title()
        notes = []
        sibling = path.with_suffix(".json")
        if sibling.is_file():
            notes.append(f"Рядом уже есть {sibling.name}. Сохранение предложит {available_json_path(path).name}")
        if note:
            notes.append(note)
        if notes:
            self.statusBar().showMessage("  |  ".join(notes), 6000)
        return True

    def open_document(self, path: Path, *, confirm: bool = True) -> bool:
        path = Path(path)
        if confirm and not self._confirm_discard():
            return False
        try:
            document = Document.load(path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            QMessageBox.warning(self, "JSON", str(exc))
            return False
        self.doc = document
        remember(path)
        image_error = False
        try:
            self._load_pixmap(document.image_path)
        except AsepriteError as exc:
            image_error = True
            QMessageBox.warning(self, "Картинка", str(exc))
        if document.image_path is not None and not document.image_path.is_file():
            QMessageBox.warning(
                self,
                "Картинка",
                f"Рядом с JSON не найдена картинка:\n{document.image_path}",
            )
        elif document.image_path is not None and self._pixmap.isNull() and not image_error:
            QMessageBox.warning(
                self,
                "Картинка",
                f"Не удалось прочитать:\n{document.image_path}",
            )
        self._reset_session()
        self._apply_document(fit=document.zoom is None)
        if document.zoom:
            self.canvas.zoom_set(document.zoom)
        self._dirty = False
        self._update_title()
        return True

    def save_document(self, path: Path | None = None) -> bool:
        target = Path(path) if path is not None else self.doc.path
        if target is None:
            return self.save_document_as()
        self.doc.zoom = round(self.canvas.current_zoom(), 4)
        try:
            self.doc.save(target, include_editor=True)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Сохранение", str(exc))
            return False
        self._dirty = False
        self._update_title()
        remember(target)
        self.statusBar().showMessage(f"Сохранено: {target}", 4000)
        return True

    def save_document_as(self) -> bool:
        suggest = self.doc.path
        if suggest is None and self.doc.image_path is not None:
            suggest = available_json_path(self.doc.image_path)
        chosen, _filter = QFileDialog.getSaveFileName(
            self, "Сохранить", str(suggest or ""), JSON_FILTER
        )
        if not chosen:
            return False
        return self.save_document(Path(chosen))

    def export_document(self, path: Path | None = None) -> bool:
        if path is None:
            if self.doc.path is not None:
                suggest = self.doc.path.with_name(self.doc.path.stem + ".clean.json")
            elif self.doc.image_path is not None:
                suggest = self.doc.image_path.with_suffix(".clean.json")
            else:
                suggest = Path("tileset.clean.json")
            chosen, _filter = QFileDialog.getSaveFileName(
                self, "Экспорт чистого JSON", str(suggest), JSON_FILTER
            )
            if not chosen:
                return False
            path = Path(chosen)
        path = Path(path)
        if self.doc.path is not None and path.resolve() == self.doc.path.resolve():
            answer = QMessageBox.question(
                self,
                "Экспорт",
                "Этот файл — рабочий. Экспорт запишет его без сеток. Продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self.doc.zoom = round(self.canvas.current_zoom(), 4)
        try:
            self.doc.save(path, include_editor=False)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Экспорт", str(exc))
            return False
        if self.doc.path is not None and path.resolve() == self.doc.path.resolve():
            self._dirty = True
            self._update_title()
        self.statusBar().showMessage(f"Экспорт: {path}", 4000)
        return True

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self._escape_selection():
            event.accept()
            return
        super().keyPressEvent(event)

    def _escape_selection(self) -> bool:
        tile = self._selected_tile()
        if tile is None:
            return False
        if not tile.name.strip():
            self._delete_selected(force=True)
        else:
            self._select(-1)
        self.canvas.setFocus()
        return True

    def closeEvent(self, event) -> None:
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            suffix = path.suffix.lower()
            if suffix == ".json":
                self.open_document(path)
                return
            if suffix in IMAGE_SUFFIXES:
                self.open_image(path)
                return

    def _pick_image(self) -> None:
        start = str(self.doc.image_path or "")
        chosen, _filter = QFileDialog.getOpenFileName(self, "Картинка", start, IMAGE_FILTER)
        if chosen:
            self.open_image(Path(chosen))

    def _pick_json(self) -> None:
        start = str(self.doc.path or self.doc.image_path or "")
        chosen, _filter = QFileDialog.getOpenFileName(self, "JSON", start, JSON_FILTER)
        if chosen:
            self.open_document(Path(chosen))

    def _reveal_in_folder(self) -> None:
        targets = reveal_targets(self.doc.path, self.doc.image_path)
        if not targets:
            self.statusBar().showMessage("Нет открытой картинки или JSON", 3000)
            return
        for target in targets:
            if target.is_dir():
                subprocess.Popen(["explorer", str(target)])
            else:
                subprocess.Popen(["explorer", f"/select,{target}"])

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Разметчик тайлсетов")
        box.setText("Есть несохранённые изменения. Сохранить?")
        save_btn = box.addButton("Сохранить", QMessageBox.ButtonRole.AcceptRole)
        discard_btn = box.addButton("Не сохранять", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save_btn:
            return self.save_document()
        return clicked is discard_btn

    def _confirm_replace(self, text: str) -> bool:
        has_work = bool(self.doc.tiles or self.doc.grids or self.doc.path or self._dirty)
        if not has_work:
            return True
        if self._dirty:
            return self._confirm_discard()
        answer = QMessageBox.question(
            self,
            "Новый документ",
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _reset_session(self) -> None:
        self._selected_id = -1
        self._anim_ids = {"a": -1, "b": -1}
        self._sequence_ids = []
        self._sequence_index = 0
        self._clear_anim_draft()
        self.blink_btn.setChecked(False)

    def _load_pixmap(self, path: Path | None) -> None:
        self._pixmap = QPixmap()
        self._arm_watch(None)
        if path is None or not path.is_file():
            return
        pixmap, note = load_pixmap(path)
        self._pixmap = pixmap
        self._arm_watch(path)
        if note:
            self.statusBar().showMessage(note, 5000)

    def _arm_watch(self, path: Path | None) -> None:
        current = self._watcher.files()
        if current:
            self._watcher.removePaths(current)
        if path is not None and path.is_file():
            self._watcher.addPath(str(path))

    def _on_watched_file(self, _path: str) -> None:
        self._reload_tries = 0
        self._reload_timer.start(400)

    def _reload_image_from_disk(self) -> None:
        path = self.doc.image_path
        if path is None:
            return
        if not path.is_file():
            self._reload_tries += 1
            if self._reload_tries < 8:
                self._reload_timer.start(300)
            return
        try:
            pixmap, note = load_pixmap(path)
        except AsepriteError:
            self._arm_watch(path)
            self.statusBar().showMessage("Не удалось перечитать картинку", 3000)
            return
        if pixmap.isNull():
            self._reload_tries += 1
            if self._reload_tries < 8:
                self._reload_timer.start(300)
            return
        self._pixmap = pixmap
        self._arm_watch(path)
        self._push_canvas()
        self._update_preview()
        self.statusBar().showMessage(note or "Картинка обновлена", 3000)

    def _apply_document(self, *, fit: bool) -> None:
        self._loading_ui = True
        self.mode_combo.setCurrentIndex(1 if self.doc.mode == "dict" else 0)
        self._loading_ui = False
        self._reload_layer_list(select=self.doc.active_index)
        self._reload_tile_list()
        select = 0 if self.doc.grids else -1
        self._reload_grid_list(select=select)
        self._reload_anim_combos()
        self._refresh_anim_panel()
        self._fill_inspector()
        self._sync_view_menu()
        self._push_canvas()
        if fit:
            QTimer.singleShot(0, self.canvas.zoom_fit)
        self._update_status()

    def _schedule_rebuild(self) -> None:
        if self._rebuild_queued:
            return
        self._rebuild_queued = True
        QTimer.singleShot(0, self._rebuild_now)

    def _rebuild_now(self) -> None:
        self._rebuild_queued = False
        self._push_canvas()

    def _push_canvas(self) -> None:
        tiles = self.doc.tiles if self.doc.active_set().visible else []
        self.canvas.set_world(
            self._pixmap,
            tiles,
            self.doc.grids,
            self._selected_id,
            self.grid_list.currentRow(),
            self.doc.background_tiles(),
        )

    def _touch(self) -> None:
        self._dirty = True
        self._update_title()
        self._update_status()

    def _update_title(self) -> None:
        if self.doc.path is not None:
            name = self.doc.path.name
        elif self.doc.image_path is not None:
            name = self.doc.image_path.name
        else:
            name = "без имени"
        star = "*" if self._dirty else ""
        self.setWindowTitle(f"{star}{name} — Разметчик тайлсетов")

    def _update_status(self) -> None:
        image = ""
        if self.doc.image_path is not None:
            image = self.doc.image_path.name
        elif self.doc.image:
            image = self.doc.image
        parts = []
        if image:
            parts.append(image)
        parts.append(f"зум {self._zoom_text}")
        parts.append(self.doc.nametileset or "набор")
        parts.append(f"тайлов {len(self.doc.tiles)}")
        if self._cursor is not None:
            parts.append(f"курсор {self._cursor[0]}, {self._cursor[1]}")
        if self._hint:
            parts.append(self._hint)
        tile = self._selected_tile()
        if tile is not None:
            label = tile.name or "без имени"
            parts.append(f"выбран {tile.id}: {label}")
        self.statusBar().showMessage("  |  ".join(parts))

    def _selected_tile(self) -> Tile | None:
        if self._selected_id < 0:
            return None
        return self.doc.find(self._selected_id)

    def _active_grid(self) -> Grid | None:
        row = self.grid_list.currentRow()
        if row < 0 or row >= len(self.doc.grids):
            return None
        return self.doc.grids[row]

    def _tile_label(self, tile: Tile) -> str:
        name = tile.name or "—"
        return f"{tile.id}: {name}  {tile.w}×{tile.h}"

    def _tile_choice_label(self, tile: Tile) -> str:
        name = tile.name or "—"
        return f"{tile.id}: {name}"

    def _grid_label(self, grid: Grid) -> str:
        return f"{grid.name}  {grid.cell_w}×{grid.cell_h}  {grid.cols}×{grid.rows}"

    def _on_tool_action(self, action: QAction) -> None:
        self.canvas.set_tool(str(action.data()))
        self.canvas.setFocus()

    def _on_rect(self, x: int, y: int, w: int, h: int) -> None:
        tile = self.doc.add_tile(x, y, w, h, "")
        self._reload_tile_list()
        self._reload_anim_combos()
        self._select(tile.id)
        self._schedule_rebuild()
        self._touch()
        QTimer.singleShot(0, self._focus_tile_name)

    def _on_grid(self, x: int, y: int, w: int, h: int) -> None:
        cell_w, cell_h = self._last_cell
        cell_w = max(1, min(cell_w, w))
        cell_h = max(1, min(cell_h, h))
        grid = Grid(
            name=f"Сетка {len(self.doc.grids) + 1}",
            x=x,
            y=y,
            cell_w=cell_w,
            cell_h=cell_h,
            cols=1,
            rows=1,
            color=GRID_COLORS[len(self.doc.grids) % len(GRID_COLORS)],
            region_w=w,
            region_h=h,
        )
        grid.set_cell_size(cell_w, cell_h)
        self.doc.grids.append(grid)
        self._last_cell = (grid.cell_w, grid.cell_h)
        self._reload_grid_list(select=len(self.doc.grids) - 1)
        self._schedule_rebuild()
        self._touch()
        self._jump_to_active_grid()

    def _on_cells(self, cells: list) -> None:
        if not cells:
            return
        if len(cells) == 1:
            x, y, w, h = cells[0]
            existing = self.doc.tile_at(x, y, w, h)
            if existing is None:
                existing = self.doc.add_tile(x, y, w, h, "")
                self._reload_tile_list()
                self._reload_anim_combos()
                self._touch()
            self._select(existing.id, center=True, focus_name=True)
            self._schedule_rebuild()
            return
        prefix = self.prefix_edit.text().strip() or "tile"
        created: list[Tile] = []
        for index, (x, y, w, h) in enumerate(cells):
            if self.doc.tile_at(x, y, w, h) is not None:
                continue
            name = unique_name([tile.name for tile in self.doc.tiles], f"{prefix}_{index}")
            created.append(self.doc.add_tile(x, y, w, h, name))
        self._reload_tile_list()
        self._reload_anim_combos()
        if created:
            self._select(created[0].id, center=True)
            self._touch()
        self._schedule_rebuild()

    def _on_tile_moved(self, tile_id: int, x: int, y: int) -> None:
        tile = self.doc.find(tile_id)
        if tile is None:
            return
        tile.x = x
        tile.y = y
        self._loading_ui = True
        self.x_spin.setValue(x)
        self.y_spin.setValue(y)
        self._loading_ui = False
        self._update_tile_label(tile)
        self._schedule_rebuild()
        self._touch()

    def _on_cursor(self, x: int, y: int) -> None:
        self._cursor = (x, y)
        self._update_status()

    def _on_hint(self, text: str) -> None:
        self._hint = text
        self._update_status()

    def _on_zoom(self, value: float) -> None:
        self.doc.zoom = round(value, 4)
        self._zoom_text = f"{value * 100:.0f}%"
        self._update_status()

    def _select(self, tile_id: int, *, center: bool = False, focus_name: bool = False) -> None:
        self._selected_id = tile_id
        self.canvas.set_selected(tile_id)
        self._sync_list_to_selection()
        self._fill_inspector()
        tile = self._selected_tile()
        if center and tile is not None:
            self.canvas.center_on_rect(tile.x, tile.y, tile.w, tile.h)
        if focus_name and tile is not None:
            self._focus_tile_name()
        self._update_status()

    def _focus_tile_name(self) -> None:
        if self._selected_tile() is None:
            return
        content = self._side_scroll.widget()
        content.adjustSize()
        bar = self._side_scroll.verticalScrollBar()
        if self._anim_box.isHidden():
            bar.setValue(bar.maximum())
        else:
            bottom = self._inspector.mapTo(content, QPoint(0, self._inspector.height())).y()
            target = bottom - self._side_scroll.viewport().height()
            bar.setValue(min(max(bar.minimum(), target), bar.maximum()))
        self.name_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self.name_edit.selectAll()

    def _reload_tile_list(self) -> None:
        self._loading_ui = True
        self.tile_list.clear()
        for tile in self.doc.tiles:
            item = QListWidgetItem(self._tile_label(tile))
            item.setData(Qt.ItemDataRole.UserRole, tile.id)
            self.tile_list.addItem(item)
            if tile.id == self._selected_id:
                self.tile_list.setCurrentItem(item)
        self.tiles_box.setTitle(f"Тайлы ({len(self.doc.tiles)})")
        self._loading_ui = False
        self._update_layer_item_label()

    def _sync_list_to_selection(self) -> None:
        self._loading_ui = True
        found = False
        for row in range(self.tile_list.count()):
            item = self.tile_list.item(row)
            if int(item.data(Qt.ItemDataRole.UserRole)) == self._selected_id:
                self.tile_list.setCurrentRow(row)
                found = True
                break
        if not found:
            self.tile_list.setCurrentRow(-1)
        self._loading_ui = False

    def _update_tile_label(self, tile: Tile) -> None:
        for row in range(self.tile_list.count()):
            item = self.tile_list.item(row)
            if int(item.data(Qt.ItemDataRole.UserRole)) == tile.id:
                item.setText(self._tile_label(tile))
                break
        self._sync_anim_item_text(tile)

    def _on_tile_row(self, row: int) -> None:
        if self._loading_ui or row < 0:
            return
        item = self.tile_list.item(row)
        if item is None:
            return
        self._select(int(item.data(Qt.ItemDataRole.UserRole)), center=True)

    def _fill_inspector(self) -> None:
        tile = self._selected_tile()
        self._loading_ui = True
        enabled = tile is not None
        for widget in self._inspector_widgets:
            widget.setEnabled(enabled)
        self.props.blockSignals(True)
        self.props.setRowCount(0)
        if tile is None:
            self.name_edit.clear()
            self.id_spin.setValue(0)
            self.x_spin.setValue(0)
            self.y_spin.setValue(0)
            self.w_spin.setValue(1)
            self.h_spin.setValue(1)
        else:
            self.name_edit.setText(tile.name)
            self.id_spin.setValue(tile.id)
            self.x_spin.setValue(tile.x)
            self.y_spin.setValue(tile.y)
            self.w_spin.setValue(tile.w)
            self.h_spin.setValue(tile.h)
            for key, value in tile.extra.items():
                self._append_prop_row(key, format_prop_value(value))
        self.props.blockSignals(False)
        self._loading_ui = False

    def _append_prop_row(self, key: str, value: str) -> None:
        row = self.props.rowCount()
        self.props.insertRow(row)
        self.props.setItem(row, 0, QTableWidgetItem(key))
        self.props.setItem(row, 1, QTableWidgetItem(value))

    def _add_prop_row(self) -> None:
        if self._selected_tile() is None:
            return
        self.props.blockSignals(True)
        self._append_prop_row("", "")
        self.props.blockSignals(False)
        self.props.setCurrentCell(self.props.rowCount() - 1, 0)
        self.props.editItem(self.props.item(self.props.rowCount() - 1, 0))

    def _delete_prop_row(self) -> None:
        row = self.props.currentRow()
        if row < 0:
            return
        self.props.removeRow(row)
        self._on_props_changed()

    def _on_name_changed(self, text: str) -> None:
        if self._loading_ui:
            return
        tile = self._selected_tile()
        if tile is None:
            return
        tile.name = text
        self._update_tile_label(tile)
        self._touch()

    def _on_name_enter(self) -> None:
        if not self._advance_cell():
            self.canvas.setFocus()

    def _advance_cell(self) -> bool:
        tile = self._selected_tile()
        grid = self._active_grid()
        if tile is None or grid is None or not grid.visible:
            return False
        hit = grid.cell_index(tile.x, tile.y)
        if hit is None or tile.w != grid.cell_w or tile.h != grid.cell_h:
            return False
        nxt = grid.next_cell(*hit)
        if nxt is None:
            self.canvas.setFocus()
            return True
        x, y, w, h = grid.cell_rect(*nxt)
        existing = self.doc.tile_at(x, y, w, h)
        if existing is None:
            existing = self.doc.add_tile(x, y, w, h, "")
            self._reload_tile_list()
            self._reload_anim_combos()
            self._touch()
        self._select(existing.id, center=True, focus_name=True)
        self._schedule_rebuild()
        return True

    def _on_id_changed(self, value: int) -> None:
        if self._loading_ui:
            return
        tile = self._selected_tile()
        if tile is None:
            return
        if any(other.id == value and other is not tile for other in self.doc.tiles):
            self.statusBar().showMessage(f"id {value} уже занят", 3000)
            self._loading_ui = True
            self.id_spin.setValue(tile.id)
            self._loading_ui = False
            return
        tile.id = value
        self._selected_id = value
        self.canvas.set_selected(value)
        self._reload_tile_list()
        self._reload_anim_combos()
        self._touch()

    def _on_geom_changed(self) -> None:
        if self._loading_ui:
            return
        tile = self._selected_tile()
        if tile is None:
            return
        tile.x = self.x_spin.value()
        tile.y = self.y_spin.value()
        tile.w = self.w_spin.value()
        tile.h = self.h_spin.value()
        self._update_tile_label(tile)
        self._schedule_rebuild()
        self._touch()

    def _on_props_changed(self, *_args) -> None:
        if self._loading_ui:
            return
        tile = self._selected_tile()
        if tile is None:
            return
        extra: dict = {}
        reserved = False
        for row in range(self.props.rowCount()):
            key_item = self.props.item(row, 0)
            val_item = self.props.item(row, 1)
            key = key_item.text().strip() if key_item else ""
            if not key:
                continue
            if key in TILE_FIELDS:
                reserved = True
                continue
            extra[key] = parse_prop_value(val_item.text() if val_item else "")
        tile.extra = extra
        self._touch()
        if reserved:
            self.statusBar().showMessage("Поля id, name, x, y, w, h задаются отдельно", 3000)

    def _recent_caption(self, path: Path, peers: list[Path]) -> str:
        names = [item.name for item in peers]
        if names.count(path.name) > 1:
            return f"{path.name} — {path.parent.name}"
        return path.name

    def _project_paths(self) -> list[Path]:
        return [Path(item) for item in load_projects()]

    def _open_last_project(self) -> None:
        raw = self._last_project_action.data()
        if raw:
            self._open_recent(Path(str(raw)))

    def _fill_file_recent(self) -> None:
        paths = self._project_paths()
        if paths:
            last = paths[0]
            self._last_project_action.setText(self._recent_caption(last, paths[:1]))
            self._last_project_action.setToolTip(str(last))
            self._last_project_action.setData(str(last))
            self._last_project_action.setEnabled(True)
        else:
            self._last_project_action.setText("Нет проектов")
            self._last_project_action.setToolTip("")
            self._last_project_action.setData(None)
            self._last_project_action.setEnabled(False)
        for action in self._recent_slots:
            self._file_menu.removeAction(action)
            action.deleteLater()
        self._recent_slots.clear()
        paths = paths[:RECENT_INLINE]
        anchor = self._all_recent_menu.menuAction()
        if not paths:
            empty = QAction("Недавних нет", self)
            empty.setEnabled(False)
            self._file_menu.insertAction(anchor, empty)
            self._recent_slots.append(empty)
            return
        for path in paths:
            action = QAction(self._recent_caption(path, paths), self)
            action.setToolTip(str(path))
            action.triggered.connect(lambda _checked=False, target=path: self._open_recent(target))
            self._file_menu.insertAction(anchor, action)
            self._recent_slots.append(action)

    def _fill_all_recent(self) -> None:
        menu = self._all_recent_menu
        menu.clear()
        paths = self._project_paths()
        if not paths:
            empty = menu.addAction("Пока пусто")
            empty.setEnabled(False)
            return
        for path in paths:
            action = menu.addAction(f"{path.name}  —  {path.parent}")
            action.setToolTip(str(path))
            action.triggered.connect(lambda _checked=False, target=path: self._open_recent(target))

    def _manage_recent(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Недавние")
        dialog.resize(560, 380)
        listing = QListWidget()
        listing.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)

        def refill() -> None:
            listing.clear()
            for item in load_projects():
                path = Path(item)
                row = QListWidgetItem(f"{path.name}  —  {path.parent}")
                row.setToolTip(str(path))
                row.setData(Qt.ItemDataRole.UserRole, str(path))
                listing.addItem(row)

        def remove_selected() -> None:
            targets = [Path(row.data(Qt.ItemDataRole.UserRole)) for row in listing.selectedItems()]
            for target in targets:
                forget(target)
            refill()

        def open_current() -> None:
            row = listing.currentItem()
            if row is None:
                return
            target = Path(row.data(Qt.ItemDataRole.UserRole))
            dialog.accept()
            self._open_recent(target)

        def clear_all() -> None:
            for item in load_projects():
                forget(Path(item))
            refill()

        refill()
        listing.itemDoubleClicked.connect(lambda _item: open_current())
        remove_btn = QPushButton("Удалить")
        open_btn = QPushButton("Открыть")
        clear_btn = QPushButton("Очистить всё")
        close_btn = QPushButton("Закрыть")
        remove_btn.clicked.connect(remove_selected)
        open_btn.clicked.connect(open_current)
        clear_btn.clicked.connect(clear_all)
        close_btn.clicked.connect(dialog.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(open_btn)
        buttons.addWidget(remove_btn)
        buttons.addWidget(clear_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout = QVBoxLayout(dialog)
        layout.addWidget(listing)
        layout.addLayout(buttons)
        dialog.exec()

    def _open_recent(self, path: Path) -> None:
        if not path.is_file():
            forget(path)
            QMessageBox.warning(self, "Недавние", f"Файл больше не найден:\n{path}")
            return
        if path.suffix.lower() == ".json":
            self.open_document(path)
        else:
            self.open_image(path)

    def _reload_layer_list(self, select: int | None = None) -> None:
        if select is None:
            select = self.layer_list.currentRow()
        self._loading_ui = True
        self.layer_list.clear()
        for tileset in self.doc.tilesets:
            item = QListWidgetItem(f"{tileset.name}  ({len(tileset.tiles)})")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if tileset.visible else Qt.CheckState.Unchecked
            )
            self.layer_list.addItem(item)
        if self.doc.tilesets:
            select = max(0, min(select, len(self.doc.tilesets) - 1))
            self.layer_list.setCurrentRow(select)
            self.doc.active_index = select
        self.layer_name.setText(self.doc.nametileset)
        self._loading_ui = False

    def _on_layer_row(self, row: int) -> None:
        if self._loading_ui or row < 0 or row == self.doc.active_index:
            return
        self.doc.active_index = row
        self._selected_id = -1
        self.canvas.set_selected(-1)
        self._loading_ui = True
        self.layer_name.setText(self.doc.nametileset)
        self._loading_ui = False
        self._reload_tile_list()
        self._reload_anim_combos()
        self._fill_inspector()
        self._schedule_rebuild()
        self._update_status()

    def _on_layer_item_changed(self, item: QListWidgetItem) -> None:
        if self._loading_ui:
            return
        row = self.layer_list.row(item)
        if row < 0 or row >= len(self.doc.tilesets):
            return
        visible = item.checkState() == Qt.CheckState.Checked
        if self.doc.tilesets[row].visible == visible:
            return
        self.doc.tilesets[row].visible = visible
        self._schedule_rebuild()
        self._touch()

    def _on_layer_name(self, text: str) -> None:
        if self._loading_ui:
            return
        self.doc.nametileset = text
        self._update_layer_item_label()
        self._touch()
        self._update_status()

    def _update_layer_item_label(self) -> None:
        row = self.doc.active_index
        item = self.layer_list.item(row)
        if item is None:
            return
        tileset = self.doc.active_set()
        self._loading_ui = True
        item.setText(f"{tileset.name}  ({len(tileset.tiles)})")
        item.setCheckState(
            Qt.CheckState.Checked if tileset.visible else Qt.CheckState.Unchecked
        )
        self._loading_ui = False

    def _add_layer(self) -> None:
        self.doc.add_tileset()
        self._selected_id = -1
        self.canvas.set_selected(-1)
        self._reload_layer_list(select=self.doc.active_index)
        self._reload_tile_list()
        self._reload_anim_combos()
        self._fill_inspector()
        self._schedule_rebuild()
        self._touch()
        self.layer_name.setFocus()
        self.layer_name.selectAll()

    def _delete_layer(self) -> None:
        if len(self.doc.tilesets) <= 1:
            self.statusBar().showMessage("Нужен хотя бы один набор", 3000)
            return
        tileset = self.doc.active_set()
        if tileset.tiles:
            answer = QMessageBox.question(
                self,
                "Удалить набор",
                f"Удалить набор «{tileset.name}» и его тайлы?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        index = self.doc.active_index
        self.doc.remove_tileset(index)
        self._selected_id = -1
        self.canvas.set_selected(-1)
        self._reload_layer_list(select=self.doc.active_index)
        self._reload_tile_list()
        self._reload_anim_combos()
        self._fill_inspector()
        self._schedule_rebuild()
        self._touch()

    def _on_background_tile(self, layer: int, tile_id: int) -> None:
        if not 0 <= layer < len(self.doc.tilesets):
            return
        self.doc.active_index = layer
        self._selected_id = tile_id
        self._reload_layer_list(select=layer)
        self._reload_tile_list()
        self._reload_anim_combos()
        self._select(tile_id, center=True)
        self._schedule_rebuild()

    def _on_mode_changed(self, index: int) -> None:
        if self._loading_ui:
            return
        mode = "dict" if index == 1 else "array"
        if mode == self.doc.mode:
            return
        if mode == "dict":
            problems = self.doc.dict_problems()
            if problems:
                self._loading_ui = True
                self.mode_combo.setCurrentIndex(0 if self.doc.mode == "array" else 1)
                self._loading_ui = False
                QMessageBox.warning(
                    self,
                    "Словарь",
                    "Нельзя переключить режим:\n" + "\n".join(problems),
                )
                return
        self.doc.mode = mode
        self._touch()

    def _nudge(self, dx: int, dy: int) -> None:
        tile = self._selected_tile()
        if tile is None:
            return
        tile.x += dx
        tile.y += dy
        self._loading_ui = True
        self.x_spin.setValue(tile.x)
        self.y_spin.setValue(tile.y)
        self._loading_ui = False
        self._update_tile_label(tile)
        self._schedule_rebuild()
        self._touch()

    def _duplicate(self) -> None:
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QAbstractSpinBox)):
            return
        if self._selected_id < 0:
            return
        tile = self.doc.duplicate_tile(self._selected_id)
        if tile is None:
            return
        self._reload_tile_list()
        self._reload_anim_combos()
        self._select(tile.id, center=True, focus_name=True)
        self._schedule_rebuild()
        self._touch()

    def _delete_selected(self, *, force: bool = False) -> None:
        if not force:
            focus = QApplication.focusWidget()
            if isinstance(focus, (QLineEdit, QAbstractSpinBox, QComboBox, QTableWidget)):
                return
        if self._selected_id < 0:
            return
        self.doc.remove_tile(self._selected_id)
        self._selected_id = -1
        self.canvas.set_selected(-1)
        self._reload_tile_list()
        self._reload_anim_combos()
        self._fill_inspector()
        self._schedule_rebuild()
        self._touch()

    def _reload_grid_list(self, select: int | None = None) -> None:
        if select is None:
            select = self.grid_list.currentRow()
        self._loading_ui = True
        self.grid_list.clear()
        for grid in self.doc.grids:
            item = QListWidgetItem(self._grid_label(grid))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if grid.visible else Qt.CheckState.Unchecked
            )
            self.grid_list.addItem(item)
        if self.doc.grids:
            select = max(0, min(select, len(self.doc.grids) - 1))
            self.grid_list.setCurrentRow(select)
        self._loading_ui = False
        self._fill_grid_panel()
        self.canvas.set_active_grid(self.grid_list.currentRow())

    def _fill_grid_panel(self) -> None:
        grid = self._active_grid()
        self._loading_ui = True
        for widget in self._grid_editors:
            widget.setEnabled(grid is not None)
        if grid is None:
            self.grid_name.clear()
            self._loading_ui = False
            return
        self.grid_name.setText(grid.name)
        self.grid_x.setValue(grid.x)
        self.grid_y.setValue(grid.y)
        self.grid_cw.setValue(grid.cell_w)
        self.grid_ch.setValue(grid.cell_h)
        self.grid_cols.setValue(grid.cols)
        self.grid_rows.setValue(grid.rows)
        self._paint_color_button(grid.color)
        self._loading_ui = False

    def _on_grid_row(self, row: int) -> None:
        if self._loading_ui:
            return
        self._fill_grid_panel()
        self.canvas.set_active_grid(row)
        self._schedule_rebuild()
        self._jump_to_active_grid()

    def _jump_to_active_grid(self) -> None:
        grid = self._active_grid()
        if grid is None:
            return
        self.canvas.center_on_rect(
            grid.x,
            grid.y,
            grid.cols * grid.cell_w,
            grid.rows * grid.cell_h,
        )
        QTimer.singleShot(0, self._reveal_grid_panel)

    def _reveal_grid_panel(self) -> None:
        if self._active_grid() is None:
            return
        content = self._side_scroll.widget()
        content.adjustSize()
        top = self._grids_box.mapTo(content, QPoint(0, 0)).y()
        bar = self._side_scroll.verticalScrollBar()
        bar.setValue(min(max(bar.minimum(), top), bar.maximum()))
        self.grid_cw.setFocus(Qt.FocusReason.OtherFocusReason)
        self.grid_cw.selectAll()

    def _on_grid_item_changed(self, item: QListWidgetItem) -> None:
        if self._loading_ui:
            return
        row = self.grid_list.row(item)
        if row < 0 or row >= len(self.doc.grids):
            return
        visible = item.checkState() == Qt.CheckState.Checked
        if self.doc.grids[row].visible == visible:
            return
        self.doc.grids[row].visible = visible
        self._schedule_rebuild()
        self._touch()

    def _on_grid_name(self, text: str) -> None:
        if self._loading_ui:
            return
        grid = self._active_grid()
        if grid is None:
            return
        grid.name = text or "Сетка"
        self._update_grid_item_label()
        self._touch()

    def _on_grid_origin(self) -> None:
        if self._loading_ui:
            return
        grid = self._active_grid()
        if grid is None:
            return
        grid.x = self.grid_x.value()
        grid.y = self.grid_y.value()
        self._grid_mutated()

    def _on_grid_cell(self) -> None:
        if self._loading_ui:
            return
        grid = self._active_grid()
        if grid is None:
            return
        grid.set_cell_size(self.grid_cw.value(), self.grid_ch.value())
        self._last_cell = (grid.cell_w, grid.cell_h)
        self._loading_ui = True
        self.grid_cols.setValue(grid.cols)
        self.grid_rows.setValue(grid.rows)
        self._loading_ui = False
        self._grid_mutated()

    def _on_grid_span(self) -> None:
        if self._loading_ui:
            return
        grid = self._active_grid()
        if grid is None:
            return
        grid.set_cols_rows(self.grid_cols.value(), self.grid_rows.value())
        self._grid_mutated()

    def _grid_mutated(self) -> None:
        self._update_grid_item_label()
        self._schedule_rebuild()
        self._touch()

    def _update_grid_item_label(self) -> None:
        row = self.grid_list.currentRow()
        grid = self._active_grid()
        if row < 0 or grid is None:
            return
        item = self.grid_list.item(row)
        if item is None:
            return
        self._loading_ui = True
        item.setText(self._grid_label(grid))
        self._loading_ui = False

    def _pick_grid_color(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        grid = self._active_grid()
        if grid is None:
            return
        color = QColorDialog.getColor(QColor(grid.color), self, "Цвет сетки")
        if not color.isValid():
            return
        grid.color = color.name()
        self._paint_color_button(grid.color)
        self._schedule_rebuild()
        self._touch()

    def _paint_color_button(self, color: str) -> None:
        qcolor = QColor(color)
        if not qcolor.isValid():
            qcolor = QColor("#3d8bfd")
        text = "#000000" if qcolor.lightness() > 140 else "#ffffff"
        self.grid_color_btn.setStyleSheet(f"background: {qcolor.name()}; color: {text};")

    def _delete_grid(self) -> None:
        row = self.grid_list.currentRow()
        if row < 0:
            return
        del self.doc.grids[row]
        self._reload_grid_list(select=min(row, len(self.doc.grids) - 1))
        self._schedule_rebuild()
        self._touch()

    def _reload_anim_combos(self) -> None:
        for slot, combo in (("a", self.anim_a), ("b", self.anim_b)):
            current = self._anim_ids[slot]
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("—", -1)
            for tile in self.doc.tiles:
                combo.addItem(self._tile_choice_label(tile), tile.id)
            index = combo.findData(current if self.doc.find(current) else -1)
            if index < 0:
                self._anim_ids[slot] = -1
                index = 0
            combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)
        self._update_preview()

    def _sync_anim_item_text(self, tile: Tile) -> None:
        label = self._tile_choice_label(tile)
        for combo in (self.anim_a, self.anim_b):
            for index in range(combo.count()):
                if combo.itemData(index) == tile.id:
                    combo.setItemText(index, label)

    def _on_anim_combo(self, slot: str, index: int) -> None:
        combo = self.anim_a if slot == "a" else self.anim_b
        value = combo.itemData(index)
        self._anim_ids[slot] = int(value) if value is not None else -1
        self._sequence_ids = []
        self._update_preview()

    def _assign_anim(self, slot: str) -> None:
        if self._selected_id < 0:
            return
        self._anim_ids[slot] = self._selected_id
        combo = self.anim_a if slot == "a" else self.anim_b
        index = combo.findData(self._selected_id)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            self._update_preview()

    def _update_preview(self) -> None:
        frames = [tile for tile_id in self._sequence_ids if (tile := self.doc.find(tile_id)) is not None]
        self.preview.set_state(
            self._pixmap,
            self.doc.find(self._anim_ids["a"]),
            self.doc.find(self._anim_ids["b"]),
            self.opacity_slider.value() / 100,
            self.blink_btn.isChecked(),
            self._blink_show_a,
            frames,
            self._sequence_index,
        )

    def _on_blink_toggled(self, on: bool) -> None:
        if on:
            self._blink_show_a = True
            self._restart_blink()
        else:
            self._blink_timer.stop()
        self._update_preview()

    def _restart_blink(self) -> None:
        if not self.blink_btn.isChecked():
            return
        fps = max(1, self.fps_spin.value())
        self._blink_timer.start(max(1, int(1000 / fps)))

    def _on_blink_tick(self) -> None:
        if self._sequence_ids:
            self._sequence_index = (self._sequence_index + 1) % len(self._sequence_ids)
        else:
            self._blink_show_a = not self._blink_show_a
        self._update_preview()

    def _on_canvas_selection(self, tile_id: int) -> None:
        self._select(tile_id)
        if self._recording_anim and tile_id >= 0:
            self._append_anim_frame(tile_id)

    def _on_record_toggled(self, on: bool) -> None:
        self._recording_anim = on
        if on:
            self._anim_draft.clear()
            self._sequence_ids = []
            self.set_tool("select")
            self._refresh_draft_label()
            self.statusBar().showMessage("Кликай тайлы по порядку, потом «Готово»", 4000)
            return
        self._refresh_draft_label()

    def _clear_anim_draft(self) -> None:
        self._anim_draft.clear()
        self._recording_anim = False
        if self.anim_record_btn.isChecked():
            self.anim_record_btn.setChecked(False)
        self._refresh_draft_label()

    def _append_anim_frame(self, tile_id: int) -> None:
        if tile_id in self._anim_draft:
            self.statusBar().showMessage("Этот тайл уже в этой анимации", 2000)
            return
        tile = self.doc.find(tile_id)
        if tile is None:
            return
        self._anim_draft.append(tile_id)
        self._refresh_draft_label()

    def _refresh_draft_label(self) -> None:
        if not self._anim_draft:
            text = "Кликай тайлы по порядку" if self._recording_anim else "Набрать — клики по тайлам, Готово — цикл"
        else:
            names = []
            for tile_id in self._anim_draft:
                tile = self.doc.find(tile_id)
                names.append(tile.name or str(tile_id) if tile else str(tile_id))
            text = " → ".join(names)
        self.anim_draft_label.setText(text)

    def _animation_groups(self) -> dict[str, list[Tile]]:
        grouped: dict[str, list[tuple[int, Tile]]] = {}
        for tile in self.doc.tiles:
            raw_name = tile.extra.get("animation_name")
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue
            raw_order = tile.extra.get("animation_id", 0)
            if isinstance(raw_order, bool):
                order = 0
            elif isinstance(raw_order, int):
                order = raw_order
            else:
                try:
                    order = int(raw_order)
                except (TypeError, ValueError):
                    order = 0
            grouped.setdefault(raw_name.strip(), []).append((order, tile))
        return {name: [tile for _order, tile in sorted(frames)] for name, frames in grouped.items()}

    def _commit_animation(self) -> None:
        if len(self._anim_draft) < 2:
            self.statusBar().showMessage("Нужно хотя бы два разных тайла", 3000)
            return
        suggested = unique_name(list(self._animation_groups()), "anim")
        name, accepted = QInputDialog.getText(self, "Анимация", "Название анимации", text=suggested)
        if not accepted:
            return
        name = name.strip()
        if not name:
            self.statusBar().showMessage("Без названия анимация не сохранится", 3000)
            return
        if name in self._animation_groups():
            answer = QMessageBox.question(
                self,
                "Анимация",
                f"«{name}» уже есть. Заменить её кадры?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._apply_animation(name)

    def _apply_animation(self, name: str) -> None:
        self._strip_animation(name)
        for index, tile_id in enumerate(self._anim_draft):
            tile = self.doc.find(tile_id)
            if tile is None:
                continue
            tile.extra["animation_name"] = name
            tile.extra["animation_id"] = index
        self._anim_draft.clear()
        self._recording_anim = False
        if self.anim_record_btn.isChecked():
            self.anim_record_btn.setChecked(False)
        self._refresh_draft_label()
        self._touch()
        self._fill_inspector()
        self._refresh_anim_panel(select=name)
        self._play_animation(name)
        self._reveal_anim_panel()
        self.statusBar().showMessage(f"«{name}»: animation_id — номер кадра с нуля", 4000)

    def _strip_animation(self, name: str) -> None:
        for tile in self.doc.tiles:
            if tile.extra.get("animation_name") == name:
                tile.extra.pop("animation_name", None)
                tile.extra.pop("animation_id", None)

    def _refresh_anim_panel(self, select: str | None = None) -> None:
        groups = self._animation_groups()
        self._anim_box.setVisible(bool(groups))
        self._loading_ui = True
        self.anim_list.clear()
        chosen = -1
        for row, name in enumerate(groups):
            count = len(groups[name])
            item = QListWidgetItem(f"{name}  ({count})")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.anim_list.addItem(item)
            if name == select:
                chosen = row
        if chosen >= 0:
            self.anim_list.setCurrentRow(chosen)
        self._loading_ui = False

    def _reveal_anim_panel(self) -> None:
        if self._anim_box.isHidden():
            return
        content = self._side_scroll.widget()
        content.adjustSize()
        top = self._anim_box.mapTo(content, QPoint(0, 0)).y()
        bar = self._side_scroll.verticalScrollBar()
        bar.setValue(min(max(bar.minimum(), top), bar.maximum()))

    def _on_anim_list_row(self, row: int) -> None:
        if self._loading_ui or row < 0:
            return
        item = self.anim_list.item(row)
        if item is None:
            return
        self._play_animation(str(item.data(Qt.ItemDataRole.UserRole)))

    def _play_animation(self, name: str) -> None:
        tiles = self._animation_groups().get(name, [])
        self._sequence_ids = [tile.id for tile in tiles]
        self._sequence_index = 0
        if not self.blink_btn.isChecked():
            self.blink_btn.setChecked(True)
        else:
            self._restart_blink()
        self._update_preview()

    def _start_animation_recording(self) -> None:
        if not self.anim_record_btn.isChecked():
            self.anim_record_btn.setChecked(True)
        else:
            self._on_record_toggled(True)

    def _delete_selected_animation(self) -> None:
        item = self.anim_list.currentItem()
        if item is None:
            return
        name = str(item.data(Qt.ItemDataRole.UserRole))
        answer = QMessageBox.question(
            self,
            "Анимация",
            f"Удалить «{name}»? С тайлов снимутся animation_name и animation_id.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._strip_animation(name)
        if self._sequence_ids and name not in self._animation_groups():
            self._sequence_ids = []
            self.blink_btn.setChecked(False)
        self._touch()
        self._fill_inspector()
        self._refresh_anim_panel()
        self._update_preview()


def grab_preview(window: MainWindow) -> QImage:
    """Снимок холста для проверки, что тайлы и сетка вообще рисуются."""
    image = QImage(window.canvas.viewport().size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("#e8e8e8"))
    painter = QPainter(image)
    window.canvas.render(painter)
    painter.end()
    return image
