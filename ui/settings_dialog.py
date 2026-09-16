"""Settings UI for registering and maintaining scan items."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QFileInfo, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.models import ConfigItem, ItemType

from .core_adapter import (
    clone_items,
    get_item_id,
    get_item_path,
    get_item_pattern,
    get_item_type,
    is_item_enabled,
    is_item_recursive,
    item_type_key,
    item_type_label,
    update_config_item,
)
from .item_dialog import ItemDialog
from .styles import apply_app_style


class DropTableWidget(QTableWidget):
    """A settings table that accepts Explorer file and folder URLs."""

    paths_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)

    @staticmethod
    def _has_local_urls(event: QDragEnterEvent | QDropEvent) -> bool:
        mime = event.mimeData()
        return mime.hasUrls() and any(url.isLocalFile() for url in mime.urls())

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._has_local_urls(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._has_local_urls(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.paths_dropped.emit(paths)
        else:
            event.ignore()


class SettingsDialog(QDialog):
    """Edit a transactional copy of the application's item list."""

    def __init__(
        self,
        items: Iterable[ConfigItem],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._items = clone_items(items)
        self.setWindowTitle("필수 항목 설정")
        self.setModal(True)
        self.resize(980, 620)
        self.setMinimumSize(760, 480)
        apply_app_style(self)

        title = QLabel("필수 항목 설정", self)
        title.setObjectName("sectionTitle")
        subtitle = QLabel(
            "검사할 파일, 폴더 또는 파일 패턴을 등록합니다. 현재 존재하지 않는 경로도 저장할 수 있습니다.",
            self,
        )
        subtitle.setObjectName("mutedLabel")
        subtitle.setWordWrap(True)

        self.add_file_button = QPushButton("＋ 파일 추가", self)
        self.add_folder_button = QPushButton("＋ 폴더 추가", self)
        self.add_pattern_button = QPushButton("＋ 파일 패턴 추가", self)
        self.add_file_button.clicked.connect(self._add_file)
        self.add_folder_button.clicked.connect(self._add_folder)
        self.add_pattern_button.clicked.connect(self._add_pattern)

        add_buttons = QHBoxLayout()
        add_buttons.setSpacing(8)
        add_buttons.addWidget(self.add_file_button)
        add_buttons.addWidget(self.add_folder_button)
        add_buttons.addWidget(self.add_pattern_button)
        add_buttons.addStretch(1)

        self.table = DropTableWidget(self)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["사용", "이름", "종류", "경로 / 패턴", "하위 폴더", "관리"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(self._edit_selected)
        self.table.paths_dropped.connect(self._handle_dropped_paths)

        self.empty_label = QLabel(
            "등록된 필수 항목이 없습니다. 위 버튼을 누르거나 Windows 탐색기에서 파일·폴더를 끌어다 놓으세요.",
            self,
        )
        self.empty_label.setObjectName("mutedLabel")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)

        drop_hint = QLabel(
            "파일 또는 폴더를 이 영역에 Drag & Drop하여 등록할 수 있습니다.", self
        )
        drop_hint.setObjectName("dropHint")
        drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        table_panel = QFrame(self)
        table_panel.setObjectName("contentCard")
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(1, 1, 1, 10)
        table_layout.setSpacing(8)
        table_layout.addWidget(self.table, 1)
        table_layout.addWidget(self.empty_label)
        table_layout.addWidget(drop_hint)

        self.cancel_button = QPushButton("취소", self)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = QPushButton("설정 저장", self)
        self.save_button.setObjectName("primaryButton")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.accept)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.cancel_button)
        bottom.addWidget(self.save_button)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addLayout(add_buttons)
        root.addWidget(table_panel, 1)
        root.addLayout(bottom)

        self._refresh_table()

    @property
    def config_items(self) -> list[ConfigItem]:
        return clone_items(self._items)

    def _item_index(self, item_id: str) -> int:
        for index, item in enumerate(self._items):
            if get_item_id(item) == item_id:
                return index
        return -1

    @staticmethod
    def _centered(widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(widget)
        return container

    def _refresh_table(self) -> None:
        self.table.setRowCount(0)
        for row, item in enumerate(self._items):
            self.table.insertRow(row)
            item_id = get_item_id(item)

            enabled = QCheckBox(self.table)
            enabled.setChecked(is_item_enabled(item))
            enabled.setToolTip("Dashboard 검사에 포함")
            enabled.toggled.connect(
                lambda checked, stable_id=item_id: self._set_enabled(stable_id, checked)
            )
            self.table.setCellWidget(row, 0, self._centered(enabled))

            name_cell = QTableWidgetItem(str(getattr(item, "name", "")))
            name_cell.setData(Qt.ItemDataRole.UserRole, item_id)
            self.table.setItem(row, 1, name_cell)
            self.table.setItem(row, 2, QTableWidgetItem(item_type_label(get_item_type(item))))

            path = get_item_path(item)
            pattern = get_item_pattern(item)
            combined = f"{path}  |  {pattern}" if pattern else path
            path_cell = QTableWidgetItem(combined)
            path_cell.setToolTip(combined)
            self.table.setItem(row, 3, path_cell)

            recursive = (
                "✓" if is_item_recursive(item) else "—"
            ) if item_type_key(get_item_type(item)) in {"folder", "pattern"} else "—"
            recursive_cell = QTableWidgetItem(recursive)
            recursive_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 4, recursive_cell)

            edit_button = QPushButton("수정", self.table)
            edit_button.setToolTip("선택한 항목 수정")
            edit_button.clicked.connect(
                lambda _checked=False, stable_id=item_id: self._edit_item(stable_id)
            )
            delete_button = QPushButton("삭제", self.table)
            delete_button.setObjectName("dangerButton")
            delete_button.setToolTip("선택한 항목 삭제")
            delete_button.clicked.connect(
                lambda _checked=False, stable_id=item_id: self._delete_item(stable_id)
            )
            manage = QWidget(self.table)
            manage_layout = QHBoxLayout(manage)
            manage_layout.setContentsMargins(4, 3, 4, 3)
            manage_layout.setSpacing(5)
            manage_layout.addWidget(edit_button)
            manage_layout.addWidget(delete_button)
            self.table.setCellWidget(row, 5, manage)

        self.empty_label.setVisible(not self._items)

    def _set_enabled(self, item_id: str, checked: bool) -> None:
        index = self._item_index(item_id)
        if index >= 0:
            self._items[index] = update_config_item(self._items[index], enabled=checked)

    def _open_item_editor(
        self,
        kind: ItemType | str,
        *,
        item: ConfigItem | None = None,
        initial_path: str = "",
        auto_browse: bool = False,
    ) -> ConfigItem | None:
        dialog = ItemDialog(
            kind,
            item=item,
            initial_path=initial_path,
            auto_browse=auto_browse,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.config_item

    def _append_new(
        self,
        kind: ItemType | str,
        initial_path: str = "",
        *,
        auto_browse: bool = False,
    ) -> None:
        item = self._open_item_editor(
            kind, initial_path=initial_path, auto_browse=auto_browse
        )
        if item is not None:
            self._items.append(item)
            self._refresh_table()
            self.table.selectRow(len(self._items) - 1)

    def _add_file(self) -> None:
        self._append_new(ItemType.FILE, auto_browse=True)

    def _add_folder(self) -> None:
        self._append_new(ItemType.FOLDER, auto_browse=True)

    def _add_pattern(self) -> None:
        self._append_new(ItemType.PATTERN)

    def _edit_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        id_cell = self.table.item(row, 1)
        if id_cell is not None:
            self._edit_item(str(id_cell.data(Qt.ItemDataRole.UserRole)))

    def _edit_item(self, item_id: str) -> None:
        index = self._item_index(item_id)
        if index < 0:
            return
        original = self._items[index]
        edited = self._open_item_editor(get_item_type(original), item=original)
        if edited is not None:
            self._items[index] = edited
            self._refresh_table()
            self.table.selectRow(index)

    def _delete_item(self, item_id: str) -> None:
        index = self._item_index(item_id)
        if index < 0:
            return
        name = str(getattr(self._items[index], "name", ""))
        answer = QMessageBox.question(
            self,
            "항목 삭제",
            f"‘{name}’ 항목을 삭제할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._items.pop(index)
        self._refresh_table()

    def _handle_dropped_paths(self, paths: list[str]) -> None:
        for path in paths:
            info = QFileInfo(path)
            if info.isDir():
                self._append_new(ItemType.FOLDER, path)
            elif info.isFile():
                self._append_new(ItemType.FILE, path)
            else:
                QMessageBox.warning(
                    self,
                    "등록할 수 없는 항목",
                    f"파일 또는 폴더로 확인할 수 없습니다.\n{path}",
                )
