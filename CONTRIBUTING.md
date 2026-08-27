# Contributing

## Быстрый старт

```bash
git clone https://github.com/CqurFar/VK-Video-Downloader.git
cd VK-Video-Downloader
uv sync --all-groups   # требует Python >=3.11, ставит ruff/pytest/pre-commit
uv run pytest -q       # 121 тест, ~0.4s
uv run ruff check src
uv run ruff format src --check
```

`uv sync` ставит пакет в editable режиме (`src` layout). `pyproject.toml` уже содержит
`[tool.hatch.build.targets.wheel] packages = ["src/vk_downloader"]`, поэтому
`from vk_downloader...` работает без ручной настройки `PYTHONPATH`.

## IDE

* **PyCharm**: открой корень проекта → `src` автоматически помечается как Sources
  благодаря hatch-конфигу. Если нет — `File → Project Structure → Mark Directory as → Sources`
  для `src` и `Tests` для `tests`. SDK — любой Python 3.11+.
* **VS Code**: `settings.json` уже не нужен — Pylance читает `pyproject.toml`.
  Если `unresolved import` — проверь, что выбран интерпретатор `.venv`.

Python поддерживается **3.11+** (`requires-python = ">=3.11"`). В `README` пока 3.13 — будет обновлён.

## Архитектура (чтобы не было сюрпризов)

```
browser/  — Firefox/WebDriver, сбор MPD из performance/DOM
media/    — MPDParser (VK-specific, не полный DASH) + FFmpegMerger
download/ — DashDownloader + WorkerBalancer/ConcurrencyGate, atomic .tmp
core/     — VKMediaDownloader (оркестратор) → QualitySelector, urlutils, RetryPolicy, MediaSession
ui/       — Console (normal/advanced/debug)
```

* `VKMediaBrowser.get_mpd() -> MediaSession` — immutable снимок
  `mpd_text/mpd_url/cookies/bases/captured_at/ttl`. Дикт-доступ сохранён для
  совместимости (`session.get("mpd_url")`), но новый код — через атрибуты.
* Retry: `is_retryable()` не ретраит `TypeError/QualityNotAvailableError` etc.
  Pipeline (`--pipeline`) на ретрае рефрешит `MediaSession` если `is_stale` или `401/403`.
* Логи: любой текст, попадающий в файл (`Console.log`), проходит `redact_text()`
  (`token/sig/hash/...` → `***`). Не логируй сырой `mpd_url` без `redact_url()`.

## Что не трогать без обсуждения

* Полный RFC6265 `CookieJar` — текущий `filter_cookies` минимален и так безопасен.
* Перевод WebDriver на `async` — долг помечен `ASYNC210` и ждёт `to_thread`.
* Fake e2e стенд (fake CDN/geckodriver) — дорого для CLI-утилиты.

## Коммиты

Conventional Commits: `type(scope): subject` (`feat/fix/docs/style/refactor/perf/test/build/ci/chore`).
Запусти `pre-commit install` — `ruff` и `pytest` гоняются в CI (Linux + Windows).

## Production заметка

После закрытых P0 (stale MPD) и P1 (redact) проект ~7.8/10 для utility и ~7.2/10
для production long-run. Остальной риск — смена VK плеера → MPD regex.
