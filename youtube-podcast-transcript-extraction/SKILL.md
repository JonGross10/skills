---
name: youtube-podcast-transcript
description: Extract podcast/video transcripts and turn them into vault knowledge notes. Use when the user shares a YouTube, Spotify, or Apple Podcasts link, or mentions "transcript", "podcast", "episode", or wants a recording added to the vault/second brain.
---

# Podcast Transcript → Second Brain

Extract YouTube transcripts via yt-dlp (downloads the stored caption file — nothing plays, ~2 seconds for a 2-hour episode), then distill into a learnings note **only after Jon confirms the takeaways**. No browser automation.

## Prerequisites

- `yt-dlp` on PATH (already installed via Homebrew).
- **If extraction fails, update yt-dlp first** (`brew upgrade yt-dlp`) — stale yt-dlp is the #1 cause of YouTube breakage. Retry once after updating before diagnosing anything else.

## Workflow

### 1. Extract (automatic, no user input needed)

```bash
python3 scripts/extract_transcript.py "YOUTUBE_URL" --out "/path/to/note.md"
```

(Script lives in this skill's `scripts/` directory.) It fetches metadata, prefers human-made captions over auto-generated, merges fragments into timestamped paragraphs with chapter headings, and writes a transcript note with frontmatter. It prints a JSON summary including `suggested_filename`.

Save directly to `reference/podcasts-transcripts-articles/` using the suggested filename: `YYYY-MM-DD episode-title-slug-transcript.md` (date = episode publish date).

### 2. Enrich frontmatter

Add `guests:` from the episode description (the JSON summary includes a snippet). Fix obvious caption errors in the frontmatter title only — never edit transcript body wording.

### 3. Draft takeaways — then STOP for confirmation

Read the transcript and present to Jon:
- 5–8 candidate takeaways (frameworks, "how to do things" learnings)
- The 2–3 most relevant sections with timestamps
- A note on caption quality (auto-generated, or manual with garbled names)

**Do not write the learnings note until Jon confirms or edits the takeaways.** He is the editor of what enters the brain.

### 4. Write the learnings note (after confirmation)

Second file, same folder: `YYYY-MM-DD episode-title-slug.md`. In Jon's voice, only his confirmed takeaways. Wikilink to the transcript note (`[[YYYY-MM-DD episode-title-slug-transcript]]`) and to related vault notes so it joins the graph. Frontmatter includes `source:` (the episode URL).

## Spotify / Apple Podcasts links

Don't scrape them — find the same episode on YouTube:

```bash
yt-dlp --no-update -j "ytsearch3:SHOW NAME episode title" 2>/dev/null | python3 -c "import json,sys; [print(json.loads(l)['webpage_url'],'|',json.loads(l)['title']) for l in sys.stdin]"
```

Confirm the match (title + duration), then run the normal flow. If the episode isn't on YouTube, that's a dead end — tell Jon his options: paste the transcript manually, or check the show's website for a published transcript.

## No captions available

Rare on YouTube. Don't attempt audio download + transcription — report "no transcript available" honestly and offer the fallbacks above.

## Common mistakes

- Writing the learnings note without Jon's confirmation — the checkpoint is the point.
- Diagnosing extraction failures before updating yt-dlp.
- "Improving" transcript wording — the cleanup script reflows text deterministically; keep it that way.
- Skipping the caption-quality flag — even manual captions garble names (e.g. "then Griggs" for "Dan Griggs").
