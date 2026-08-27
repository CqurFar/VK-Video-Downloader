"""Тесты типизированной модели треков (media.tracks)."""

from vk_downloader.media.mpd_parser import MPDParser
from vk_downloader.media.tracks import AudioTrack, Segment, VideoTrack


def test_video_track_typed_from_parser():
    mpd = """<?xml version="1.0"?>
    <MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">
      <Period>
        <AdaptationSet contentType="video" mimeType="video/mp4">
          <SegmentTemplate media="seg-$Number$.m4s" initialization="init.mp4" timescale="1000">
            <SegmentTimeline><S d="1000" r="1"/></SegmentTimeline>
          </SegmentTemplate>
          <Representation id="v1" codecs="avc1.64001e" bandwidth="1000" width="640" height="360"/>
        </AdaptationSet>
      </Period>
    </MPD>
    """
    videos, _ = MPDParser.parse(mpd, "https://cdn.vkuser.net/manifest.mpd")
    track = VideoTrack.from_dict(videos[0])
    assert isinstance(track, VideoTrack)
    assert track.width == 640 and track.height == 360
    assert track.codecs == "avc1.64001e"
    # r=1 -> 2 сегмента
    assert len(track.segments) == 2
    assert all(isinstance(s, Segment) for s in track.segments)
    assert track.segments[0].number == 1


def test_audio_track_typed_from_parser():
    mpd = """<?xml version="1.0"?>
    <MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">
      <Period>
        <AdaptationSet contentType="audio" mimeType="audio/mp4">
          <SegmentTemplate media="a-$Number$.m4s" timescale="1000">
            <SegmentTimeline><S d="1000" r="0"/></SegmentTimeline>
          </SegmentTemplate>
          <Representation id="a1" codecs="mp4a.40.2" bandwidth="500"/>
        </AdaptationSet>
      </Period>
    </MPD>
    """
    _, audios = MPDParser.parse(mpd, "https://cdn.vkuser.net/manifest.mpd")
    track = AudioTrack.from_dict(audios[0])
    assert isinstance(track, AudioTrack)
    assert len(track.segments) == 1
