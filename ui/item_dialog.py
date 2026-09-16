"""Editor dialog for file, folder, and wildcard scan items."""

from __future__ import annotations

from pathlib import PureWindowsPath

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.models import ConfigItem, ItemType

from .core_adapter import (
    build_config_item,
    default_display_name,
    get_item_id,
    get_item_path,
    get_item_pattern,
    get_item_type,
    is_item_enabled,
    is_item_recursive,
    item_type_key,
    item_type_label,
)
from .styles import apply_app_style


class ItemDialog(QDialog):
    """Collect and validate one :class:`ConfigItem`."""

    def __init__(
        self,
        kind: ItemType | str,
        *,
        item: ConfigItem | None = None,
        initial_path: str = "",
        auto_browse: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._source_item = item
        self._kind = get_item_type(item) if item is not None else kind
        self._result_item: ConfigItem | None = None

        self.setWindowTitle(
            f"{item_type_label(self._kind)} {'수정' if item is not None else '추가'}"
        )
        self.setModal(True)
        self.setMinimumWidth(530)
        apply_app_style(self)

        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText("비워 두면 파일명, 폴더명 또는 패턴을 사용합니다.")

        self.path_edit = QLineEdit(self)
        self.path_edit.setClearButtonEnabled(True)
        self.path_edit.setPlaceholderText(
            "예: \\\\SERVER\\Data" if item_type_key(self._kind) != "file" else "예: C:\\Data\\report.csv"
        )
        self.browse_button = QPushButton(
            "파일 선택" if item_type_key(self._kind) == "file" else "폴더 선택",
            self,
        )
        self.browse_button.clicked.connect(self._browse)

        path_row = QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(8)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(self.browse_button)
        path_widget = QWidget(self)
        path_widget.setLayout(path_row)

        self.pattern_edit = QLineEdit(self)
        self.pattern_edit.setClearButtonEnabled(True)
        self.pattern_edit.setPlaceholderText("예: Daily_*.xlsx, Recipe_??.json")

        self.recursive_check = QCheckBox("하위 폴더 포함", self)
        self.recursive_check.setChecked(True)
        self.recursive_check.setToolTip("체크하면 모든 하위 폴더를 함께 검색합니다.")

        self.enabled_check = QCheckBox("활성화", self)
        self.enabled_check.setChecked(True)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(14)
        form.addRow("종류", QLabel(item_type_label(self._kind), self))
        form.addRow("표시 이름", self.name_edit)
        form.addRow("파일 경로" if item_type_key(self._kind) == "file" else "기준 폴더", path_widget)
        if item_type_key(self._kind) == "pattern":
            form.addRow("파일 패턴", self.pattern_edit)
        if item_type_key(self._kind) in {"folder", "pattern"}:
            form.addRow("검색 범위", self.recursive_check)
        form.addRow("사용", self.enabled_check)

        panel = QFrame(self)
        panel.setObjectName("contentCard")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(20, 20, 20, 20)
        panel_layout.addLayout(form)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("errorLabel")
        self.error_label.setWordWrap(True)
        self.error_label.hide()

        self.cancel_button = QPushButton("취소", self)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = QPushButton("저장", self)
        self.save_button.setObjectName("primaryButton")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._accept_if_valid)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.save_button)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)
        root.addWidget(panel)
        root.addWidget(self.error_label)
        root.addLayout(button_row)

        if item is not None:
            self.name_edit.setText(str(getattr(item, "name", "")))
            self.path_edit.setText(get_item_path(item))
            self.pattern_edit.setText(get_item_pattern(item))
            self.recursive_check.setChecked(is_item_recursive(item))
            self.enabled_check.setChecked(is_item_enabled(item))
        else:
            self.path_edit.setText(initial_path)
            if initial_path:
                self.name_edit.setText(default_display_name(self._kind, initial_path))

        self.name_edit.setFocus()
        self.name_edit.selectAll()
        if auto_browse and item is None and not initial_path:
            # The picker appears first for the common path, but cancelling it
            # deliberately leaves this editor open so offline/future paths can
            # still be typed by hand.
            QTimer.singleShot(0, self._browse)

    @property
    def config_item(self) -> ConfigItem | None:
        return self._result_item

    def _initial_browse_location(self) -> str:
        current = self.path_edit.text().strip()
        if not current:
            return ""
        if item_type_key(self._kind) == "file":
            return str(PureWindowsPath(current).parent)
        return current

    def _browse(self) -> None:
        old_path = self.path_edit.text().strip()
        old_default = default_display_name(
            self._kind, old_path, self.pattern_edit.text().strip()
        )
        if item_type_key(self._kind) == "file":
            selected, _ = QFileDialog.getOpenFileName(
                self,
                "필수 파일 선택",
                self._initial_browse_location(),
                "모든 파일 (*.*)",
            )
        else:
            selected = QFileDialog.getExistingDirectory(
                self,
                "기준 폴더 선택" if item_type_key(self._kind) == "pattern" else "필수 폴더 선택",
                self._initial_browse_location(),
                QFileDialog.Option.ShowDirsOnly,
            )
        if not selected:
            return
        self.path_edit.setText(selected)
        current_name = self.name_edit.text().strip()
        if not current_name or current_name == old_default:
            self.name_edit.setText(
                default_display_name(self._kind, selected, self.pattern_edit.text().strip())
            )

    def _show_error(self, message: str, target: QWidget) -> None:
        self.error_label.setText(message)
        self.error_label.show()
        target.setFocus()
        if isinstance(target, QLineEdit):
            target.selectAll()

    def _accept_if_valid(self) -> None:
        path = self.path_edit.text().strip().strip('"')
        pattern = self.pattern_edit.text().strip()
        if not path:
            self._show_error("경로를 입력하거나 찾아보기 버튼으로 선택해 주세요.", self.path_edit)
            return
        if item_type_key(self._kind) == "pattern" and not pattern:
            self._show_error("파일 패턴을 입력해 주세요. 예: *.csv", self.pattern_edit)
            return
        if "\x00" in path or "\x00" in pattern:
            self._show_error("경로와 패턴에는 NUL 문자를 사용할 수 없습니다.", self.path_edit)
            return

        name = self.name_edit.text().strip()
        if not name:
            name = default_display_name(self._kind, path, pattern)
        if not name:
            self._show_error("표시 이름을 입력해 주세요.", self.name_edit)
            return

        try:
            self._result_item = build_config_item(
                item_id=get_item_id(self._source_item) if self._source_item is not None else None,
                name=name,
                kind=self._kind,
                path=path,
                pattern=pattern,
                recursive=(
                    self.recursive_check.isChecked()
                    if item_type_key(self._kind) in {"folder", "pattern"}
                    else False
                ),
                enabled=self.enabled_check.isChecked(),
            )
        except (TypeError, ValueError) as error:
            QMessageBox.critical(self, "항목 저장 실패", str(error))
            return
        self.accept()
