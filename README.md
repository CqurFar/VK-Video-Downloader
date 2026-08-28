# VK Video Downloader

CLI-загрузчик видео [VK Видео](https://vkvideo.ru) через запущенный Firefox:
приложение открывает страницу видео в вашем браузере, перехватывает DASH-манифест
(MPD) из сетевого трафика плеера, скачивает дорожки и склеивает их через ffmpeg.

- Работает с приватными/возрастно-ограниченными видео, доступными вашему
  залогиненному Firefox.
- Одиночные ссылки, файлы ссылок, плейлисты и списки плейлистов.
- Форматы: `mp4`, `mkv`, `webm` + аудио (`mp3`, `aac`, `m4a`, `ogg`, `opus`,
  `flac`, `wav`).
- Два режима консоли: **normal** — простой статус для пользователя,
  **advanced** (`--advanced`) — подробный технический вывод; `--debug`
  включает advanced + логи и дампы манифестов.

## Установка с нуля

### 0) Python и uv

Установите Python 3.13+ ([python.org/downloads](https://www.python.org/downloads/))
и менеджер пакетов uv:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Проверка: `python --version` и `uv --version`.

### 1) Клонировать проект

```powershell
git clone <URL-репозитория> vk_video_downloader
cd vk_video_downloader
```

(или просто распакуйте архив проекта в отдельную папку)

### 1.5) Зависимости

```powershell
uv sync
```

### 2) Firefox

1. Установите [Firefox](https://www.mozilla.org/firefox/) и войдите в свой аккаунт VK.
2. Откройте нужное видео на vkvideo.ru и убедитесь, что оно **воспроизводится**.
3. Включите режим Marionette и перезапустите браузер:
   - вариант A (одноразово): закрыть Firefox и запустить
     `"C:\Program Files\Mozilla Firefox\firefox.exe" -marionette`
   - вариант B (постоянно): в `about:config` создать
     `marionette.enabled = true` и перезапустить Firefox.
4. **Не закрывайте браузер** во время работы загрузчика.

### 3) Профиль Firefox (опционально)

Загрузчик подключается к уже открытому Firefox, поэтому профиль используется ваш.
Для корректной preflight-проверки скопируйте папку профиля в проект:

```
C:\Users\<вы>\AppData\Local\Mozilla\Firefox\Profiles\xxxx.default-release
    -> .\packages\firefox_profile\
```

или укажите путь переменной окружения:

```bat
set "VK_DOWNLOADER_FIREFOX_PROFILE=C:\Users\<вы>\AppData\Local\Mozilla\Firefox\Profiles\xxxx.default-release"
```

### 4) geckodriver

Скачайте сборку под Windows со страницы релизов
[github.com/mozilla/geckodriver/releases](https://github.com/mozilla/geckodriver/releases),
распакуйте и положите exe сюда:

```
.\packages\geckodriver\geckodriver.exe
```

(либо убедитесь, что он доступен в PATH)

### 5) ffmpeg

Скачайте full-сборку (например, с
[gyan.dev/ffmpeg/builds](https://www.gyan.dev/ffmpeg/builds/) или
[github.com/BtbN/FFmpeg-Builds/releases](https://github.com/BtbN/FFmpeg-Builds/releases))
и разложите файлы так:

```
.\packages\ffmpeg\bin\ffmpeg.exe
.\packages\ffmpeg\bin\ffprobe.exe
```

(либо установите ffmpeg в систему через PATH)

## Быстрый старт

```powershell
# одна ссылка
uv run vk-dl "https://vkvideo.ru/video-232462760_456239037" --video best --audio best --format mkv

# список ссылок из файла
uv run vk-dl --file urls.txt

# один плейлист
uv run vk-dl --playlist "https://vkvideo.ru/playlist/-232462760_50"

# несколько плейлистов из файла
uv run vk-dl --playlist-file playlists.txt
```

Подойдёт любая форма ссылки: `vkvideo.ru/video-232462760_456239037`,
`vkvideo.ru/video_ext.php?oid=...&id=...`, `vk.ru/video_ext.php?...`,
в т.ч. с параметром `?pl=` — загрузчик сам нормализует её к каноническому виду.

Либо заполните переменные в `run.bat` (там же выбирается MODE:
`normal` / `advanced` / `debugging`) и запустите двойным кликом.

## Параметры командной строки

### Выбор источника (что качаем)

| Параметр | Что делает |
|---|---|
| `<urls>...` | одна или несколько ссылок на видео прямо в команде |
| `--file <файл>` | текстовый файл со ссылками: по одной на строку, строки с `#` игнорируются |
| `--playlist <url>` | один плейлист VK — скачиваются все его видео в одну папку |
| `--playlist-file <файл>` | файл со ссылками на плейлисты — каждый качается целиком в свою папку, затем следующий |

Приоритет: `--playlist-file` > `--playlist` > обычные ссылки.

```powershell
# 1. Одна или несколько ссылок
uv run vk-dl "https://vkvideo.ru/video-232462760_456239037" "https://vkvideo.ru/video-232462760_456239038"

# 2. Ссылки из файла urls.txt
uv run vk-dl --file urls.txt

# 3. Целый плейлист
uv run vk-dl --playlist "https://vkvideo.ru/playlist/-232462760_50"

# 4. Несколько плейлистов
uv run vk-dl --playlist-file playlists.txt
```

### Качество и формат

| Параметр | Значения | По умолчанию |
|---|---|---|
| `--video` | `best` / `none` / высота кадра (`1080`, `720`, `360`...) | `best` |
| `--audio` | `best` / `none` / битрейт kbps (`128`, `192`...) | `best` |
| `--format` | см. примеры ниже: контейнер, аудио-формат или `контейнер+аудио` | `mkv` |
| `--workers N` | максимум параллельных воркеров скачивания сегментов | `64` |
| `--ffmpeg "args"` | дополнительные аргументы ffmpeg при склейке (см. ниже) | — |

```powershell
# максимальное качество
uv run vk-dl "https://..." --video best --audio best --format mkv

# конкретная высота 1080p; если такой нет — ближайшая большая
uv run vk-dl "https://..." --video 1080 --audio 128 --format mp4

# только аудио в mp3
uv run vk-dl "https://..." --video none --format mp3

# только видео без звука
uv run vk-dl "https://..." --video 720 --audio none --format mp4

# контейнер + аудиокодек: mkv, где звук конвертируется в AAC
uv run vk-dl "https://..." --format mkv+aac

# webm c opus-звуком
uv run vk-dl "https://..." --format webm+opus

# WebM без перекодирования: берутся только VP8/VP9/AV1 + Opus/Vorbis дорожки
uv run vk-dl "https://..." --format webm

# ограничить параллелизм скачивания
uv run vk-dl --playlist-file playlists.txt --workers 16
```

Правила `--format`:
- один токен из видеоформатов (`mp4|mkv|webm`) → контейнер, дорожки копируются как есть;
- один токен из аудиоформатов (`mp3|aac|m4a|ogg|opus|flac|wav`) → только аудио;
- `видео+аудио` (например `mkv+aac`, `mp4+opus`) → видео копией, аудио
  конвертируется в указанный кодек внутри контейнера;
- `acc` принимается как алиас правильного `aac`.

### Дополнительные аргументы ffmpeg (--ffmpeg)

Строка `--ffmpeg` дописывается **в конец** команды склейки, поэтому ваши флаги
имеют приоритет над автоматическими (`-c copy` и т.п.) — так работают правила
последнего флага в ffmpeg. Полный список возможностей —
[официальная документация ffmpeg](https://ffmpeg.org/ffmpeg.html).

```powershell
# добавить метаданные в итоговый файл
uv run vk-dl "https://..." --ffmpeg "-metadata title=""Лекция 1"" -metadata artist=ФПМИ"

# mkv+aac со своим битрейтом аудио
uv run vk-dl "https://..." --format mkv+aac --ffmpeg "-b:a 192k"

# принудительно перекодировать видео в 720p H.264
# (перекодирование медленнее, чем копирование дорожек)
uv run vk-dl "https://..." --format mp4 --ffmpeg "-vf scale=-2:720 -c:v libx264 -crf 20 -preset medium"

# вырезать первые 30 секунд (перезапись стартового времени)
uv run vk-dl "https://..." --ffmpeg "-ss 30"
```

Если точное значение качества недоступно среди треков, берётся ближайшее
большее. Кодеки подбираются автоматически под выбранный контейнер.

### Режимы вывода и отладка

| Параметр | Что делает |
|---|---|
| *(по умолчанию)* | normal: баннер, проверки, одна строка на видео, общий прогресс |
| `--advanced` | подробный вывод: секции MPD/DOWNLOAD, прогресс-бары, CDN-детали |
| `--mode normal\|advanced\|debug` | то же самое одним флагом (`debug` = advanced + логи) |
| `--debug` | advanced + дампы `*.mpd` в `downloaded/logs/`, логи, полные traceback'и |

> **Privacy:** дампы `*.mpd` и логи в `--debug` могут содержать подписанные CDN-URL (`token=`, `sig=` и т.д.). Не публикуйте их — значения маскируются как `***`.

```powershell
uv run vk-dl "https://..." --mode advanced
uv run vk-dl "https://..." --debug
```

### Повторные попытки

- Каждый файл проходит до 3 автоматических попыток с растущей паузой.
- После прохода всех ссылок предлагается ручной RETRY только по проблемным.
- Всё нескачанное сохраняется в `failed.txt`; если все загрузки удались — файл
  удаляется. Формат совместим с `--file`:

```powershell
uv run vk-dl --file failed.txt
```

## Структура результатов

```
downloaded/
  <название видео>_<NN>_[ID].<ext>
  <папка плейлиста>/<видео>.<ext>
  logs/*.mpd                 # дампы манифестов (--debug)
.temp_m4s/                   # скрытая временная папка, удаляется автоматически
```

## Для разработчиков

### Архитектура

```
src/vk_downloader/
  __main__.py          # точка входа CLI (uv run vk-dl / python -m vk_downloader)
  settings.py          # конфигурация: пути, браузер, загрузка, кодеки
  core/
    downloader.py      # оркестратор: нормализация ссылок, пайплайн захват↔скачивание
    errors.py          # предметная иерархия исключений (VKDownloadError)
  browser/
    media_browser.py   # WebDriver: вкладка плеера, сбор MPD из трафика, cookies
    webdriver_client.py # минимальный HTTP-клиент Marionette/geckodriver
  download/
    dash_downloader.py # сегменты (пул worker + резюм) или single-file режим
    worker_balancer.py # адаптивное число воркеров
    concurrency_gate.py # динамический лимитер параллелизма
  media/
    mpd_parser.py      # MPD -> треки (BaseURL-цепочки, SegmentTemplate/Timeline)
    ffmpeg_merger.py   # склейка через subprocess ffmpeg с прогрессом
  ui/
    console.py         # два режима вывода: normal / advanced
    fs_paths.py        # \\?\-пути и атрибуты Windows
```

Поток данных одной ссылки:
`run() -> _run_batch() -> _process_with_retry() -> process_one()` →
`get_mpd()` (браузер) → `MPDParser.parse()` → выбор качества →
`DashDownloader.download_track()` → `FFmpegMerger.merge()` → файл в `downloaded/`.

В advanced-режиме батч выполняется как пайплайн: пока видео N скачивается,
браузер уже захватывает манифест видео N+1 (браузерный этап остаётся
последовательным — вкладка одна).

Ключевые инварианты при доработке:

- **Сегменты пишутся атомарно** (`.tmp` + `os.replace`) и кэшируются между
  попытками: существующая непустая часть = гарантированно целый сегмент.
- **`MPDNotFoundError` = SKIP**, остальные ошибки = ERROR с автоповторами.
- **Вкладка плеера переиспользуется** между видео; при выгрузке вкладки
  Firefox ("browsing context discarded") контекст восстанавливается автоматически.
- **Свёрнутое окно Firefox останавливает инициализацию видео** — приложение
  само поднимает окно через WinAPI.

### Разработка

```powershell
uv sync                  # зависимости (+ dev-группа: ruff, pre-commit)
uv run ruff check src    # линт
uv run ruff format src   # форматирование
powershell -File ci.ps1  # локальный CI: то же, что в GitHub Actions
pre-commit install       # хуки на коммит (опционально)
```

Конфигурация ruff — в `pyproject.toml` (`[tool.ruff]`), CI —
`.github/workflows/ci.yml`.

IDE (PyCharm/VS Code): откройте корень проекта, интерпретатор — `.venv`
(`uv sync` создаёт его автоматически); папку `src/vk_downloader` пометьте как
Sources Root, чтобы импорты `vk_downloader.*` подсвечивались без ошибок.

### Переменные окружения

| Переменная | Значение |
|---|---|
| `VK_DOWNLOADER_FIREFOX_PROFILE` | путь к профилю Firefox для preflight-проверки (по умолчанию `./packages/firefox_profile`) |
