# Essential File Scanner

**English** | [한국어](README.ko.md)

A local desktop tool for checking files and folders that must be present on a Windows PC. View availability, access status, the most recent file, its last modified time, and elapsed time in one dashboard. No internet connection or separate server is required.

The application interface is currently in Korean. This guide includes the original UI labels where helpful.

## Features

- Register individual files, folders, and wildcard file patterns (`*`, `?`).
- Optionally search subfolders and find the most recent matching file.
- Scan automatically at startup, refresh manually, or cancel an ongoing scan.
- Keep the interface responsive by running scans outside the GUI thread.
- Add files and folders with drag and drop; edit, delete, or enable registered items.
- Search by name, path, or most recent file; filter by status and sort the table.
- Open folders in File Explorer, reveal files, and copy paths.
- Store JSON settings and rotating logs under `%APPDATA%`.

An old file does not trigger a warning on its own. If a path exists and is accessible, its status is **OK** (`정상`).

## Run from source

Python 3.12 or later and Windows PowerShell are recommended.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

If your virtual environment is already activated, you can also run:

```powershell
python main.py
```

On first launch, add items from the settings dialog or the dashboard's Add button. You can save paths that do not exist or are currently disconnected; their status is determined when a scan runs.

## Status definitions

| Status | Meaning |
| --- | --- |
| OK (`정상`) | The file or folder exists, or a file matches the pattern, and it is accessible. |
| File missing (`파일 없음`) | The file's parent folder or the pattern's base folder exists, but the target file is missing. |
| Path missing (`경로 없음`) | The registered folder or the file's parent path does not exist. |
| Inaccessible (`접근 불가`) | The path cannot be checked because of a permission, device, network, or I/O problem. |

An empty folder is OK as long as the folder itself is accessible. Folder and pattern scans retain only the most recent file while traversing the directory, rather than keeping the entire file list in memory. Recursive scans do not follow symbolic links or junctions.

By default, GUI scans allow up to 60 seconds per item. If an item exceeds this limit, it is marked **Inaccessible**, and a separate scan process starts to continue with the remaining items. This prevents an unresponsive NAS from blocking the entire scan, but a local folder with a very large number of files may also exceed the limit. Adjust `MainWindow`'s `item_timeout_seconds` for your deployment, or set it to `None` to disable the timeout. Clicking **Cancel scan** (`검사 취소`) also terminates the active child process.

## Settings and logs

User data is stored in the writable Roaming AppData directory rather than next to the executable.

```text
%APPDATA%\RequiredFileScanner\config.json
%APPDATA%\RequiredFileScanner\logs\scanner.log
```

Settings are saved by writing a temporary file and replacing the existing file, reducing the risk of corruption if the application exits during a save. If the settings file cannot be read, the application logs the cause and starts with an empty configuration.

## Tests

Install the development dependencies, then run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

Tests cover file, folder, and pattern scans; recursive search; selection of the most recent file; empty folders; missing paths; settings persistence; and relative time formatting.

## Build a Windows executable

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\build.ps1 -Python .\.venv\Scripts\python.exe -Clean
```

If PowerShell's execution policy blocks the script, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Python .\.venv\Scripts\python.exe -Clean
```

The executable is created at `dist\RequiredFileScanner.exe`. The PyInstaller configuration uses `one-file` and `windowed` modes, so Python does not need to be installed on the target PC. Before deployment, test with the paths you intend to monitor, including disconnected NAS paths.

## Project structure

```text
main.py                       Application entry point, global error handling, and logging
core/models.py                Configuration item and scan result models
core/scanner.py               File system scanning independent of the UI
core/config_manager.py        AppData paths, JSON settings, and file logging
ui/main_window.py             Dashboard and background scan coordination
ui/settings_dialog.py         Registered item list and drag-and-drop support
ui/item_dialog.py             File, folder, and pattern editor
tests/                        Automated tests
RequiredFileScanner.spec      PyInstaller build definition
build.ps1                     Windows build script
```

## Troubleshooting

- **`No module named PySide6`:** Run `python -m pip install -r requirements.txt` in your current Python environment.
- **A network path takes too long:** The GUI remains responsive during each item's timeout period (60 seconds by default). Click **Cancel scan** (`검사 취소`) if needed. If a large, accessible folder exceeds the limit, increase `item_timeout_seconds` for your deployment.
- **Registered items are missing:** Check `%APPDATA%\RequiredFileScanner\logs\scanner.log` for errors reading the settings file.
- **File Explorer does not open the location:** Check that the path exists and that your user account has permission to access it.
