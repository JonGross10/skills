#!/usr/bin/env python3
"""Turn a meeting transcript export into a dated vault note.

Usage:
    python3 ingest_meeting_transcript.py EXPORT_FILE --vault-root /path/to/jon-os
    python3 ingest_meeting_transcript.py EXPORT_FILE --out /path/to/note.md

Accepts what Granola and Zoom actually hand you: WebVTT/SRT caption files,
plain text or markdown exports, and JSON exports carrying a transcript array.
Speaker labels and timestamps are preserved as-is; nothing is summarized.

Writes `reference/meeting-raw-transcripts/YYYY-MM-DD title-slug.md` with
provenance frontmatter and prints a JSON summary to stdout.
"""

import argparse
import json
import os
import re
import sys
from datetime import date as date_cls
from pathlib import Path

TRANSCRIPT_AREA = "reference/meeting-raw-transcripts"
CAPTION_INDEX_RE = re.compile(r"^\d+$")
VTT_TIME_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*\S+"
)
DATE_IN_NAME_RE = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})")
PROVIDER_HINTS = {"granola": "Granola", "zoom": "Zoom", "fireflies": "Fireflies"}


def die(msg):
    print(json.dumps({"error": msg}))
    sys.exit(1)


def slugify(text, limit=60):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:limit].strip("-")


def guess_provider(path, explicit=None):
    if explicit:
        return explicit
    haystack = str(path).lower()
    for hint, provider in PROVIDER_HINTS.items():
        if hint in haystack:
            return provider
    return "unknown"


def guess_date(path, explicit=None, text=""):
    """Prefer an explicit date, then one in the filename, then the file mtime."""
    if explicit:
        return explicit
    for candidate in (Path(path).name, text[:400]):
        match = DATE_IN_NAME_RE.search(candidate)
        if match:
            return "-".join(match.groups())
    try:
        return date_cls.fromtimestamp(os.path.getmtime(path)).isoformat()
    except OSError:
        return date_cls.today().isoformat()


def guess_title(path, explicit=None, text=""):
    if explicit:
        return explicit
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    stem = DATE_IN_NAME_RE.sub("", Path(path).stem).strip(" -_")
    return stem.replace("_", " ").replace("-", " ").strip() or "untitled meeting"


def parse_captions(text):
    """Flatten WebVTT/SRT cues into `[timestamp] line` rows, dropping cue numbering."""
    rows, pending_start = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "WEBVTT" or CAPTION_INDEX_RE.match(line):
            continue
        if line.startswith("NOTE ") or line.startswith("STYLE"):
            continue
        timing = VTT_TIME_RE.search(line)
        if timing:
            pending_start = timing.group("start").replace(",", ".").split(".")[0]
            continue
        if pending_start is None:
            rows.append(line)
            continue
        if rows and rows[-1] == f"[{pending_start}] {line}":
            continue
        rows.append(f"[{pending_start}] {line}")
    return rows


def parse_json_export(payload):
    """Pull (rows, metadata) out of a Granola/Zoom-style JSON export."""
    segments = None
    for key in ("transcript", "segments", "sentences", "utterances"):
        value = payload.get(key)
        if isinstance(value, list):
            segments = value
            break
    if segments is None:
        die("JSON export has no transcript/segments/sentences/utterances array")

    rows = []
    for seg in segments:
        if isinstance(seg, str):
            rows.append(seg)
            continue
        if not isinstance(seg, dict):
            continue
        speaker = seg.get("speaker") or seg.get("speaker_name") or seg.get("name")
        stamp = seg.get("timestamp") or seg.get("start_time") or seg.get("start")
        body = seg.get("text") or seg.get("sentence") or seg.get("content") or ""
        if not body:
            continue
        prefix = f"[{stamp}] " if stamp not in (None, "") else ""
        if speaker:
            prefix += f"{speaker}: "
        rows.append(f"{prefix}{body}".strip())

    participants = payload.get("participants") or payload.get("attendees") or []
    if isinstance(participants, list):
        participants = [
            p.get("name", "") if isinstance(p, dict) else str(p) for p in participants
        ]
        participants = [p for p in participants if p]
    else:
        participants = []

    meta = {
        "title": payload.get("title") or payload.get("meeting_title") or payload.get("topic"),
        "date": (payload.get("date") or payload.get("start_time") or "")[:10] or None,
        "participants": participants,
        "recording_url": payload.get("recording_url") or payload.get("url"),
    }
    return rows, meta


def read_export(path):
    """Return (rows, metadata) for any supported export format."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        die(f"Could not read {path}: {e}")
    if not text.strip():
        die(f"{path} is empty")

    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            die(f"{path} is not valid JSON ({e})")
        if not isinstance(payload, dict):
            die(f"{path} must contain a JSON object")
        return parse_json_export(payload)
    if suffix in (".vtt", ".srt"):
        return parse_captions(text), {}
    # Granola's markdown/text export is already readable; keep it verbatim.
    return [line.rstrip() for line in text.strip().splitlines()], {}


def build_note(rows, *, title, date, provider, source, participants, recording_url):
    front = [
        "---",
        f"source: {source}",
        f"date: {date}",
        f"provider: {provider}",
        f"participants: [{', '.join(participants)}]",
        f"recording_url: {recording_url or 'null'}",
        "type: meeting-transcript",
        "---",
    ]
    body = "\n".join(rows).strip()
    return f"{chr(10).join(front)}\n\n# {title}\n\nSource: {source}\n\n{body}\n"


def unique_path(path):
    """Never clobber an existing transcript; a re-run gets a -2, -3, ... suffix."""
    if not path.exists():
        return path
    for n in range(2, 100):
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    die(f"Too many notes already named like {path.name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export", help="transcript export file (.vtt, .srt, .txt, .md, .json)")
    ap.add_argument("--vault-root", help=f"vault root; note lands in {TRANSCRIPT_AREA}/")
    ap.add_argument("--out", help="explicit output path (overrides --vault-root)")
    ap.add_argument("--title")
    ap.add_argument("--date", help="meeting date as YYYY-MM-DD")
    ap.add_argument("--provider", help="Granola, Zoom, Fireflies, ...")
    ap.add_argument("--source", help="provenance string; defaults to the export path")
    args = ap.parse_args()

    if not args.out and not args.vault_root:
        die("Pass --vault-root or --out")

    rows, meta = read_export(args.export)
    if not rows:
        die(f"No transcript lines found in {args.export}")

    raw_text = "\n".join(rows[:20])
    title = guess_title(args.export, args.title or meta.get("title"), raw_text)
    date = guess_date(args.export, args.date or meta.get("date"), raw_text)
    provider = guess_provider(args.export, args.provider)
    source = args.source or f"{provider} — {args.export}"

    note = build_note(
        rows,
        title=title,
        date=date,
        provider=provider,
        source=source,
        participants=meta.get("participants") or [],
        recording_url=meta.get("recording_url"),
    )

    filename = f"{date} {slugify(title)}.md"
    out = Path(args.out) if args.out else Path(args.vault_root) / TRANSCRIPT_AREA / filename
    out.parent.mkdir(parents=True, exist_ok=True)
    out = unique_path(out)
    try:
        out.write_text(note, encoding="utf-8")
    except OSError as e:
        die(f"Could not write {out}: {e}")

    print(json.dumps({
        "title": title,
        "date": date,
        "provider": provider,
        "lines": len(rows),
        "characters": len(note),
        "suggested_filename": filename,
        "written_to": str(out),
    }, indent=2))


if __name__ == "__main__":
    # The contract is a single JSON object on stdout, so an unexpected crash
    # must still be reported as JSON rather than a traceback the caller
    # cannot parse.
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        die(f"Unexpected {type(e).__name__}: {e}")
