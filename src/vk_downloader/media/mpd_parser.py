import html
from urllib.parse import urljoin
import xml.etree.ElementTree as ElementTree


class MPDParser:
    """РАЗБОР DASH-МАНИФЕСТА В СПИСКИ ВИДЕО И АУДИО ТРЕКОВ"""

    # Разбор MPD XML: треки с учётом наследования BaseURL и SegmentTemplate
    @classmethod
    def parse(cls, xml_text: str, mpd_url: str) -> tuple[list[dict], list[dict]]:
        try:
            root = ElementTree.fromstring(xml_text.lstrip("\ufeff \t\r\n"))
        except ElementTree.ParseError as exc:
            snippet = xml_text.lstrip("\ufeff \t\r\n")[:80]
            raise RuntimeError(f"Invalid MPD: {exc}; body starts with: {snippet!r}") from exc
        if cls.local_name(root.tag) != "MPD":
            raise RuntimeError(f"Invalid root tag: {root.tag}")
        videos, audios = [], []
        mpd_base = cls.direct_base_url(root)
        root_base = urljoin(mpd_url, mpd_base) if mpd_base else mpd_url
        for period in cls.children_by_name(root, "Period"):
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
                    cls.parse_segment_timeline(template) if template is not None else []
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
    ) -> dict | None:
        template = cls.child_by_name(rep, "SegmentTemplate")
        template_data = dict(inherited_template)
        timeline = list(inherited_timeline)
        if template is not None:
            template_data.update(template.attrib)
            timeline = cls.parse_segment_timeline(template)
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
    @classmethod
    def parse_segment_timeline(cls, template) -> list[dict]:
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
                else cls.negative_repeat_count(items, index, current_time, duration)
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

    # Число повторов при отрицательном r: до следующего явного t
    @classmethod
    def negative_repeat_count(
        cls, items: list, index: int, current_time: int, duration: int
    ) -> int:
        for next_segment in items[index + 1 :]:
            if "t" not in next_segment.attrib:
                continue
            next_time = cls.to_int(next_segment.attrib.get("t"))
            return max((next_time - current_time) // duration, 1) if next_time > current_time else 1
        return 1

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


# === Пример ===
# from vk_downloader.media.mpd_parser import MPDParser
# videos, audios = MPDParser.parse(mpd_xml_text, mpd_url)
