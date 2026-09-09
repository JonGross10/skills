---
name: extract-meeting-transcript
description: Extract the raw transcript of a meeting from Fireflies, Granola, Read.ai, Fathom, or Otter via their MCP servers and file it as a dated Markdown note in the vault. Not for summaries or action items.
argument-hint: "<provider> <meeting-identifier>"
triggers:
  - extract a meeting transcript
  - file a Fireflies transcript
  - file a Granola transcript
  - file a Read.ai transcript
  - file a Fathom transcript
  - file an Otter transcript
  - pull a raw meeting transcript
  - save a transcript from Fireflies, Granola, Read.ai, Fathom, or Otter
---

# Extract and file a raw meeting transcript

## Trigger

Use this skill when the user asks to pull, extract, save, or file a raw meeting transcript from **Fireflies**, **Granola**, **Read.ai**, **Fathom**, or **Otter**. Raw means verbatim: if the request says "raw", "transcript", "what was actually said", or "exact quotes", never substitute the provider's AI summary or notes.

This is the MCP half of meeting capture. The file half is `meeting-transcript-ingest` (`scripts/ingest_meeting_transcript.py`), which converts an export file that landed in the watched folder. Both write the same kind of note to the same folder; pick by where the transcript is — in a provider's API (this skill) or already on disk (ingest). Do not run both on the same meeting.

## Inputs required

1. **Provider** — `Fireflies`, `Granola`, `Read.ai`, `Fathom`, or `Otter`. When a meeting was captured by more than one, Granola is the transcript of record and Read.ai is the backup (see the Read.ai specifics); do not merge two providers' text into one body.
2. **Meeting identifier** — URL, meeting title, date, or internal ID. Ask if more than one meeting could match.
3. **Destination folder** — `meeting-raw-transcripts/`. Create the folder if it does not already exist.
4. **Known context** — project, participants, or tags the user wants recorded.

## Procedure

### Step 1: Resolve the meeting
- Confirm the provider and the exact meeting (date + title).
- If the identifier is ambiguous, list the candidates and ask the user to pick one.

### Step 2: Retrieve the raw transcript via MCP
- Ensure the provider’s MCP server is connected in the harness (`fireflies` for Fireflies, `granola` for Granola, `Read_AI` for Read.ai, `fathom` for Fathom, `otter` for Otter — check the exact server name in the harness’s server list). If it is not available, stop and ask the user to configure it. Provider tools are often deferred and the servers can be slow to connect: retry the tool lookup a few times before declaring the server missing.
- For the selected provider, call the MCP tool that lists or searches meetings (e.g. `fireflies_get_transcripts` / Granola `list_meetings` / Read.ai `list_meetings` / `search_meetings`) to resolve the meeting identifier. Never guess a meeting ID.
- Call the provider’s transcript-fetching MCP tool (e.g. `fireflies_get_transcript` / Granola `get_meeting_transcript` / Read.ai `list_meetings` with `expand: ["transcript"]`) to get the full raw transcript.
- Large transcripts (an hour is roughly 40–60 KB) may be persisted by the harness to a JSON file with only a short preview shown inline. If you get a preview, do not retry and do not work from the preview — read the persisted file and extract the transcript field from it.
- Capture the transcript **as-is**: do not summarize, condense, or rewrite it.
- Preserve speaker labels and timestamps if they are present in the source.
- Collect available metadata: meeting title, date, duration, participants, recording URL, and source URL/ID.

#### Fireflies specifics

`fireflies_get_transcript` returns structured data, not a text blob. The transcript text lives in the `sentences` array — each entry has a speaker name, start/end times, and the sentence text. Metadata such as `recording_url` sits alongside it.

- Read the **entire** `sentences` array, in order, and reconstruct the transcript body from it. Every entry goes into the note; no sampling, no truncating to the first N sentences.
- Format each sentence on its own line:

  ```
  [HH:MM - HH:MM] Speaker Name: sentence text
  ```

- Keep the speaker label and both timestamps on every line. Do not merge consecutive sentences from the same speaker into a paragraph and do not drop timestamps.
- If the tool paginates or caps results, keep calling it until you have every sentence, and note the count you retrieved so Step 5 can check it.
- `recording_url` is **provenance only**. It is not a substitute for the transcript body: a link to the Fireflies recording does not put a single word of transcript in the vault. The full sentence-by-sentence text must be pulled into the note.

#### Granola specifics

Granola exposes two different things. `get_meeting_transcript` returns the verbatim transcript; `get_meetings` and `query_granola_meetings` return AI-generated notes. Only the first one is a transcript.

- When resolving the meeting with `list_meetings`, always pass `"involvement": {"listed_as_participant": true}`. Without it, only notes the caller owns come back, and a meeting whose Granola note belongs to another participant is silently absent.
- For a specific day, use a custom range spanning that day **plus the next** (`"time_range": "custom", "custom_start": "YYYY-MM-DD", "custom_end": "<next day>"`) — the end bound behaves as a boundary, not an inclusive day. Match titles loosely; take the date and time from the record, not from the expected schedule. If there is no match, widen the range before concluding the meeting does not exist — Granola only has meetings where a note was captured.
- The transcript comes back as one continuous string with turns run together (`Me: … Them: …`), which is unreadable as Markdown. Split it into one line per speaker turn, breaking at each speaker label, so the body reads as alternating lines rather than a single wall of text.
- `Me` is the note owner. `Them` is **every other participant, undifferentiated** — consecutive speakers merge into one `Them` turn. Replace `Me` / `Them` with real names only when the MCP response identifies the speaker or Read.ai settles it (see below); never map `Them` onto a roster or infer who spoke from context. Keep the source labels otherwise and say in the note that individual speakers were not separated.
- Check capture completeness: Granola records locally and only while the app is running, so a late start or early stop is normal. Read the first and last turns — does it open at the top of the meeting or mid-sentence, and run through the goodbyes or stop mid-agenda? Record the finding in the Step 6 report; it decides whether Read.ai is needed for gap-fill.
- Splitting on speaker turns and adding line breaks is the only reformatting allowed — never reword, reorder, or drop text.

#### Read.ai specifics

Read.ai is a support source, not a co-equal one. Granola is the transcript of record when both captured the meeting. Read.ai is used for exactly three things: standing in when Granola did not run at all, covering a span Granola missed, and putting names to speakers.

- Fetch with `list_meetings` on the `Read_AI` server: set `start_datetime_gte` to the relevant window (e.g. 7 days back) and `expand: ["summary", "action_items", "transcript"]`. The transcript is nested inside the expanded meeting object and is large; read it from the persisted file with a script rather than from the preview.
- Read.ai transcripts **name speakers**. Reconstruct the body one line per speaker turn, `Speaker Name: text`, keeping any timestamps the response carries. Never drop the names.
- **Granola absent** — Read.ai becomes the transcript of record. Set `provider: Read.ai` and say so in the report.
- **Granola partial** — Read.ai covers ONLY the missing span, appended as a clearly labelled second section (`## Read.ai — <start>–<end>`) after Granola's text. Do not re-quote or re-derive anything inside Granola's coverage. State which span came from which source in the note and the report.
- **Speaker identification** — compare Read.ai's participant list against Granola's `known_participants`. Where they agree, the name is confirmed and may replace a `Them` label where it genuinely settles who spoke. Where they disagree, or a name is in neither list, do not guess or merge: keep the source label and report the discrepancy.
- Read.ai can return `out_of_quota`. Treat it as unavailable: stay on Granola alone and say so. If Read.ai simply has no record of the meeting, say so and continue.
- If no provider has the meeting, report that and stop — never invent one.

#### Fathom and Otter specifics

Fathom and Otter are only used when the user names them; they are not fallbacks for Granola. Their MCP tool names and transcript shapes are not yet documented here.

- List the connected server's tools first, then use its list/search tool to resolve the meeting and its transcript tool to fetch the body.
- Whatever the shape (an array of sentences or utterances, or a single string with speaker labels), reconstruct it one line per speaker turn with `Speaker Name: text`, keeping timestamps when present, and apply the same no-summary, no-truncation rules as above.
- After the first successful pull, record the actual server name, tool names, and response shape in this subsection so the next run does not rediscover them.

### Step 3: Build the filename
- Format: `YYYY-MM-DD <meeting-title>.md`.
- Use lower-case, hyphenated words for the title portion, e.g. `2026-08-30 acme-q3-review.md`.

### Step 4: Classify the meeting

Every transcript gets an `area`, so the folder stays filterable in Obsidian instead of becoming an undifferentiated pile.

- Infer it from the transcript subject matter — participants, the company or client discussed, the recurring topic.
- `area` must be one of the transcript areas listed in the table below. Fill the table in for your own setup before first use — a short, fixed vocabulary of the recurring kinds of meetings you have (a client engagement, a recurring team sync, research calls, a course). Do not invent a new area at run time; if nothing fits, set `area: unfiled` and say so in the Step 6 report.

  | `area` | covers |
  | --- | --- |
  | `<area-slug>` | `<what kinds of meetings belong here>` |
  | `<area-slug>` | `<what kinds of meetings belong here>` |
- `project` is reserved for grouping meetings on the same recurring workstream *within* an area. Until you define a project vocabulary, write `project: null` and leave the grouping to `area`. Do not invent slugs.
- A subsidiary workstream is not its own area — it belongs to the area that owns it.
- The classification is a label only: the note still lives in `meeting-raw-transcripts/` regardless of `area`.
- State the proposed `area` in the Step 6 report so the user can correct it in one line. Never block on confirmation — propose, save, and let the user override.

### Step 5: Write the note
- Create `meeting-raw-transcripts/` if it does not already exist.
- Save the file in that destination folder.
- Begin with YAML frontmatter:

  ```yaml
  ---
  source: <provider> — <meeting-url-or-id>
  date: YYYY-MM-DD
  provider: Fireflies|Granola|Read.ai|Fathom|Otter
  participants: [name, name, ...]
  recording_url: <url or null>
  area: <one of the transcript areas above, or unfiled>
  project: null
  transcript_status: none-at-source  # only when the provider returned no transcript
  ---
  ```

- Add a source pointer line after the frontmatter, e.g. `Source: <provider> — <meeting-url-or-id>`.
- Under the source pointer, paste the reconstructed transcript body — for Fireflies, the sentence lines built from the `sentences` array; for Granola, Read.ai, Fathom, and Otter, the turn-per-line text. Do not edit the transcript text itself.
- When Read.ai gap-fills a Granola transcript, add a line under the source pointer naming both sources and the span each covers, and keep the Read.ai span as its own labelled section.
- If the provider genuinely has no transcript for the meeting (Fireflies returns no sentences at all, Granola or Read.ai has no record), do not invent one: save the metadata, add `transcript_status: none-at-source` to the frontmatter, and say so in the Step 6 report.
- A note whose body is only the source pointer, the `recording_url`, or the metadata block is **invalid**. The frontmatter and the URL are provenance; the body must contain the transcript. If the body would be empty, stop and report the failure instead of saving a URL-only note.
- Add wikilinks to relevant project or person notes only if the user has explicitly named them.

### Step 6: Verify
- Confirm the file exists and is not empty or truncated.
- Confirm the body contains actual transcript sentences — multiple speaker/timestamp lines — and not just the source pointer or `recording_url`.
- Count the transcript lines in the saved note and confirm the number roughly matches the number of sentences (Fireflies) or speaker turns (Granola, Read.ai, Fathom, Otter) the MCP tool returned. A large shortfall means the body was truncated; go back to Step 2.
- Check the length is plausible for the meeting's duration — a 60-minute call producing a few KB means a late start or cut-out — and that the first and last lines are real conversation, not truncation artifacts.
- Confirm no speaker name appears in the body that was not in the source (Granola `Them` stays `Them` unless Step 2 settled it).
- Confirm the transcript is un-summarized and the frontmatter is complete.
- Report the saved file path, the line/sentence count, the proposed `area`, which source(s) supplied the body and whether they named speakers, the capture-completeness finding, and any missing metadata.

## Output

A dated Markdown note in `meeting-raw-transcripts/`, containing the full raw transcript and provenance frontmatter.

## Quality checks

- [ ] Transcript is the raw text, not a summary or notes.
- [ ] Note body contains the transcript sentences themselves, not merely the source URL or `recording_url`.
- [ ] Transcript line count roughly matches the sentence/turn count returned by the MCP tool.
- [ ] Speaker labels and timestamps are preserved if available.
- [ ] Body is line-broken per sentence (Fireflies) or per speaker turn (Granola, Read.ai, Fathom, Otter), not one unbroken block.
- [ ] No speaker attribution in the body that the source did not support; a Read.ai gap-fill span is labelled and its coverage stated.
- [ ] Filename uses `YYYY-MM-DD` prefix and lower-case, hyphenated title.
- [ ] Frontmatter includes `source`, `date`, `provider`, and `area`.
- [ ] `area` is one of the defined areas (or `unfiled`), and the proposed classification was reported to the user.
- [ ] `meeting-raw-transcripts/` exists and the file is saved there.
