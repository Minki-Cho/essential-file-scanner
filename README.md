# 필수 파일 스캐너

Windows PC에서 반드시 존재해야 하는 파일·폴더를 한 화면에서 확인하는 로컬 GUI 도구입니다. 등록한 경로의 존재/접근 상태, 가장 최근 파일, 마지막 수정 시각과 경과 시간을 표시합니다. 인터넷 연결이나 별도 서버는 사용하지 않습니다.

## 주요 기능

- 단일 파일, 폴더, 와일드카드 파일 패턴(`*`, `?`) 등록
- 폴더/패턴의 하위 폴더 검색 설정 및 최신 파일 탐색
- 프로그램 시작 시 자동 검사, 버튼을 통한 수동 새로고침/진행 중 취소
- 검사 작업을 GUI 스레드와 분리해 느린 폴더에서도 화면 응답 유지
- 파일·폴더 Drag & Drop 등록, 등록 항목 수정/삭제/활성화
- 이름·경로·최근 파일 검색과 상태 필터/테이블 정렬
- Explorer에서 폴더 열기, 파일 위치 선택, 경로 복사
- `%APPDATA%` 아래 JSON 설정과 회전 로그 저장

오래된 파일이라는 이유만으로 경고하지 않습니다. 경로가 존재하고 접근 가능하면 `정상`입니다.

## 개발 환경 실행

Python 3.12 이상과 Windows PowerShell을 권장합니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

가상환경을 활성화했다면 명세의 기본 명령으로도 실행할 수 있습니다.

```powershell
python main.py
```

최초 실행 시 등록된 항목이 없으면 설정 화면 또는 대시보드의 추가 버튼으로 항목을 등록하세요. 존재하지 않거나 현재 연결되지 않은 경로도 저장할 수 있으며 실제 상태는 검사할 때 판정합니다.

## 상태 판정

| 상태 | 의미 |
| --- | --- |
| 정상 | 파일/폴더가 존재하거나 패턴과 일치하는 파일이 있으며 접근 가능 |
| 파일 없음 | 파일의 부모 폴더 또는 패턴 기준 폴더는 있지만 대상 파일이 없음 |
| 경로 없음 | 등록된 폴더 또는 파일의 부모 경로가 없음 |
| 접근 불가 | 권한, 장치, 네트워크 또는 I/O 문제로 경로를 확인할 수 없음 |

폴더가 비어 있어도 폴더 자체에 접근할 수 있으면 정상입니다. 폴더와 패턴 검색은 파일 목록 전체를 보관하지 않고 순회 중 최신 항목 하나만 유지하며, 심볼릭 링크와 Junction은 재귀 대상으로 따라가지 않습니다.

기본 GUI 검사는 한 항목당 최대 60초를 허용합니다. 제한 시간을 넘긴 항목은 `접근 불가`로 표시하고 별도의 검사 프로세스를 시작해 나머지 항목을 계속 검사합니다. 이 제한은 응답하지 않는 NAS가 전체 검사를 막는 것을 방지하지만, 파일이 매우 많은 로컬 폴더도 60초를 넘기면 같은 상태로 판정될 수 있습니다. 배포 환경에 맞춰 `MainWindow`의 `item_timeout_seconds` 값으로 조정하거나 `None`으로 비활성화할 수 있습니다. 검사 중 `검사 취소` 버튼을 누르면 현재 자식 프로세스까지 정리합니다.

## 설정 및 로그

사용자 데이터는 실행 파일 옆이 아니라 쓰기 가능한 Roaming AppData에 저장됩니다.

```text
%APPDATA%\RequiredFileScanner\config.json
%APPDATA%\RequiredFileScanner\logs\scanner.log
```

설정 저장은 임시 파일을 이용해 교체하므로 쓰는 도중 종료되어도 기존 파일이 훼손될 가능성을 줄였습니다. 읽을 수 없는 설정 파일은 로그에 원인을 남기며 안전한 빈 설정으로 시작합니다.

## 테스트

개발 의존성을 설치한 뒤 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

테스트에는 파일/폴더/패턴 검사, 재귀 설정, 최신 파일 선택, 빈 폴더, 누락 경로, 설정 저장·복원과 상대 시간 표현이 포함됩니다.

## Windows EXE 빌드

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\build.ps1 -Python .\.venv\Scripts\python.exe -Clean
```

PowerShell 실행 정책 때문에 스크립트가 차단되면 다음처럼 실행할 수 있습니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Python .\.venv\Scripts\python.exe -Clean
```

완성 파일은 `dist\RequiredFileScanner.exe`에 생성됩니다. PyInstaller `one-file`, `windowed` 구성이라 대상 PC에는 Python 설치가 필요하지 않습니다. 새 PC에 배포하기 전 실제 운영 경로와 연결이 끊긴 NAS 경로를 포함해 테스트하는 것을 권장합니다.

## 프로젝트 구조

```text
main.py                       프로그램 진입점과 전역 오류/로그 설정
core/models.py                설정 항목과 검사 결과 모델
core/scanner.py               UI와 독립적인 파일 시스템 검사
core/config_manager.py        AppData 경로, JSON 설정, 파일 로그
ui/main_window.py             대시보드와 백그라운드 검사 조정
ui/settings_dialog.py         등록 항목 목록과 Drag & Drop
ui/item_dialog.py             파일/폴더/패턴 편집 화면
tests/                        자동화 테스트
RequiredFileScanner.spec      PyInstaller 빌드 정의
build.ps1                     Windows 빌드 스크립트
```

## 문제 해결

- `No module named PySide6`: 현재 Python 환경에서 `python -m pip install -r requirements.txt`를 실행하세요.
- 네트워크 경로가 오래 걸림: 항목별 제한 시간(기본 60초) 동안 GUI는 계속 응답하며, 필요하면 `검사 취소`를 누를 수 있습니다. 대형 폴더가 정상인데도 제한 시간을 넘긴다면 배포 시 `item_timeout_seconds`를 늘리세요.
- 등록 내용이 보이지 않음: `%APPDATA%\RequiredFileScanner\logs\scanner.log`에서 설정 읽기 오류를 확인하세요.
- Explorer 열기가 실패함: 경로가 실제로 존재하고 현재 사용자에게 접근 권한이 있는지 확인하세요.
