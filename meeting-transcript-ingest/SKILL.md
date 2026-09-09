---
name: meeting-transcript-ingest
description: Ingest a meeting transcript export (Granola, Zoom, Fireflies) into a dated raw-transcript note in the vault. Use when a meeting recording or transcript file needs to land in the vault, or when the meeting watcher hands over a new export.
argument-hint: "<export-file>"
triggers:
  - ingest a meeting transcript
  - file a Granola export
  - file a Zoom transcript
  - a new meeting export appeared in the watched folder
---

# Meeting transcript → vault note

This is the automated half of meeting capture: a file lands, a note appears. No
questions asked, nothing summarized. For pulling a transcript out of a provider
API instead of a file, use `extract-meeting-transcript` (MCP-based).

## When it fires

A meeting ends and the provider drops an export file somewhere on disk. Run
this skill by hand on that file, or wire it into a folder watcher that calls
the script below whenever a new export appears.

## Run it

```bash
python3 meeting-transcript-ingest/scripts/ingest_meeting_transcript.py "EXPORT_FILE" --vault-root .
```

(`--vault-root` is the project or vault root; the note lands in its
`meeting-raw-transcripts/` folder. Use `--out` to write somewhere else.) Accepted formats: `.vtt` / `.srt` caption files (Zoom),
`.txt` / `.md` exports (Granola), and `.json` exports carrying a
`transcript` / `segments` / `sentences` / `utterances` array.

It writes `meeting-raw-transcripts/YYYY-MM-DD title-slug.md` with
frontmatter (`source`, `date`, `provider`, `participants`, `recording_url`,
`type: meeting-transcript`) and prints a JSON summary including `written_to`.
Provider, date, and title are inferred from the JSON metadata, then the
filename, then the file mtime; override with `--provider`, `--date`, `--title`.

Re-running on the same meeting never overwrites: the second note gets a `-2`
suffix. Delete the duplicate rather than letting the script clobber the first.

## After ingestion

The transcript note is raw material, not a deliverable.

- Leave the transcript body untouched — it is the provenance record.
- If the meeting needs a summary, decisions, or follow-ups, write a *second*
  note and wikilink it to the transcript (`[[YYYY-MM-DD title-slug]]`), keeping
  transcript and learnings separate.
- Present candidate takeaways to the user and wait for confirmation before
  writing that second note. They are the editor of what enters the vault.

## Quality checks

- [ ] Body is the raw transcript, not a summary.
- [ ] Speaker labels and timestamps preserved where the export had them.
- [ ] Filename is `YYYY-MM-DD` + lower-case hyphenated title.
- [ ] Frontmatter has `source`, `date`, and `provider`.
- [ ] Note is in `meeting-raw-transcripts/`.

## Common mistakes

- Filing under a new meetings area — `meeting-raw-transcripts/` already
  exists and is the destination.
- Cleaning up transcript wording. Garbled names stay; note them in the summary
  note instead.
- Writing the summary note before the user confirms the takeaways.
