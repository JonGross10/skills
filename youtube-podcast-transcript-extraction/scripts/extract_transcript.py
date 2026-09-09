#!/usr/bin/env python3
"""Extract a YouTube transcript into a readable, timestamped markdown note.

Usage:
    python3 extract_transcript.py URL --out /path/to/note.md

Pipeline: yt-dlp -J for metadata (no download), fetch the caption track
(manual captions preferred over auto-generated), merge caption fragments
into paragraphs with coarse [h:mm:ss] markers, insert chapter headings,
write markdown with frontmatter. Prints a JSON summary to stdout.
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

PARA_MAX_SECONDS = 60      # start a new paragraph after this much elapsed time
PARA_MAX_CHARS = 900       # ... or when the paragraph grows past this length


def die(msg):
    print(json.dumps({"error": msg}))
    sys.exit(1)


def fetch_metadata(url):
    try:
        result = subprocess.run(
            ["yt-dlp", "-J", "--skip-download", url],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError:
        die("yt-dlp is not installed or not on PATH (install with: pip install -U yt-dlp)")
    except subprocess.TimeoutExpired:
        die("yt-dlp timed out after 120s fetching metadata")

    if result.returncode != 0:
        die(f"yt-dlp failed (try updating: yt-dlp -U): {result.stderr.strip()[-500:]}")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        die(f"yt-dlp returned unparseable JSON ({e}): {result.stdout.strip()[:300]}")


def pick_caption_track(info):
    """Prefer human-made captions; fall back to auto-generated. Returns (url, kind)."""
    for source, kind in ((info.get("subtitles"), "manual"),
                         (info.get("automatic_captions"), "auto-generated")):
        if not source:
            continue
        for lang in sorted(source):
            if lang == "en" or lang.startswith("en-"):
                # skip auto-translations like "en-fr"; en-orig / en-US etc. are fine
                if kind == "auto-generated" and "-" in lang and lang not in ("en-orig", "en-US", "en-GB"):
                    continue
                for fmt in source[lang]:
                    if fmt.get("ext") == "json3":
                        return fmt["url"], kind
    return None, None


def fetch_caption_events(caption_url):
    req = urllib.request.Request(caption_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        die(f"Caption download failed with HTTP {e.code} {e.reason} (URL may have expired)")
    except (urllib.error.URLError, TimeoutError) as e:
        die(f"Caption download failed: {e}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        die(f"Caption track was not valid json3 ({e})")
    return data.get("events", [])


def events_to_lines(events):
    """Flatten caption events to (start_seconds, text), deduping consecutive repeats."""
    lines = []
    for ev in events:
        segs = ev.get("segs")
        if not segs or "tStartMs" not in ev:
            continue
        text = "".join(s.get("utf8", "") for s in segs)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if lines and lines[-1][1] == text:
            continue
        lines.append((ev["tStartMs"] / 1000.0, text))
    return lines


def fmt_ts(seconds):
    s = int(seconds)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def build_body(lines, chapters):
    """Merge caption lines into timestamped paragraphs, with chapter headings."""
    chapters = sorted(chapters or [], key=lambda c: c.get("start_time") or 0)
    out, para, para_start = [], [], None
    next_chapter = 0

    def flush():
        nonlocal para, para_start
        if para:
            out.append(f"**[{fmt_ts(para_start)}]** " + " ".join(para))
            para, para_start = [], None

    for start, text in lines:
        while next_chapter < len(chapters) and start >= (chapters[next_chapter].get("start_time") or 0):
            flush()
            out.append(f"## {chapters[next_chapter].get('title', 'Chapter')}")
            next_chapter += 1
        if para_start is None:
            para_start = start
        para.append(text)
        if start - para_start >= PARA_MAX_SECONDS or sum(len(t) for t in para) >= PARA_MAX_CHARS:
            flush()
    flush()
    return "\n\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out", required=True, help="output markdown file path")
    args = ap.parse_args()

    info = fetch_metadata(args.url)
    caption_url, kind = pick_caption_track(info)
    if not caption_url:
        die("No English captions found (manual or auto-generated).")

    lines = events_to_lines(fetch_caption_events(caption_url))
    if not lines:
        die("Caption track was empty.")

    body = build_body(lines, info.get("chapters"))

    upload = info.get("upload_date", "")
    date = f"{upload[:4]}-{upload[4:6]}-{upload[6:8]}" if len(upload) == 8 else ""
    duration = info.get("duration") or 0
    title = info.get("title", "Untitled")
    channel = info.get("channel") or info.get("uploader") or ""

    front = "\n".join([
        "---",
        f'title: "{title}"',
        f"source: {info.get('webpage_url', args.url)}",
        f'show: "{channel}"',
        "platform: YouTube",
        f"date: {date}",
        f"duration: {fmt_ts(duration)}",
        f"captions: {kind}",
        "type: transcript",
        "---",
    ])
    note = f"{front}\n\n# {title}\n\n{body}\n"

    try:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(note)
    except OSError as e:
        die(f"Could not write {args.out}: {e}")

    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
    print(json.dumps({
        "title": title,
        "show": channel,
        "date": date,
        "duration": fmt_ts(duration),
        "captions": kind,
        "characters": len(body),
        "chapters": len(info.get("chapters") or []),
        "description_snippet": (info.get("description") or "")[:400],
        "suggested_filename": f"{date} {slug}-transcript.md",
        "written_to": args.out,
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
