"""Окно разметчика: список тайлов, инспектор, сетки и превью двух кадров."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer
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
    QFileDialog,
    QFormLayout,
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
from tilemapper.recent import forget, load_recent, remember

IMAGE_FILTER = "Изображения (*.png *.bmp *.gif *.jpg *.jpeg *.webp)"
JSON_FILTER = "JSON (*.json)"


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

    def set_state(
        self,
        pixmap: QPixmap,
        tile_a: Tile | None,
        tile_b: Tile | None,
        opacity: float,
        blinking: bool,
        show_a: bool,
    ) -> None:
        self._pixmap = pixmap
        self._tile_a = tile_a
        self._tile_b = tile_b
        self._opacity = opacity
        self._blinking = blinking
        self._show_a = show_a
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        self._paint_checker(painter)
        if self._tile_a is None and self._tile_b is None:
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
        if self._blinking:
            tile = self._tile_a if self._show_a else self._tile_b
            self._blit(painter, tile, origin_x, origin_y, scale, 1.0)
        else:
            self._blit(painter, self._tile_a, origin_x, origin_y, scale, 1.0)
            self._blit(painter, self._tile_b, origin_x, origin_y, scale, self._opacity)
        painter.end()

    def _box_size(self) -> tuple[int, int]:
        width = max(tile.w for tile in (self._tile_a, self._tile_b) if tile is not None)
        height = max(tile.h for tile in (self._tile_a, self._tile_b) if tile is not None)
        return max(1, width), max(1, height)

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
        self._rebuild_queued = False
        self._tool_actions: dict[str, QAction] = {}

        self.canvas = Canvas()
        self._build_ui()
        self._connect()
        self._apply_document(fit=False)
        self._update_title()
        self.resize(1280, 820)
        self.setAcceptDrops(True)

    def _build_ui(self) -> None:
        self._build_toolbar()
        self.tile_list = QListWidget()
        self.tile_list.setMinimumHeight(140)
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
        form = QFormLayout(inspector)
        form.addRow("Имя", self.name_edit)
        form.addRow("id", self.id_spin)
        form.addRow("x", self.x_spin)
        form.addRow("y", self.y_spin)
        form.addRow("w", self.w_spin)
        form.addRow("h", self.h_spin)
        form.addRow("Свои поля", self.props)
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
        self.grid_list.setMinimumHeight(90)
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
        self.layer_list.setMinimumHeight(80)
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
        side_layout.addWidget(layers)
        side_layout.addWidget(self.tiles_box)
        side_layout.addWidget(inspector)
        side_layout.addWidget(grids)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(side)
        scroll.setMinimumWidth(320)

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

        open_image = QAction("Картинка", self)
        open_image.setShortcut(QKeySequence("Ctrl+Shift+O"))
        open_image.setToolTip("Открыть картинку как новый документ")
        open_image.triggered.connect(self._pick_image)
        open_json = QAction("JSON", self)
        open_json.setShortcut(QKeySequence("Ctrl+O"))
        open_json.triggered.connect(self._pick_json)
        save = QAction("Сохранить", self)
        save.setShortcut(QKeySequence("Ctrl+S"))
        save.setToolTip("Рабочий файл: тайлы и временные сетки")
        save.triggered.connect(lambda: self.save_document())
        save_as = QAction("Сохранить как", self)
        save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_as.triggered.connect(self.save_document_as)
        export = QAction("Экспорт чистый", self)
        export.setShortcut(QKeySequence("Ctrl+E"))
        export.setToolTip("JSON без блока editor — то, что ест игра")
        export.triggered.connect(lambda: self.export_document())
        self.recent_button = QToolButton()
        self.recent_button.setText("Недавние")
        self.recent_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.recent_menu = QMenu(self.recent_button)
        self.recent_button.setMenu(self.recent_menu)
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Массив", "Словарь"])
        self.mode_combo.setFixedWidth(110)
        self.mode_combo.setToolTip("Словарь: имя тайла — уникальный ключ")

        group = QActionGroup(self)
        group.setExclusive(True)
        tools = (
            ("select", "Выбор", "Клик, перетаскивание. Стрелки — 1 px, Shift — 8. Ctrl+D дубль, Delete удалить. Клавиша 1"),
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

        for action in (open_image, open_json, save, save_as, export):
            bar.addAction(action)
        bar.addWidget(self.recent_button)
        bar.addSeparator()
        bar.addWidget(self.mode_combo)
        bar.addSeparator()
        for action in self._tool_actions.values():
            bar.addAction(action)
        bar.addSeparator()
        bar.addAction(self.snap_action)
        bar.addAction(actual)
        bar.addAction(fit)
        bar.addWidget(QLabel(" Префикс "))
        bar.addWidget(self.prefix_edit)

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
        wrap = QWidget()
        wrap.setLayout(controls)
        wrap.setMinimumWidth(280)
        layout = QHBoxLayout(box)
        layout.addWidget(wrap)
        layout.addWidget(self.preview, 1)
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
        self.canvas.selection_changed.connect(lambda tile_id: self._select(tile_id))
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
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            QMessageBox.warning(self, "Картинка", f"Не удалось прочитать:\n{path}")
            return False
        self._pixmap = pixmap
        self.doc = Document.from_image(path)
        remember(path)
        self._reset_session()
        self._apply_document(fit=True)
        self._dirty = False
        self._update_title()
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
        self._load_pixmap(document.image_path)
        if document.image_path is not None and not document.image_path.is_file():
            QMessageBox.warning(
                self,
                "Картинка",
                f"Рядом с JSON не найдена картинка:\n{document.image_path}",
            )
        elif document.image_path is not None and self._pixmap.isNull():
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
            suggest = self.doc.image_path.with_suffix(".json")
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
            if suffix in {".png", ".bmp", ".gif", ".jpg", ".jpeg", ".webp"}:
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
        self.blink_btn.setChecked(False)

    def _load_pixmap(self, path: Path | None) -> None:
        if path is None or not path.is_file():
            self._pixmap = QPixmap()
            return
        self._pixmap = QPixmap(str(path))

    def _apply_document(self, *, fit: bool) -> None:
        self._loading_ui = True
        self.mode_combo.setCurrentIndex(1 if self.doc.mode == "dict" else 0)
        self._loading_ui = False
        self._reload_layer_list(select=self.doc.active_index)
        self._reload_tile_list()
        select = 0 if self.doc.grids else -1
        self._reload_grid_list(select=select)
        self._reload_anim_combos()
        self._fill_inspector()
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
        self._select(tile.id, focus_name=True)
        self._schedule_rebuild()
        self._touch()

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
        self.grid_cw.setFocus()
        self.grid_cw.selectAll()

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
            self.name_edit.setFocus()
            self.name_edit.selectAll()
        self._update_status()

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

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        paths = load_recent()
        if not paths:
            empty = self.recent_menu.addAction("Пока пусто")
            empty.setEnabled(False)
            return
        for item in paths:
            path = Path(item)
            action = self.recent_menu.addAction(f"{path.name}  —  {path.parent}")
            action.setToolTip(str(path))
            action.triggered.connect(lambda _checked=False, target=path: self._open_recent(target))

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

    def _delete_selected(self) -> None:
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
        self.preview.set_state(
            self._pixmap,
            self.doc.find(self._anim_ids["a"]),
            self.doc.find(self._anim_ids["b"]),
            self.opacity_slider.value() / 100,
            self.blink_btn.isChecked(),
            self._blink_show_a,
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
        self._blink_show_a = not self._blink_show_a
        self._update_preview()


def grab_preview(window: MainWindow) -> QImage:
    """Снимок холста для проверки, что тайлы и сетка вообще рисуются."""
    image = QImage(window.canvas.viewport().size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("#e8e8e8"))
    painter = QPainter(image)
    window.canvas.render(painter)
    painter.end()
    return image
