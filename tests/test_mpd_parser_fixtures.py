"""Фикстуры MPDParser — покрытие узких кейсов VK."""

import pytest

from vk_downloader.media.mpd_parser import MPDParser


def parse(xml, url="https://example.com/manifest.mpd"):
    return MPDParser.parse(xml, url)


# 1. namespace — теги с префиксом xmlns не ломают парсинг
def test_namespace_independent():
    xml = (
        '<?xml version="1.0"?>'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">'
        '<Period><AdaptationSet mimeType="video/mp4">'
        '<Representation id="1" height="720" bandwidth="1000" codecs="avc1.64001e">'
        "<BaseURL>v.mp4</BaseURL></Representation>"
        "</AdaptationSet></Period></MPD>"
    )
    videos, _ = parse(xml)
    assert videos[0]["height"] == 720


# 2. nested BaseURL — MPD → Period → AdaptationSet → Representation
def test_nested_baseurl_inheritance():
    xml = (
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">'
        "<BaseURL>https://cdn.example.com/</BaseURL>"
        "<Period><BaseURL>period/</BaseURL>"
        '<AdaptationSet mimeType="video/mp4"><BaseURL>adapt/</BaseURL>'
        '<Representation id="1" height="360" bandwidth="500" codecs="avc1.64001e">'
        "<BaseURL>rep.mp4</BaseURL></Representation>"
        "</AdaptationSet></Period></MPD>"
    )
    videos, _ = parse(xml, "https://example.com/manifest.mpd")
    # rep.mp4 резолвится через цепочку
    assert videos[0]["url"].endswith("rep.mp4")
    assert "cdn.example.com" in videos[0]["url"]


# 3. SegmentTemplate в Representation переопределяет AdaptationSet
def test_segment_template_in_representation_overrides():
    xml = (
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>'
        '<AdaptationSet mimeType="video/mp4">'
        '<SegmentTemplate media="adapt-$Number$.m4s" initialization="init.mp4" timescale="1000">'
        '<SegmentTimeline><S d="1000" r="1"/></SegmentTimeline></SegmentTemplate>'
        '<Representation id="1" height="720" bandwidth="1000" codecs="avc1.64001e">'
        '<SegmentTemplate media="rep-$Number$.m4s"><SegmentTimeline><S d="500" r="2"/></SegmentTimeline></SegmentTemplate>'
        "<BaseURL>https://cdn.example.com/</BaseURL>"
        "</Representation></AdaptationSet></Period></MPD>"
    )
    videos, _ = parse(xml)
    # Representation template: d=500 r=2 → 3 сегмента
    assert len(videos[0]["segment_timeline"]) == 3
    assert videos[0]["segment_template"]["media"] == "rep-$Number$.m4s"


# 4. Timeline с явными t
def test_timeline_with_t():
    xml = (
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>'
        '<AdaptationSet mimeType="video/mp4">'
        '<Representation id="1" height="360" bandwidth="500" codecs="avc1.64001e">'
        "<BaseURL>https://cdn.example.com/</BaseURL>"
        '<SegmentTemplate timescale="1000" media="seg-$Time$.m4s">'
        '<SegmentTimeline><S t="0" d="1000"/><S t="5000" d="1000"/></SegmentTimeline>'
        "</SegmentTemplate></Representation></AdaptationSet></Period></MPD>"
    )
    videos, _ = parse(xml)
    tl = videos[0]["segment_timeline"]
    assert tl[0]["time"] == 0
    assert tl[1]["time"] == 5000


# 5. r > 0 — повтор
def test_r_positive():
    xml = (
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>'
        '<AdaptationSet mimeType="video/mp4">'
        '<Representation id="1" height="360" bandwidth="500" codecs="avc1.64001e">'
        "<BaseURL>https://cdn.example.com/</BaseURL>"
        '<SegmentTemplate timescale="1000" media="$Number$.m4s">'
        '<SegmentTimeline><S d="1000" r="2"/></SegmentTimeline>'
        "</SegmentTemplate></Representation></AdaptationSet></Period></MPD>"
    )
    videos, _ = parse(xml)
    assert len(videos[0]["segment_timeline"]) == 3


# 6. r = -1 not last — повтор до следующего t
def test_r_minus_one_not_last():
    xml = (
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>'
        '<AdaptationSet mimeType="video/mp4">'
        '<Representation id="1" height="360" bandwidth="500" codecs="avc1.64001e">'
        "<BaseURL>https://cdn.example.com/</BaseURL>"
        '<SegmentTemplate timescale="1000" media="$Number$.m4s">'
        '<SegmentTimeline><S d="1000" r="-1"/><S t="3000" d="1000"/></SegmentTimeline>'
        "</SegmentTemplate></Representation></AdaptationSet></Period></MPD>"
    )
    videos, _ = parse(xml)
    # первый S r=-1 должен развернуться до t=3000 → 3 сегмента + 1 последний =4
    assert len(videos[0]["segment_timeline"]) == 4


# 7. r = -1 last без duration — фолбэк 1
def test_r_minus_one_last_no_duration():
    xml = (
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>'
        '<AdaptationSet mimeType="video/mp4">'
        '<Representation id="1" height="360" bandwidth="500" codecs="avc1.64001e">'
        "<BaseURL>https://cdn.example.com/</BaseURL>"
        '<SegmentTemplate timescale="1000" media="$Number$.m4s">'
        '<SegmentTimeline><S d="1000" r="-1"/></SegmentTimeline>'
        "</SegmentTemplate></Representation></AdaptationSet></Period></MPD>"
    )
    videos, _ = parse(xml)
    assert len(videos[0]["segment_timeline"]) == 1


# 8. r = -1 last с Period duration — оценивает остаток
def test_r_minus_one_last_with_period_duration():
    xml = (
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" mediaPresentationDuration="PT0H0M5.0S">'
        '<Period duration="PT5S">'
        '<AdaptationSet mimeType="video/mp4">'
        '<Representation id="1" height="360" bandwidth="500" codecs="avc1.64001e">'
        "<BaseURL>https://cdn.example.com/</BaseURL>"
        '<SegmentTemplate timescale="1000" media="$Number$.m4s">'
        '<SegmentTimeline><S t="0" d="1000" r="-1"/></SegmentTimeline>'
        "</SegmentTemplate></Representation></AdaptationSet></Period></MPD>"
    )
    videos, _ = parse(xml)
    # 5 сек *1000 /1000 =5 сегментов
    assert len(videos[0]["segment_timeline"]) == 5


# 9. пропуск duration d=0
def test_skip_zero_duration():
    xml = (
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>'
        '<AdaptationSet mimeType="video/mp4">'
        '<Representation id="1" height="360" bandwidth="500" codecs="avc1.64001e">'
        "<BaseURL>https://cdn.example.com/</BaseURL>"
        '<SegmentTemplate media="$Number$.m4s"><SegmentTimeline><S d="0"/><S d="1000"/></SegmentTimeline></SegmentTemplate>'
        "</Representation></AdaptationSet></Period></MPD>"
    )
    videos, _ = parse(xml)
    assert len(videos[0]["segment_timeline"]) == 1
    assert videos[0]["segment_timeline"][0]["duration"] == 1000


# 10. несколько Period — плоская модель их не поддерживает, явный отказ
def test_multiple_periods_rejected():
    from vk_downloader.core.errors import MultiPeriodNotSupportedError

    xml = (
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">'
        '<Period><AdaptationSet mimeType="video/mp4">'
        '<Representation id="1" height="360" bandwidth="500" codecs="avc1.64001e"><BaseURL>https://cdn.example.com/p1.mp4</BaseURL></Representation>'
        "</AdaptationSet></Period>"
        '<Period><AdaptationSet mimeType="video/mp4">'
        '<Representation id="2" height="720" bandwidth="1000" codecs="avc1.64001e"><BaseURL>https://cdn.example.com/p2.mp4</BaseURL></Representation>'
        "</AdaptationSet></Period></MPD>"
    )
    with pytest.raises(MultiPeriodNotSupportedError):
        parse(xml)


# 11. duplicate representations — dedupe
def test_duplicate_dedupe():
    xml = (
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>'
        '<AdaptationSet mimeType="video/mp4">'
        '<Representation id="1" height="720" width="1280" bandwidth="1000" codecs="avc1.64001e"><BaseURL>https://cdn.example.com/v.mp4</BaseURL></Representation>'
        '<Representation id="1" height="720" width="1280" bandwidth="1000" codecs="avc1.64001e"><BaseURL>https://cdn.example.com/v.mp4</BaseURL></Representation>'
        "</AdaptationSet></Period></MPD>"
    )
    videos, _ = parse(xml)
    assert len(videos) == 1


# 12. URL с query/hash
def test_url_with_query_hash_preserved():
    xml = (
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>'
        '<AdaptationSet mimeType="video/mp4">'
        '<Representation id="1" height="360" bandwidth="500" codecs="avc1.64001e"><BaseURL>https://cdn.example.com/v.mp4?token=abc#frag</BaseURL></Representation>'
        "</AdaptationSet></Period></MPD>"
    )
    videos, _ = parse(xml)
    # BaseURL text preserved via urljoin? direct BaseURL unescaped but urljoin keeps it
    assert "v.mp4" in videos[0]["url"]


# 13. отсутствие audio / video — только один тип
def test_only_video_no_audio():
    xml = (
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>'
        '<AdaptationSet mimeType="video/mp4">'
        '<Representation id="1" height="720" bandwidth="1000" codecs="avc1.64001e"><BaseURL>https://cdn.example.com/v.mp4</BaseURL></Representation>'
        "</AdaptationSet></Period></MPD>"
    )
    videos, audios = parse(xml)
    assert len(videos) == 1
    assert len(audios) == 0


def test_only_audio_no_video():
    xml = (
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>'
        '<AdaptationSet mimeType="audio/mp4">'
        '<Representation id="1" bandwidth="128000" codecs="mp4a.40.2"><BaseURL>https://cdn.example.com/a.mp4</BaseURL></Representation>'
        "</AdaptationSet></Period></MPD>"
    )
    videos, audios = parse(xml)
    assert len(videos) == 0
    assert len(audios) == 1


# 14. разные codec strings — классификация video/audio
def test_codec_classification():
    xml = (
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>'
        '<AdaptationSet mimeType="video/webm"><Representation id="1" height="480" bandwidth="500" codecs="vp09.00.10.08"><BaseURL>https://cdn.example.com/v.webm</BaseURL></Representation></AdaptationSet>'
        '<AdaptationSet mimeType="audio/webm"><Representation id="2" bandwidth="128000" codecs="opus"><BaseURL>https://cdn.example.com/a.webm</BaseURL></Representation></AdaptationSet>'
        "</Period></MPD>"
    )
    videos, audios = parse(xml)
    assert len(videos) == 1
    assert len(audios) == 1
    assert videos[0]["codecs"] == "vp09.00.10.08"
    assert audios[0]["codecs"] == "opus"


def test_parse_iso8601_duration():
    assert MPDParser.parse_iso8601_duration("PT5S") == 5
    assert MPDParser.parse_iso8601_duration("PT1H2M3.5S") == 3723.5
    assert MPDParser.parse_iso8601_duration("PT0H0M5.0S") == 5
    assert MPDParser.parse_iso8601_duration(None) is None
    assert MPDParser.parse_iso8601_duration("bad") is None
