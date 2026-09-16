"""Windows GUI entry point for Required File Scanner."""

from __future__ import annotations

import ctypes
import logging
import multiprocessing
import sys
from types import TracebackType

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from core.config_manager import ConfigManager, configure_file_logging
from ui.main_window import MainWindow


APP_VERSION = "1.0.0"
logger = logging.getLogger(__name__)


def _set_windows_app_id() -> None:
    """Give the packaged app a stable Windows taskbar identity."""

    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
            "OpenAI.RequiredFileScanner.1"
        )
    except (AttributeError, OSError):
        logger.debug("Windows AppUserModelID 설정을 건너뜁니다.", exc_info=True)


def _install_exception_handler() -> None:
    previous_hook = sys.excepthook

    def handle_exception(
        exception_type: type[BaseException],
        exception: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        if issubclass(exception_type, KeyboardInterrupt):
            previous_hook(exception_type, exception, traceback)
            return
        logger.critical(
            "처리되지 않은 예외",
            exc_info=(exception_type, exception, traceback),
        )
        application = QApplication.instance()
        if application is not None:
            QMessageBox.critical(
                None,
                "필수 파일 스캐너 오류",
                "예상하지 못한 오류가 발생했습니다.\n"
                "프로그램 로그에서 자세한 내용을 확인해 주세요.",
            )

    sys.excepthook = handle_exception


def main() -> int:
    # Required by Windows spawn and by PyInstaller child scan processes.
    multiprocessing.freeze_support()

    QCoreApplication.setOrganizationName("RequiredFileScanner")
    QCoreApplication.setApplicationName("Required File Scanner")
    QCoreApplication.setApplicationVersion(APP_VERSION)

    try:
        configure_file_logging()
    except OSError:
        logging.basicConfig(level=logging.INFO)
        logger.exception("파일 로그를 준비하지 못해 기본 로그를 사용합니다.")

    _install_exception_handler()
    _set_windows_app_id()
    logger.info("프로그램 실행 (version=%s)", APP_VERSION)

    application = QApplication(sys.argv)
    application.setStyle("Fusion")
    window: MainWindow | None = None
    exit_code = 1
    try:
        window = MainWindow(config_manager=ConfigManager())
        application.aboutToQuit.connect(window.shutdown)
        window.show()
        exit_code = application.exec()
    finally:
        if window is not None and not window.shutdown():
            logger.error("백그라운드 검사 작업을 제한 시간 안에 종료하지 못했습니다.")

    logger.info("프로그램 종료 (code=%d)", exit_code)
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
