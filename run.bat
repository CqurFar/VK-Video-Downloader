@echo off
cd /d "%~dp0"

set "URLS="
set "URL_FILE="
set "PLAYLIST="
set "PLAYLIST_FILE="
set "VIDEO=best"
set "AUDIO=best"
set "FORMAT=mkv"
set "MODE=normal"

set "MODE_FLAG="
if /I not "%MODE%"=="normal" set "MODE_FLAG=--mode %MODE%"

if not "%PLAYLIST_FILE%"=="" (
    uv run vk-dl %MODE_FLAG% --playlist-file "%PLAYLIST_FILE%" --video "%VIDEO%" --audio "%AUDIO%" --format "%FORMAT%"
) else if not "%PLAYLIST%"=="" (
    uv run vk-dl %MODE_FLAG% --playlist "%PLAYLIST%" --video "%VIDEO%" --audio "%AUDIO%" --format "%FORMAT%"
) else if defined URL_FILE (
    uv run vk-dl %MODE_FLAG% --file "%URL_FILE%" --video "%VIDEO%" --audio "%AUDIO%" --format "%FORMAT%"
) else (
    uv run vk-dl %MODE_FLAG% "%URLS%" --video "%VIDEO%" --audio "%AUDIO%" --format "%FORMAT%"
)
pause
