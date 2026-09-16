"""Application-wide visual styling for the Required File Scanner."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget


STATUS_COLORS: dict[str, tuple[str, str]] = {
    "ok": ("#157347", "#E9F7EF"),
    "file_missing": ("#B42318", "#FDECEC"),
    "path_missing": ("#B42318", "#FDECEC"),
    "access_error": ("#A15C00", "#FFF3DF"),
    "pending": ("#667085", "#F2F4F7"),
}


APP_STYLE = """
QWidget {
    color: #1F2937;
    font-family: "Malgun Gothic", "Segoe UI", sans-serif;
    font-size: 10pt;
}

QMainWindow, QDialog {
    background: #F6F8FB;
}

QFrame#headerCard, QFrame#contentCard, QFrame#dropPanel,
QFrame#detailCard, QFrame#emptyCard {
    background: #FFFFFF;
    border: 1px solid #E3E8EF;
    border-radius: 10px;
}

QLabel#appTitle {
    color: #172B4D;
    font-size: 20pt;
    font-weight: 700;
}

QLabel#sectionTitle {
    color: #172B4D;
    font-size: 13pt;
    font-weight: 700;
}

QLabel#mutedLabel, QLabel#lastScanLabel, QLabel#dropHint {
    color: #667085;
}

QLabel#errorLabel {
    color: #B42318;
}

QPushButton {
    min-height: 34px;
    padding: 0 14px;
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    font-weight: 600;
}

QPushButton:hover {
    background: #F8FAFC;
    border-color: #94A3B8;
}

QPushButton:pressed {
    background: #EEF2F7;
}

QPushButton:disabled {
    color: #98A2B3;
    background: #F2F4F7;
    border-color: #E4E7EC;
}

QPushButton#primaryButton {
    color: #FFFFFF;
    background: #2563EB;
    border-color: #2563EB;
}

QPushButton#primaryButton:hover {
    background: #1D4ED8;
}

QPushButton#dangerButton {
    color: #B42318;
    background: #FFFFFF;
    border-color: #FDA29B;
}

QLineEdit, QComboBox {
    min-height: 34px;
    padding: 0 10px;
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    selection-background-color: #BFDBFE;
}

QLineEdit:focus, QComboBox:focus {
    border: 2px solid #3B82F6;
}

QTableView, QTableWidget {
    background: #FFFFFF;
    alternate-background-color: #F8FAFC;
    border: none;
    gridline-color: #E7ECF2;
    selection-background-color: #DBEAFE;
    selection-color: #172B4D;
}

QHeaderView::section {
    color: #475467;
    background: #F1F5F9;
    border: none;
    border-bottom: 1px solid #D8E0EA;
    padding: 10px 8px;
    font-weight: 700;
}

QTableView::item, QTableWidget::item {
    padding: 8px;
    border-bottom: 1px solid #EEF2F6;
}

QProgressBar {
    min-height: 8px;
    max-height: 8px;
    border: none;
    border-radius: 4px;
    background: #E5E7EB;
    text-align: center;
}

QProgressBar::chunk {
    border-radius: 4px;
    background: #3B82F6;
}

QToolTip {
    color: #FFFFFF;
    background: #344054;
    border: 1px solid #475467;
    padding: 6px;
}

QMenu {
    background: #FFFFFF;
    border: 1px solid #D0D5DD;
    padding: 5px;
}

QMenu::item {
    padding: 7px 24px;
    border-radius: 4px;
}

QMenu::item:selected {
    background: #EAF2FF;
}
"""


def apply_app_style(widget: QWidget) -> None:
    """Apply the shared stylesheet to a top-level widget."""

    widget.setStyleSheet(APP_STYLE)
