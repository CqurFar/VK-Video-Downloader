import contextlib
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from vk_downloader.core.errors import FFmpegMergeError, FFmpegNotFoundError
from vk_downloader.settings import Config
from vk_downloader.ui.console import Console
from vk_downloader.ui.fs_paths import FSPaths


class FFmpegMerger:
    """СКЛЕЙКА ВИДЕО И АУДИО ЧЕРЕЗ FFMPEG С ПРОГРЕССОМ"""

    def __init__(self, config: Config, console: Console):
        self.config = config
        self.console = console

    # Поиск ffmpeg: packages -> PATH, без исключения
    def locate(self) -> str | None:
        base = self.config.paths.ffmpeg_dir
        candidates = [
            base / "ffmpeg.exe",
            base / "ffmpeg",
            base / "bin" / "ffmpeg.exe",
            base / "bin" / "ffmpeg",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate.resolve())
        return shutil.which("ffmpeg")

    # Поиск ffmpeg с исключением при отсутствии
    def find_ffmpeg(self) -> str:
        found = self.locate()
        if not found:
            raise FFmpegNotFoundError(
                f"ffmpeg was not found in {self.config.paths.ffmpeg_dir.resolve()} or PATH"
            )
        return found

    # Длительность медиафайла через ffprobe: сначала рядом с ffmpeg, затем PATH
    def get_duration(self, path: Path) -> float:
        ffprobe = None
        try:
            ffmpeg = self.find_ffmpeg()
            neighbor = Path(ffmpeg).with_name(
                "ffprobe.exe" if ffmpeg.lower().endswith(".exe") else "ffprobe"
            )
            if neighbor.is_file():
                ffprobe = str(neighbor)
        except FFmpegNotFoundError:
            pass
        ffprobe = ffprobe or shutil.which("ffprobe")
        if not ffprobe:
            return 0.0
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                FSPaths.long(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            return float(result.stdout.strip())
        except ValueError:
            return 0.0

    # Кодек трека
    @staticmethod
    def codec_name(track: dict | None) -> str:
        return (track or {}).get("codecs", "") or (track or {}).get("mime", "")

    # Сборка ffmpeg-команды под формат вывода; допустим mux без аудио или без видео
    def build_command(
        self,
        ffmpeg: str,
        video: Path | None,
        audio: Path | None,
        output: Path,
        output_format: str,
        video_track: dict | None,
        audio_track: dict | None,
    ) -> list[str]:
        video_codec, audio_codec = (
            self.codec_name(video_track).lower(),
            self.codec_name(audio_track).lower(),
        )
        loglevel = "info" if self.config.debug else "error"
        target_audio = getattr(self.config.media, "audio_target_format", None)
        command = [ffmpeg, "-y", "-hide_banner", "-loglevel", loglevel]
        if video and audio:
            command += [
                "-i",
                FSPaths.long(video),
                "-i",
                FSPaths.long(audio),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
            ]
            if target_audio:
                # Явный аудиоформат внутри контейнера: видео копией, звук конвертацией
                command += ["-c:v", "copy"]
                command += self.audio_codec_args(target_audio, audio_codec)
                if output_format == "mp4":
                    command += ["-movflags", "+faststart"]
            else:
                if output_format == "webm":
                    webm_ok = self._codec_family(
                        video_codec, self.config.media.webm_video
                    ) and self._codec_family(audio_codec, self.config.media.webm_audio)
                    if not webm_ok:
                        raise RuntimeError("WebM requires VP8/VP9/AV1 video and Opus/Vorbis audio")
                command += ["-c", "copy"]
                if output_format == "mp4":
                    command += ["-movflags", "+faststart"]
        elif video:
            # Mux только видео (audio=none): копирование без перекодирования
            command += ["-i", FSPaths.long(video), "-map", "0:v:0", "-c", "copy"]
            if output_format == "mp4":
                command += ["-movflags", "+faststart"]
        elif audio:
            command += ["-i", FSPaths.long(audio), "-map", "0:a:0"]
            command += self.audio_codec_args(output_format, audio_codec)
        else:
            raise RuntimeError("Nothing to merge: both tracks are missing")
        # Пользовательские аргументы дописываются последними и могут
        # переопределить дефолтные -c/-b (порядок флагов в ffmpeg решает)
        command += list(getattr(self.config.media, "ffmpeg_extra_args", []) or [])
        command += ["-progress", "pipe:1", "-nostats", FSPaths.long(output)]
        return command

    # Проверка кодека по первому компоненту fourcc (см. downloader._codec_is)
    @staticmethod
    def _codec_family(codec: str, families: tuple[str, ...]) -> bool:
        return codec.split(".")[0].strip().lower() in families

    # Кодек аудио при конвертации в аудиоформаты
    @staticmethod
    def audio_codec_args(output_format: str, audio_codec: str) -> list[str]:
        copy_aac = any(c in audio_codec for c in ("aac", "mp4a"))
        codec_map = {
            "mp3": ["-c:a", "libmp3lame"],
            "aac": ["-c:a", "copy"] if copy_aac else ["-c:a", "aac"],
            "m4a": ["-c:a", "copy"] if copy_aac else ["-c:a", "aac"],
            "ogg": ["-c:a", "copy"] if "vorbis" in audio_codec else ["-c:a", "libvorbis"],
            "opus": ["-c:a", "copy"] if "opus" in audio_codec else ["-c:a", "libopus"],
            "flac": ["-c:a", "copy"] if "flac" in audio_codec else ["-c:a", "flac"],
            "wav": ["-c:a", "pcm_s16le"],
        }
        if output_format not in codec_map:
            raise RuntimeError(f"Unsupported audio format: {output_format}")
        return codec_map[output_format]

    # Запуск склейки с разбором прогресса из stdout
    def merge(
        self,
        video: Path | None,
        audio: Path | None,
        output: Path,
        output_format: str,
        video_track: dict | None,
        audio_track: dict | None,
    ) -> None:
        ffmpeg = self.find_ffmpeg()
        duration = max(self.get_duration(video or audio), 0.0)
        # Атомарный вывод: ffmpeg пишет в .tmp, затем replace
        tmp_output = output.with_name(output.name + ".tmp")
        tmp_output.parent.mkdir(parents=True, exist_ok=True)
        command = self.build_command(
            ffmpeg, video, audio, tmp_output, output_format, video_track, audio_track
        )
        started = time.time()
        try:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            # stderr читается в отдельном потоке: иначе заполненный буфер
            # канала блокирует ffmpeg и мы зависаем на чтении stdout
            stderr_chunks: list[str] = []
            if process.stderr:
                threading.Thread(
                    target=lambda: stderr_chunks.append(process.stderr.read()),
                    daemon=True,
                ).start()
            if process.stdout:
                for line in process.stdout:
                    if line.startswith("out_time_ms=") and duration:
                        try:
                            progress = float(line.split("=", 1)[1]) / 1_000_000
                        except ValueError:
                            continue
                        self.console.progress("Merged", progress, duration, started)
            code = process.wait()
        except OSError as exc:
            tmp_output.unlink(missing_ok=True)
            raise FFmpegMergeError(f"ffmpeg failed to start: {exc}") from exc
        elapsed = time.time() - started
        stderr = (stderr_chunks[0] if stderr_chunks else "").strip()
        elapsed = time.time() - started
        self.console.log("")
        self.console.log("FFMPEG")
        self.console.log(f"Command : {' '.join(command)}")
        self.console.log(f"Output  : {output}")
        self.console.log(f"Time    : {elapsed:.3f} sec | Exit code: {code}")
        if stderr.strip():
            self.console.log(stderr.strip())
        if duration:
            self.console.progress("Merged", duration, duration, started)
        if code != 0:
            tmp_output.unlink(missing_ok=True)
            raise FFmpegMergeError(stderr.strip() or "FFmpeg processing failed")
        # fsync + atomic replace
        try:
            with tmp_output.open("rb") as f, contextlib.suppress(OSError):
                os.fsync(f.fileno())
            os.replace(tmp_output, output)
        except Exception as exc:
            tmp_output.unlink(missing_ok=True)
            raise FFmpegMergeError(f"atomic replace failed: {exc}") from exc


# === Пример ===
# from vk_downloader.settings import Config
# from vk_downloader.media.ffmpeg_merger import FFmpegMerger
# from vk_downloader.ui.console import Console
# merger = FFmpegMerger(Config(), Console(Config()))
# merger.merge(video_path, audio_path, out_path, "mkv", video_track, audio_track)
