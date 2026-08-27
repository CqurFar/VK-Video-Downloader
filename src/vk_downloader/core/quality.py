"""Выбор качества видео/аудио — вынесено из VKMediaDownloader для тестируемости."""

from __future__ import annotations

from vk_downloader.core.errors import QualityNotAvailableError


class QualitySelector:
    """Выбор трека по запросу пользователя с учётом формата вывода."""

    def __init__(self, config) -> None:
        self.config = config

    # Сопоставление кодека трека с семейством по первому компоненту fourcc
    @staticmethod
    def _codec_is(track_codecs: str, families: tuple[str, ...]) -> bool:
        fourcc = track_codecs.split(".")[0].strip().lower()
        return fourcc in families

    # Выбор видео-трека: точное совпадение цели или лучший доступный
    def choose_video(self, videos: list[dict], target: int | str, output_format: str) -> dict:
        if output_format == "webm":
            videos = [
                t for t in videos if self._codec_is(t["codecs"], self.config.media.webm_video)
            ]
            if not videos:
                raise QualityNotAvailableError("WebM requires VP8, VP9 or AV1 video")
        elif output_format == "mkv":
            videos = [t for t in videos if self._codec_is(t["codecs"], self.config.media.mkv_video)]
        elif compatible := [
            t for t in videos if self._codec_is(t["codecs"], self.config.media.mp4_video)
        ]:
            videos = compatible
        videos = sorted(videos, key=lambda t: (t["height"], t["bandwidth"]))
        if not videos:
            raise QualityNotAvailableError("No video track available")
        if target == "best":
            return videos[-1]
        if isinstance(target, int):
            exact = [t for t in videos if t["height"] == target]
            if exact:
                return exact[-1]
            greater = [t for t in videos if t["height"] >= target]
            return min(greater, key=lambda t: t["height"]) if greater else videos[-1]
        raise QualityNotAvailableError(f"Invalid video quality: {target}")

    # Выбор аудио-трека: точное совпадение цели или лучший доступный
    def choose_audio(self, audios: list[dict], target: int | str, output_format: str) -> dict:
        if output_format == "webm":
            audios = [
                t for t in audios if self._codec_is(t["codecs"], self.config.media.webm_audio)
            ]
            if not audios:
                raise QualityNotAvailableError("WebM requires Opus or Vorbis audio")
        elif output_format == "mkv":
            audios = [t for t in audios if self._codec_is(t["codecs"], self.config.media.mkv_audio)]
        elif output_format in {"mp4", "m4a"}:
            if compatible := [
                t for t in audios if self._codec_is(t["codecs"], self.config.media.mp4_audio)
            ]:
                audios = compatible
        audios = sorted(audios, key=lambda t: t["bandwidth"])
        if not audios:
            raise QualityNotAvailableError("No audio track available")
        if target == "best":
            return audios[-1]
        if isinstance(target, int):
            exact = [t for t in audios if t["bandwidth"] // 1000 == target]
            if exact:
                return exact[-1]
            value = target * 1000
            greater = [t for t in audios if t["bandwidth"] >= value]
            return min(greater, key=lambda t: t["bandwidth"]) if greater else audios[-1]
        raise QualityNotAvailableError(f"Invalid audio quality: {target}")

    # Разрешение качества трека; None означает "дорожка не нужна"
    def resolve_quality(
        self, kind: str, tracks: list[dict], requested: str, output_format: str
    ) -> dict | None:
        if requested.lower() == "none":
            return None
        return self._pick(kind, tracks, requested)

    # Разбор значения качества без меню
    @staticmethod
    def _pick(kind: str, tracks: list[dict], requested: str) -> dict:
        if kind == "video":
            tracks = sorted(tracks, key=lambda t: (t["height"], t["bandwidth"]))
            field_name, scale = "height", 1
        else:
            tracks = sorted(tracks, key=lambda t: t["bandwidth"])
            field_name, scale = "bandwidth", 1000
        if requested.lower() != "best":
            try:
                value = int(requested) * scale
            except ValueError as exc:
                raise QualityNotAvailableError(f"Invalid {kind} quality: {requested}") from exc
            exact = [t for t in tracks if t[field_name] == value]
            if exact:
                return exact[-1]
            greater = [t for t in tracks if t[field_name] >= value]
            if greater:
                return min(greater, key=lambda t: t[field_name])
        return tracks[-1]
