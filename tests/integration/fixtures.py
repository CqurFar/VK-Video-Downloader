"""Анонимизированные MPD-фикстуры для E2E replay-тестов (без токенов/подписей)."""

from __future__ import annotations

# Анонимизированный статический MPD: SegmentTemplate + Timeline, 2 видео- + 2 аудио-сегмента
REPLAY_MPD = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">
  <Period>
    <AdaptationSet contentType="video" mimeType="video/mp4">
      <SegmentTemplate media="adapt-$Number$.m4s" initialization="init.mp4" timescale="1000">
        <SegmentTimeline><S d="1000" r="1"/></SegmentTimeline>
      </SegmentTemplate>
      <Representation id="v1" codecs="avc1.64001e" bandwidth="1000" width="640" height="360"/>
    </AdaptationSet>
    <AdaptationSet contentType="audio" mimeType="audio/mp4">
      <SegmentTemplate media="audio-$Number$.m4s" initialization="ainit.mp4" timescale="1000">
        <SegmentTimeline><S d="1000" r="1"/></SegmentTimeline>
      </SegmentTemplate>
      <Representation id="a1" codecs="mp4a.40.2" bandwidth="500"/>
    </AdaptationSet>
  </Period>
</MPD>
"""

# Заведомо аномальный MPD: r=200000 материализует >MAX_SEGMENTS сегментов
ABUSIVE_MPD = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">
  <Period>
    <AdaptationSet contentType="video" mimeType="video/mp4">
      <SegmentTemplate media="adapt-$Number$.m4s" initialization="init.mp4" timescale="1000">
        <SegmentTimeline><S d="1000" r="200000"/></SegmentTimeline>
      </SegmentTemplate>
      <Representation id="v1" codecs="avc1.64001e" bandwidth="1000" width="640" height="360"/>
    </AdaptationSet>
  </Period>
</MPD>
"""
