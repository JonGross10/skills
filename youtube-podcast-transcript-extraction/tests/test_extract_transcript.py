"""Unit tests for the YouTube transcript extractor.

Network and yt-dlp calls are stubbed; the caption-parsing and formatting
logic is exercised directly.
"""
import json

import pytest


def json3(lang_map, auto_map=None):
    info = {"subtitles": lang_map}
    if auto_map is not None:
        info["automatic_captions"] = auto_map
    return info


def track(url, ext="json3"):
    return [{"ext": ext, "url": url}]


class TestPickCaptionTrack:
    def test_manual_captions_preferred_over_auto(self, et):
        info = json3({"en": track("manual-url")}, {"en": track("auto-url")})
        assert et.pick_caption_track(info) == ("manual-url", "manual")

    def test_falls_back_to_auto_generated(self, et):
        info = json3({}, {"en": track("auto-url")})
        assert et.pick_caption_track(info) == ("auto-url", "auto-generated")

    def test_auto_translations_are_skipped(self, et):
        info = json3({}, {"en-fr": track("translated-url")})
        assert et.pick_caption_track(info) == (None, None)

    @pytest.mark.parametrize("lang", ["en-orig", "en-US", "en-GB"])
    def test_allowed_auto_variants(self, et, lang):
        info = json3({}, {lang: track("ok-url")})
        assert et.pick_caption_track(info) == ("ok-url", "auto-generated")

    def test_manual_regional_variant_is_accepted(self, et):
        info = json3({"en-CA": track("ca-url")})
        assert et.pick_caption_track(info) == ("ca-url", "manual")

    def test_non_english_languages_ignored(self, et):
        info = json3({"fr": track("fr-url"), "de": track("de-url")})
        assert et.pick_caption_track(info) == (None, None)

    def test_non_json3_formats_ignored(self, et):
        info = json3({"en": track("vtt-url", ext="vtt")})
        assert et.pick_caption_track(info) == (None, None)

    def test_no_caption_data_at_all(self, et):
        assert et.pick_caption_track({}) == (None, None)


class TestEventsToLines:
    def test_segments_are_joined_and_whitespace_collapsed(self, et):
        events = [{"tStartMs": 1500, "segs": [{"utf8": "hello "}, {"utf8": "  there"}]}]
        assert et.events_to_lines(events) == [(1.5, "hello there")]

    def test_consecutive_duplicates_deduped(self, et):
        events = [
            {"tStartMs": 0, "segs": [{"utf8": "same"}]},
            {"tStartMs": 1000, "segs": [{"utf8": "same"}]},
            {"tStartMs": 2000, "segs": [{"utf8": "different"}]},
        ]
        assert et.events_to_lines(events) == [(0.0, "same"), (2.0, "different")]

    def test_non_consecutive_repeat_is_kept(self, et):
        events = [
            {"tStartMs": 0, "segs": [{"utf8": "a"}]},
            {"tStartMs": 1000, "segs": [{"utf8": "b"}]},
            {"tStartMs": 2000, "segs": [{"utf8": "a"}]},
        ]
        assert [t for _, t in et.events_to_lines(events)] == ["a", "b", "a"]

    def test_events_without_segs_or_timestamp_skipped(self, et):
        events = [
            {"tStartMs": 0},
            {"segs": [{"utf8": "no timestamp"}]},
            {"tStartMs": 3000, "segs": [{"utf8": "kept"}]},
        ]
        assert et.events_to_lines(events) == [(3.0, "kept")]

    def test_whitespace_only_events_skipped(self, et):
        events = [{"tStartMs": 0, "segs": [{"utf8": " \n "}]}]
        assert et.events_to_lines(events) == []

    def test_segments_missing_utf8_key_tolerated(self, et):
        events = [{"tStartMs": 0, "segs": [{}, {"utf8": "text"}]}]
        assert et.events_to_lines(events) == [(0.0, "text")]


class TestFmtTs:
    @pytest.mark.parametrize("seconds,expected", [
        (0, "0:00"),
        (9, "0:09"),
        (75, "1:15"),
        (599, "9:59"),
        (3600, "1:00:00"),
        (3725, "1:02:05"),
        (37230, "10:20:30"),
    ])
    def test_formats(self, et, seconds, expected):
        assert et.fmt_ts(seconds) == expected

    def test_fractional_seconds_truncated(self, et):
        assert et.fmt_ts(75.9) == "1:15"


class TestBuildBody:
    def test_single_paragraph_gets_one_timestamp(self, et):
        lines = [(0.0, "first"), (5.0, "second")]
        assert et.build_body(lines, None) == "**[0:00]** first second"

    def test_new_paragraph_after_time_limit(self, et):
        lines = [(0.0, "a"), (float(et.PARA_MAX_SECONDS), "b"), (200.0, "c")]
        body = et.build_body(lines, [])
        assert body == "**[0:00]** a b\n\n**[3:20]** c"

    def test_new_paragraph_after_char_limit(self, et):
        lines = [(0.0, "x" * et.PARA_MAX_CHARS), (5.0, "next")]
        assert et.build_body(lines, []).split("\n\n")[1] == "**[0:05]** next"

    def test_chapter_headings_inserted_before_their_lines(self, et):
        lines = [(0.0, "intro"), (100.0, "part two")]
        chapters = [{"start_time": 90, "title": "Second Part"}]
        assert et.build_body(lines, chapters) == (
            "**[0:00]** intro\n\n## Second Part\n\n**[1:40]** part two"
        )

    def test_chapters_sorted_by_start_time(self, et):
        lines = [(0.0, "a"), (100.0, "b"), (200.0, "c")]
        chapters = [{"start_time": 150, "title": "Later"}, {"start_time": 50, "title": "Earlier"}]
        body = et.build_body(lines, chapters)
        assert body.index("## Earlier") < body.index("## Later")

    def test_untitled_chapter_falls_back_to_generic_heading(self, et):
        body = et.build_body([(10.0, "text")], [{"start_time": 0}])
        assert body.startswith("## Chapter")

    def test_chapter_without_start_time_treated_as_zero(self, et):
        body = et.build_body([(10.0, "text")], [{"title": "Cold Open"}])
        assert body == "## Cold Open\n\n**[0:10]** text"

    def test_no_lines_yields_empty_body(self, et):
        assert et.build_body([], None) == ""


class TestFetchMetadata:
    def test_returns_parsed_json_on_success(self, et, monkeypatch):
        def fake_run(cmd, **kwargs):
            assert cmd[:3] == ["yt-dlp", "-J", "--skip-download"]
            return type("R", (), {"returncode": 0, "stdout": '{"title": "T"}', "stderr": ""})

        monkeypatch.setattr(et.subprocess, "run", fake_run)
        assert et.fetch_metadata("https://youtu.be/abc") == {"title": "T"}

    def test_nonzero_exit_dies_with_json_error(self, et, monkeypatch, capsys):
        monkeypatch.setattr(
            et.subprocess, "run",
            lambda cmd, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": "boom"}),
        )
        with pytest.raises(SystemExit) as exc:
            et.fetch_metadata("https://youtu.be/abc")
        assert exc.value.code == 1
        assert "boom" in json.loads(capsys.readouterr().out)["error"]


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestFetchCaptionEvents:
    def test_parses_events_and_sends_user_agent(self, et, monkeypatch):
        payload = json.dumps({"events": [{"tStartMs": 0, "segs": [{"utf8": "hi"}]}]})
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["headers"] = req.headers
            return FakeResponse(payload)

        monkeypatch.setattr(et.urllib.request, "urlopen", fake_urlopen)
        assert et.fetch_caption_events("https://caption-url") == [
            {"tStartMs": 0, "segs": [{"utf8": "hi"}]}
        ]
        assert "Mozilla" in captured["headers"]["User-agent"]

    def test_missing_events_key_yields_empty_list(self, et, monkeypatch):
        monkeypatch.setattr(
            et.urllib.request, "urlopen", lambda req, timeout=None: FakeResponse("{}")
        )
        assert et.fetch_caption_events("https://caption-url") == []


class TestDie:
    def test_prints_json_error_and_exits_one(self, et, capsys):
        with pytest.raises(SystemExit) as exc:
            et.die("no captions")
        assert exc.value.code == 1
        assert json.loads(capsys.readouterr().out) == {"error": "no captions"}


class TestMain:
    """Drives main() with metadata and captions stubbed out."""

    INFO = {
        "title": "Building AI Agents That Work",
        "webpage_url": "https://www.youtube.com/watch?v=abc",
        "channel": "Some Show",
        "upload_date": "20260317",
        "duration": 3725,
        "chapters": [{"start_time": 60, "title": "Setup"}],
        "description": "A long description.",
        "subtitles": {"en": [{"ext": "json3", "url": "caption-url"}]},
    }

    EVENTS = [
        {"tStartMs": 0, "segs": [{"utf8": "welcome to the show"}]},
        {"tStartMs": 70000, "segs": [{"utf8": "now the real content"}]},
    ]

    def _run(self, et, monkeypatch, capsys, out_path, info=None, events=None):
        monkeypatch.setattr(et, "fetch_metadata", lambda url: info if info is not None else self.INFO)
        monkeypatch.setattr(
            et, "fetch_caption_events",
            lambda url: self.EVENTS if events is None else events,
        )
        monkeypatch.setattr(et.sys, "argv", ["extract_transcript.py", "https://youtu.be/abc",
                                             "--out", str(out_path)])
        et.main()
        return json.loads(capsys.readouterr().out)

    def test_writes_note_with_frontmatter_and_body(self, et, monkeypatch, capsys, tmp_path):
        out = tmp_path / "note.md"
        self._run(et, monkeypatch, capsys, out)
        note = out.read_text(encoding="utf-8")
        assert note.startswith("---\n")
        assert 'title: "Building AI Agents That Work"' in note
        assert "source: https://www.youtube.com/watch?v=abc" in note
        assert 'show: "Some Show"' in note
        assert "date: 2026-03-17" in note
        assert "duration: 1:02:05" in note
        assert "captions: manual" in note
        assert "# Building AI Agents That Work" in note
        assert "## Setup" in note
        assert "welcome to the show" in note

    def test_summary_json_fields(self, et, monkeypatch, capsys, tmp_path):
        out = tmp_path / "note.md"
        summary = self._run(et, monkeypatch, capsys, out)
        assert summary["title"] == "Building AI Agents That Work"
        assert summary["show"] == "Some Show"
        assert summary["date"] == "2026-03-17"
        assert summary["duration"] == "1:02:05"
        assert summary["captions"] == "manual"
        assert summary["chapters"] == 1
        assert summary["written_to"] == str(out)
        assert summary["description_snippet"] == "A long description."

    def test_suggested_filename_is_slugified(self, et, monkeypatch, capsys, tmp_path):
        summary = self._run(et, monkeypatch, capsys, tmp_path / "note.md")
        assert summary["suggested_filename"] == (
            "2026-03-17 building-ai-agents-that-work-transcript.md"
        )

    def test_uploader_used_when_channel_absent(self, et, monkeypatch, capsys, tmp_path):
        info = {**self.INFO, "uploader": "Fallback Channel"}
        del info["channel"]
        self._run(et, monkeypatch, capsys, tmp_path / "note.md", info=info)
        assert 'show: "Fallback Channel"' in (tmp_path / "note.md").read_text(encoding="utf-8")

    def test_malformed_upload_date_yields_empty_date(self, et, monkeypatch, capsys, tmp_path):
        info = {**self.INFO, "upload_date": "2026"}
        summary = self._run(et, monkeypatch, capsys, tmp_path / "note.md", info=info)
        assert summary["date"] == ""

    def test_missing_captions_exits_one(self, et, monkeypatch, capsys, tmp_path):
        info = {**self.INFO, "subtitles": {}}
        with pytest.raises(SystemExit) as exc:
            self._run(et, monkeypatch, capsys, tmp_path / "note.md", info=info)
        assert exc.value.code == 1

    def test_empty_caption_track_exits_one(self, et, monkeypatch, capsys, tmp_path):
        with pytest.raises(SystemExit) as exc:
            self._run(et, monkeypatch, capsys, tmp_path / "note.md", events=[])
        assert exc.value.code == 1
