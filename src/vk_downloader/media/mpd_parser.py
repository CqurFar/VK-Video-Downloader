import html
import logging
import xml.etree.ElementTree as ElementTree
from urllib.parse import urljoin

from vk_downloader.core.errors import MPDTooLargeError, MultiPeriodNotSupportedError

# Защита от DoS: аномально большой r= в SegmentTimeline материализует миллионы сегментов
MAX_SEGMENTS = 100_000

logger = logging.getLogger(__name__)


class MPDParser:
    """VK-specific DASH MPD parser: BaseURL chains, SegmentTemplate/Timeline, $Number$/$Time$.

    Поддерживает диалект, который фактически отдаёт VK Video (имена с учётом
    namespace, наследование BaseURL/SegmentTemplate, SegmentTimeline с r).
    Не претендует на полный DASH — SegmentBase/SegmentList, ContentProtection
    и другие редкие конструкции не покрыты.
    """

    # Совместимость: старое имяGeneric → VK-specific alias
    VKMPDParser = None  # заполняется после определения класса

    # Разбор MPD XML: треки с учётом наследования BaseURL и SegmentTemplate
    @classmethod
    def parse(cls, xml_text: str, mpd_url: str) -> tuple[list[dict], list[dict]]:
        stripped = xml_text.lstrip("\ufeff \t\r\n")
        lowered = stripped[:1024].lower()
        if "<!doctype" in lowered or "<!entity" in lowered:
            raise RuntimeError("Invalid MPD: DTD/entity declarations are not allowed")
        try:
            root = ElementTree.fromstring(stripped)
        except ElementTree.ParseError as exc:
            snippet = xml_text.lstrip("\ufeff \t\r\n")[:80]
            raise RuntimeError(f"Invalid MPD: {exc}; body starts with: {snippet!r}") from exc
        if cls.local_name(root.tag) != "MPD":
            raise RuntimeError(f"Invalid root tag: {root.tag}")
        videos, audios = [], []
        mpd_base = cls.direct_base_url(root)
        root_base = urljoin(mpd_url, mpd_base) if mpd_base else mpd_url
        periods = cls.children_by_name(root, "Period")
        if len(periods) > 1:
            raise MultiPeriodNotSupportedError(
                f"MPD has {len(periods)} periods; multi-period manifests are not supported "
                "(a single Period is expected for VK)"
            )
        mpd_duration = cls.parse_iso8601_duration(root.attrib.get("mediaPresentationDuration"))
        for period in periods:
            period_duration = cls.parse_iso8601_duration(period.attrib.get("duration"))
            if period_duration is None and len(periods) == 1:
                period_duration = mpd_duration
            period_base = cls.direct_base_url(period)
            period_full_base = urljoin(root_base, period_base) if period_base else root_base
            for adaptation in cls.children_by_name(period, "AdaptationSet"):
                mime = adaptation.attrib.get("mimeType", "")
                content_type = adaptation.attrib.get("contentType", "")
                adaptation_base = cls.direct_base_url(adaptation)
                full_base = (
                    urljoin(period_full_base, adaptation_base)
                    if adaptation_base
                    else period_full_base
                )
                template = cls.child_by_name(adaptation, "SegmentTemplate")
                inherited_template = dict(template.attrib) if template is not None else {}
                inherited_timeline = (
                    cls.parse_segment_timeline(template, period_duration)
                    if template is not None
                    else []
                )
                for rep in cls.children_by_name(adaptation, "Representation"):
                    track = cls.build_track(
                        rep,
                        mime,
                        content_type,
                        inherited_template,
                        inherited_timeline,
                        full_base,
                        mpd_url,
                        period_duration,
                    )
                    if track is None:
                        continue
                    if cls.is_video(track, content_type):
                        videos.append(track)
                    elif cls.is_audio(track, content_type):
                        audios.append(track)
        videos, audios = cls.dedupe(videos), cls.dedupe(audios)
        if not videos and not audios:
            raise RuntimeError("No media tracks found in MPD")
        return videos, audios

    # Сборка словаря трека из Representation
    @classmethod
    def build_track(
        cls,
        rep,
        mime: str,
        content_type: str,
        inherited_template: dict,
        inherited_timeline: list[dict],
        base: str,
        mpd_url: str,
        period_duration: float | None = None,
    ) -> dict | None:
        template = cls.child_by_name(rep, "SegmentTemplate")
        template_data = dict(inherited_template)
        timeline = list(inherited_timeline)
        if template is not None:
            template_data.update(template.attrib)
            timeline = cls.parse_segment_timeline(template, period_duration)
        # База трека: собственный BaseURL, иначе унаследованная цепочка, иначе папка MPD
        rep_base = cls.direct_base_url(rep)
        track_base = (
            urljoin(base, rep_base) if rep_base else (base or mpd_url.rsplit("/", 1)[0] + "/")
        )
        return {
            "id": rep.attrib.get("id", ""),
            "bandwidth": cls.to_int(rep.attrib.get("bandwidth")),
            "height": cls.to_int(rep.attrib.get("height")),
            "width": cls.to_int(rep.attrib.get("width")),
            "codecs": rep.attrib.get("codecs", ""),
            "quality": rep.attrib.get("quality", ""),
            "mime": rep.attrib.get("mimeType", mime) or "",
            "url": track_base,
            "segment_template": template_data,
            "segment_timeline": timeline,
        }

    # Признак видео-трека
    @staticmethod
    def is_video(track: dict, content_type: str) -> bool:
        return track["mime"].startswith("video/") or content_type == "video" or track["height"] > 0

    # Признак аудио-трека
    @staticmethod
    def is_audio(track: dict, content_type: str) -> bool:
        return (
            track["mime"].startswith("audio/")
            or content_type == "audio"
            or ("mp4a" in track["codecs"].lower() and track["height"] == 0)
        )

    # Прямой BaseURL элемента с раскрытием HTML-сущностей
    @staticmethod
    def direct_base_url(element) -> str | None:
        base = MPDParser.child_by_name(element, "BaseURL")
        if base is None:
            return None
        value = html.unescape((base.text or "").strip())
        return value or None

    # Раскрутка SegmentTemplate + SegmentTimeline в список сегментов
    # period_duration — длительность периода в секундах (MPD/Period), если известна
    @classmethod
    def parse_segment_timeline(cls, template, period_duration: float | None = None) -> list[dict]:
        if template is None:
            return []
        timeline = cls.child_by_name(template, "SegmentTimeline")
        if timeline is None:
            return []
        start_number = cls.to_int(template.attrib.get("startNumber"), 1)
        timescale = max(cls.to_int(template.attrib.get("timescale"), 1), 1)
        items = cls.children_by_name(timeline, "S")
        segments, number, current_time = [], start_number, 0
        for index, segment in enumerate(items):
            if "t" in segment.attrib:
                current_time = cls.to_int(segment.attrib.get("t"), current_time)
            duration = cls.to_int(segment.attrib.get("d"))
            if duration <= 0:
                continue
            repeat = cls.to_int(segment.attrib.get("r"))
            count = (
                repeat + 1
                if repeat >= 0
                else cls.negative_repeat_count(
                    items, index, current_time, duration, timescale, period_duration
                )
            )
            if len(segments) + count > MAX_SEGMENTS:
                raise MPDTooLargeError(
                    f"MPD declares {len(segments) + count} segments (limit {MAX_SEGMENTS}); "
                    "rejecting to avoid memory exhaustion"
                )
            for _ in range(count):
                segments.append(
                    {
                        "number": number,
                        "time": current_time,
                        "duration": duration,
                        "timescale": timescale,
                    }
                )
                number += 1
                current_time += duration
        return segments

    # Число повторов при отрицательном r: до следующего явного t или до конца периода
    @classmethod
    def negative_repeat_count(
        cls,
        items: list,
        index: int,
        current_time: int,
        duration: int,
        timescale: int = 1,
        period_duration: float | None = None,
    ) -> int:
        for next_segment in items[index + 1 :]:
            if "t" not in next_segment.attrib:
                continue
            next_time = cls.to_int(next_segment.attrib.get("t"))
            return max((next_time - current_time) // duration, 1) if next_time > current_time else 1
        # Последний S с r=-1: пробуем оценить по длительности периода
        if period_duration is not None and duration > 0 and timescale > 0:
            try:
                remaining = period_duration * timescale - current_time
                if remaining > 0:
                    return max(int(remaining // duration), 1)
            except Exception:
                pass
        logger.warning(
            "r=-1 on last S without period duration: count unknown, emitting 1 segment "
            "(t=%s d=%s timescale=%s period_duration=%r) — file may be truncated",
            current_time,
            duration,
            timescale,
            period_duration,
        )
        return 1

    @staticmethod
    def parse_iso8601_duration(value: str | None) -> float | None:
        """Парсит ISO8601 duration PT... в секунды, возвращает None если не удалось."""
        if not value or not isinstance(value, str):
            return None
        value = value.strip()
        if not value.startswith("PT"):
            return None
        # PT[H]H[M]M[S]S, например PT1H2M3.5S
        import re

        m = re.match(r"^PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?$", value)
        if not m:
            return None
        hours = float(m.group(1) or 0)
        minutes = float(m.group(2) or 0)
        seconds = float(m.group(3) or 0)
        total = hours * 3600 + minutes * 60 + seconds
        return total if total > 0 else None

    # Удаление дублей треков по ключевым полям
    @staticmethod
    def dedupe(tracks: list[dict]) -> list[dict]:
        seen, result = set(), []
        for track in tracks:
            key = (
                track["width"],
                track["height"],
                track["bandwidth"],
                track["codecs"],
                track["url"],
            )
            if key not in seen:
                seen.add(key)
                result.append(track)
        return result

    # Имя XML-тега без namespace
    @staticmethod
    def local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    # Первый дочерний тег по имени
    @classmethod
    def child_by_name(cls, parent, name: str):
        return next((child for child in list(parent) if cls.local_name(child.tag) == name), None)

    # Все дочерние теги по имени
    @classmethod
    def children_by_name(cls, parent, name: str) -> list:
        return [child for child in list(parent) if cls.local_name(child.tag) == name]

    # Безопасное преобразование строки в int
    @staticmethod
    def to_int(value, default: int = 0) -> int:
        try:
            return int(value or default)
        except (TypeError, ValueError):
            return default


# VK-specific alias — явное имя для нового кода, старое MPDParser оставлено для совместимости
VKMPDParser = MPDParser
MPDParser.VKMPDParser = MPDParser  # type: ignore[attr-defined]


# === Пример ===
# from vk_downloader.media.mpd_parser import MPDParser  # или VKMPDParser
# videos, audios = MPDParser.parse(mpd_xml_text, mpd_url)
