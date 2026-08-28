# VK Video Downloader — Roadmap

> **Baseline:** 27.08.2026 — 12 коммитов `22→27.08`, 37 tracked файлов, `ruff` clean, `pytest` 18/18, `ci.yml` на `ubuntu-latest`.  
> **Текущая оценка:** `7/10` как инженерный проект, `~6/10` production maturity — архитектура опередила покрытие тестами.  
> **Видение:** довести до `8+/10` без новых фич — сначала доказуемая корректность пайплайна, затем поддерживаемость.

Цель этого документа — зафиксировать приоритеты из двух независимых аудитов (обе сессии сошлись), описать *что* делаем, *зачем*, *чем* и *как проверим*.

---

## 1. Методология

- **Correctness > Reliability > Maintainability > DX.** Фичи заморожены до закрытия `P0`.
- **Evidence-first:** каждая задача сверена с кодом `src/vk_downloader/*:line`.
- **Effort/Impact:** `S` ≤4ч, `M` 1-2 дня, `L` 3-5 дней.
- **Верификация:** `uv run ruff check src`, `uv run ruff format --check`, `uv run pytest -q`, `uv run vk-dl --help`, `powershell -File ci.ps1`, `git log --oneline`.
- **Стек:** `Python 3.13`, `uv`, `ruff`, `pytest 8+`, `pre-commit`, `GitHub Actions` (`ubuntu` + `windows`), `requests`, `ffmpeg`.

---

## 2. P0 — Критично, correctness (1-2 недели, берём первым)

### P0-1 — Починка выбора качества (functional bug, contract violation)
- **Где:** `src/vk_downloader/core/downloader.py:54-123` — `choose_video`, `choose_audio`, `_pick:109-123`
- **Что сейчас:**
  ```python
  exact = [t for t in tracks if t[field]==value]
  tracks = exact or tracks
  return tracks[-1]  # всегда max
  ```
  При запросе `900p` и доступных `720/1080/1440` вернёт `1440` вместо `1080`. `README:212` обещает "ближайшее большее".
- **Что делаем:** `exact → min(t for t in tracks if t[field]>=value) or max(tracks)`. Для video `field="height"`, для audio `field="bandwidth"` с `scale=1000`. Фикс в одном месте `_pick` + прокинуть в `choose_video/choose_audio` или вынести `QualitySelector` (заготовка для P1-3).
- **Чем:** Python `sorted`, `pytest` parametrized.
- **Фикстуры:** `tests/test_quality_selector.py` — `900→1080`, `500→720`, `2000→1440`, `best→max`, `exact 720→720`, bitrate `128→128`, `200→192` ближайший.
- **Критерий:** `pytest` 6+ кейсов зелёно, `README` и код совпадают.
- **Effort:** S

### P0-2 — MPD `r=-1` + фикстуры парсера
- **Где:** `src/vk_downloader/media/mpd_parser.py:138-166` — `negative_repeat_count` для последнего `S` возвращает `1`.
- **Проблема:** по DASH `r=-1` = повтор до следующего `t` или до конца `Period@duration`/`MPD@duration`. Сейчас — потеря сегментов.
- **Что делаем:**
  1. Добавить `tests/fixtures/mpd/` — 12 XML: `namespace`, `nested BaseURL (MPD→Period→AdaptationSet→Representation)`, `SegmentTemplate` в `AdaptationSet` vs `Representation`, `Timeline t/r>0`, `r=-1` not-last, `r=-1` last, `duration=0 skip`, `Period×2`, `duplicate Representations`, `URL ?#`, `no audio`, `no video`.
  2. Фикс `mpd_parser.py:158-166` — если `r=-1` last, использовать `Period@duration`/`MPD@mediaPresentationDuration` если есть, иначе фолбэк `1` + лог warning. Покрыть фикстурами.
  3. Расширить `tests/test_mpd_parser.py:4-25` (сейчас 5 кейсов) → 17+.
- **Чем:** `xml.etree.ElementTree`, `pytest` fixture, `html.unescape` уже в коде.
- **Критерий:** `pytest` 17/17, `ruff` clean.
- **Effort:** M

### P0-3 — Тесты `DashDownloader` + orchestration (главный гэп `testing 4.5/10`)
- **Где:** `src/vk_downloader/download/dash_downloader.py:203-360` (init/resume/rescue/tail_freeze), `core/downloader.py:255-360` retry, `media/ffmpeg_merger.py:167-218` (мок).
- **Что сейчас:** `tests: 5 файлов, 18 тестов` покрывают только `concurrency_gate`, `worker_balancer`, `mpd_parser`, `settings`, `errors`. Нет тестов на `download_track`, `assemble`, `_process_with_retry`, пайплайн.
- **Что делаем (in-process, без сети):**
  - `test_dash_downloader.py` — `parts cache` (`_valid_part:195`), `resume` (`pending` vs `finished:278`), `rescue phase` (`drain:312-360`), `tail_freeze:333-339` (при `len(finished) >= 0.9*total`).
  - `test_orchestration.py` — `resolve_quality` → `download_track` мок → `merge` мок, `failed.txt` `downloader.py:365`.
  - Использовать `tmp_path`, `ThreadPoolExecutor` мок, `WorkerBalancer`/`ConcurrencyGate` реальные.
- **Чем:** `pytest`, `tmp_path`, `monkeypatch`, `asyncio`.
- **Критерий:** `pytest -q` +5 тестов, покрытие `download/*` >60% (пока без браузера).
- **Effort:** M

### P0-4 — Windows CI (оставляем, per твой запрос)
- **Где:** `.github/workflows/ci.yml:7` сейчас только `ubuntu-latest`, `ci.ps1:5` только `vk-dl --help` vs `README:301` "то же, что GH Actions".
- **Что делаем:**
  ```yaml
  test-windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - run: uv run ruff check src --output-format=github
      - run: uv run ruff format src --check
      - run: uv run pytest -q
      - run: uv run vk-dl --help
  ```
  Синхронизировать `ci.ps1` — добавить `ruff check` + `pytest`.
- **Чем:** `setup-uv@v5`, `windows-latest`.
- **Критерий:** `ci.yml` зелёно на `ubuntu` и `windows`, локально `powershell -File ci.ps1` == CI.
- **Effort:** S

---

## 3. P1 — Важно, reliability/maintainability (2-4 недели, после P0)

### P1-1 — Atomic final output (crash safety)
- **Где:** `media/ffmpeg_merger.py:141` `FSPaths.long(output)` напрямую, `dash_downloader.py:364` `open("wb") final`, `_download_with_progress:155` прямой `open`.
- **Что делаем:** `output.with_suffix(".tmp")` → `ffmpeg` → `fsync` → `os.replace(tmp, output)`. Сегменты уже `tmp→replace:113` — применить тот же паттерн к финалу.
- **Чем:** `pathlib`, `os.replace`, `os.fsync`.
- **Критерий:** `kill -9` mid-merge → нет битого `mkv/mp4`.
- **Effort:** S

### P1-2 — Privacy: signed URL в debug логах
- **Где:** `core/downloader.py:187-188` `mpd_file.write_text(mpd_text)`, `download/dash_downloader.py:44-58` headers с `Referer/Origin`.
- **Что делаем:** в `README` добавить "Debug dumps may contain signed URLs — do not share", перед `write_text` маскировать `token|sig|expires|hash` → `***`.
- **Чем:** `re.sub`, `README` docs.
- **Effort:** S

### P1-3 — Декомпозиция `core/downloader.py:25` 914стр (god-object)
- **Что делаем (только после P0-3):**
  - `QualitySelector` — `choose_video:55`, `choose_audio:75`, `_pick:109`
  - `InputResolver` — `normalize_url:383`, `parse_args:410-498`
  - `DownloadOrchestrator` — `_run_batch:515`, `_run_batch_pipeline:563`, `_process_with_retry:255`
  - `EnvironmentChecker` — `preflight:745`, `_probe_version:734`
  - `ResultStore` — `_save_failed:365`, `failed.txt`
  - `VKMediaDownloader` → тонкий координатор (<300стр).
- **Чем:** `python-expert` skill, `ruff` isort.
- **Критерий:** `pytest` зелёно, `VKMediaDownloader` <300стр, `git blame` сохранён.
- **Effort:** L

### P1-4 — Domain types `dataclass` вместо `dict`
- **Где:** `mpd_parser.py:83-94` `track:dict` с 9 ключами, `downloader.py:280` `outcome:dict`.
- **Что делаем:** начать с `MediaTrack(frozen=True)` + `DownloadResult`, `BrowserCapture` — постепенно, не всё сразу. Добавить `__all__` в `__init__.py`.
- **Чем:** `dataclasses`, `mypy` опционально.
- **Effort:** M

### P1-5 — WebDriver `to_thread` (полу-async система)
- **Где:** `browser/webdriver_client.py:1-5` `requests` внутри `async` (`downloader.py:1-3` `asyncio.gather`), `ffmpeg_merger.py:183` `Popen` внутри `async _materialize:147`.
- **Что делаем:** обернуть `requests` и `Popen` в `asyncio.to_thread`, убрать `per-file-ignores ASYNC210` из `pyproject.toml` после.
- **Чем:** `asyncio.to_thread`, `pytest-asyncio`.
- **Effort:** M

### P1-6 — Pipeline retry инвалидация MPD
- **Где:** `core/downloader.py:617-662` `_download_with_retry` ретраит `same data` vs `255-313` `new MPD`.
- **Что делаем:** при `pipeline && retry` перезахватить `data = await browser_helper.get_mpd(...)` или ввести `CaptureResult` с `expires_at`.
- **Effort:** M

---

## 4. P2 — Бэклог (когда в прод)

- **P2-1** Интеграционные browser тесты (fake `FirefoxRemoteSession`) — дорого, flaky.
- **P2-2** `FailureKind` enum + `RetryPolicy` unified — только если вводишь `dataclass` + `P1-3`.
- **P2-3** Синхронизация `README:301` vs `ci.ps1` — 5 мин, но DX.

## 5. Won't fix сейчас

- Полный редизайн `application/domain/transport/infrastructure/cli` (§19) — овер-дизайн для 37 файлов.
- Retry multiplication как проблема — текущие слои изолированы осознанно.
- `FailureKind` как P0 — достаточно `SKIP vs ERROR` `core/errors.py:1-40`.

---

## 6. План исполнения по фазам

**Фаза 0 (день 0):** ветка `roadmap/p0`, `uv sync`, снапшот `ruff/pytest` baseline.  
**Фаза 1 — P0 последовательно, test-first:**
1. `P0-1` failing `tests/test_quality_selector.py` → фикс → `ruff --fix` → зелёно
2. `P0-2` фикстуры + фикс `r=-1` → зелёно
3. `P0-3` `test_dash_downloader` → зелёно
4. `P0-4` `ci.yml` windows → пуш, проверка на `windows-latest`

**Фаза 2 — P1 (после зелёного P0):** `P1-1 → P1-2 → P1-3 (рефактор только с покрытием) → P1-4 постепенно → P1-5/6`

**Коммиты:** 1 задача = 1 `conventional commit` (`fix:`, `test:`, `ci:`, `refactor:`), пуш пачкой после фазы. Даты — реальные (после 27.08), не backdated.

---

## 7. Метрики готовности

| Метрика | Сейчас | После P0 | После P1 | Чем мерим |
|---|---|---|---|---|
| testing | 4.5/10 | 6.5/10 | 7.5/10 | `pytest --cov` branch >70% |
| reliability | 6.5/10 | 7.5/10 | 8/10 | atomic final + retry доказуем |
| DevOps | 5.5/10 | 7/10 (win CI) | 7.5/10 | ubuntu+windows зелёно |
| production | ~6/10 | 7/10 | 8/10 | без новых фич |

---

## 8. Риски

- VK DOM churn → browser hotspot (§8) — не рефакторить сверх `BrowserCaptureResult`.
- `r=-1` без `Period@duration` → фолбэк `1` + warning.
- Декомпозиция без P0-3 = риск регресса — строго после тестов.

---

*Сгенерировано 28.08.2026 на основе двух аудитов + сверки с кодом. Следующий шаг — `build` фазы P0.*
