---
name: source-to-markdown-verified
description: "Faithfully convert source documents (.pdf, .xlsx, .docx, .pptx) and web articles (URLs) to markdown AND verify the conversion didn't drop or scramble content. Use this whenever the user asks to convert, transcribe, dump, archive, or export documents or articles to markdown — especially when they care about accuracy (e.g., 'I need these files in markdown so I can hand them to another agent', 'convert these PDFs and make sure nothing is lost', 'turn this deck into markdown without losing the data'). Also use when the user uploads multiple documents and asks for them in a more agent-readable form. The skill's distinguishing feature is a verification pass that catches the most dangerous failure mode of naive conversion — tables and chart data that look authoritative but are silently misordered."
---

# Source-to-Markdown (Verified)

Convert documents to markdown while preserving content with high fidelity, then run a verification pass before declaring the work done. The naive approach — just extract text and dump it — produces output that *looks* fine but frequently has silent data corruption: spreadsheet cells aligned to the wrong rows, slide-deck chart percentages associated with the wrong categories, multi-column PDFs read sideways. The verification step exists to catch these.

The workflow has four phases: **calibrate effort**, **understand the input**, **convert**, **verify**.

## Running this skill (any agent)

The skill is plain files plus two scripts, so it runs the same under any agent
(Devin, Codex, Claude Code) and by hand. Nothing here depends on a
vendor-specific tool: where a step says "read the image" or "ask the user", use
whatever your harness offers for that.

```bash
SKILL=.agents/skills/source-to-markdown-verified

# Web article -> source artifact + draft markdown (needs beautifulsoup4)
python3 $SKILL/scripts/fetch_web_article.py <url> --out-dir <dir>

# Any source + finished markdown -> verification report
python3 $SKILL/scripts/coverage_check.py <source_file> <output.md>
```

Dependencies are per-format and imported lazily, so install only what the
source needs: `beautifulsoup4` (web), `pdfplumber` (.pdf), `openpyxl` (.xlsx),
`python-docx` (.docx), `python-pptx` (.pptx). Both scripts exit `3` and name the
missing package rather than failing obscurely.

Every conversion ends with a `coverage_check.py` run that exits `0`, or with an
explicit note in the delivery message about each finding you chose not to fix.

## Phase 0 — Calibrate effort and check the source's nature

Before doing anything else, look at the source and decide (a) whether you should be converting the full text at all, and (b) how much verification it warrants. Verification is cheap insurance against the catastrophic failure mode of "looks right, is wrong" — but it's not free, and applying it to documents that don't need it wastes the user's time and tokens.

### 0a. Copyright check (do this first)

If the source is a published copyrighted work — most often a commercially published book or a paid research report — full-text OCR/conversion to markdown reproduces the entire copyrighted text in a redistributable format. The user owning the PDF allows their own use of the PDF; it doesn't extend to producing a full-text copy in another format. Don't do that conversion regardless of how the request is framed (format conversion, archival, "context for another agent", etc.).

Tells that a source is a published copyrighted work:
- Front matter with ISBN, publisher imprint, copyright notice, "All rights reserved" language.
- A trade-book file size and page count (typically 150–400+ pages).
- Recognizable author and title that match a commercially published book.

When you spot this, don't silently proceed. Offer the user these alternatives and let them pick:

1. **Detailed framework/chapter summary** — a knowledge artifact covering the book's concepts, organized by chapter, with examples but not verbatim prose. Best for "I want to use this as context for another agent" — agents need the model, not the prose.
2. **Structural outline only** — OCR just the table of contents, chapter titles, section headings, figure/table captions. Skeleton, no body text.
3. **Specific chapters verbatim** — OCR 1–3 chapters the user names, falling within fair-use quoting for personal study.
4. **Skip the file** — the user has the PDF; they can reference it directly.

Documents that are *not* covered by this concern (proceed normally): the user's own work product, internal company documents, training decks the user produced or has rights to share, public-domain texts, government publications, marketing collateral / public research reports that organizations publish for free distribution, individual assessment outputs the user is the subject of.

### 0b. Verification scope

Use this rubric to decide what verification to run:

| Source profile | Coverage check | Visual chart verification |
|---|---|---|
| Text-only PDF, .docx, simple .md/.txt | **Yes** | No (no charts to verify) |
| .xlsx with simple data table | **Yes** | No (numbers are in cells, not charts) |
| .xlsx layout document (frameworks, forms) | **Yes** | No |
| .pdf with charts containing numeric data labels | **Yes** | **Yes** — this is the failure mode the skill exists for |
| .pptx with native PowerPoint charts | **Yes** | Native chart data is accessible via python-pptx; visual verify only if charts are pasted images |
| .pptx with image-pasted charts | **Yes** | **Yes** |
| HTML web article (a URL) | **Yes** — against a saved text transcription, see Phase 1 | Only if the article embeds charts as images |

When in doubt, do the verification — but don't waste cycles rendering 30 PDF pages as images when none of them contain charts. A 5-second scan with `pdfplumber` (count images per page) tells you which pages, if any, need visual verification.

## Phase 1 — Understand the input

Before converting anything, ask three quick clarifying questions (use whatever
structured-question mechanism your harness has; a short numbered list works too):

1. **One combined markdown file, or one per source?** Default to one-per-source for distinct documents.
2. **For spreadsheets:** preserve raw tables, tables-plus-summaries, or summaries only? Most users want accurate tables — but if a workbook is a layout document (forms, frameworks) rather than tabular data, flag that and structure it semantically instead.
3. **For PDFs:** full-text verbatim, full-text lightly cleaned, or structured summary? Default to "lightly cleaned" (remove page numbers, headers/footers) unless the user wants the document as a raw source archive — in which case verbatim is correct.

If the user's request already specifies these (e.g., "convert this PDF verbatim to markdown"), skip the corresponding questions. Don't ask questions whose answers you already have.

Then inspect each file. See `references/conversion-by-type.md` for the inspection recipe per format. Key signals to look for during inspection:

**For .xlsx — is this a data table or a layout document?**

A spreadsheet is a **data table** if:
- There's a clear single header row near the top of the first sheet.
- Most rows have values in most columns.
- Column count is consistent across rows.
- Few or no merged cells.

A spreadsheet is a **layout document** if any of these are true:
- Section labels appear in column A with data spread across rows below.
- Merged cells are used for section headers (count via `len(ws.merged_cells.ranges)` — more than ~5 strongly suggests layout).
- Many rows have only one or two filled cells.
- The first few rows contain titles, branding, or framework metadata rather than column headers.

Convert data tables to markdown tables. Convert layout documents to **headed sections** with semantic structure — forcing a layout document into a 12-column markdown table produces output no agent can use.

**For .pdf — what kind of PDF is this?**

```python
import pdfplumber
with pdfplumber.open(path) as pdf:
    for i, page in enumerate(pdf.pages, 1):
        chars = len(page.chars)
        imgs = len(page.images)
        print(f"page {i}: chars={chars}, images={imgs}")
```

Classify each page:
- **Native-text page** — chars > ~200, few or no images that contain text. Use `page.extract_text()`.
- **Image-based page** — chars near zero but `imgs > 0`. OCR is required.
- **Chart-bearing page** — text extraction returns chart-label values (percentages, dollar amounts, year labels in numeric tables). These need visual verification regardless of whether the page also has native text.

A single PDF often mixes all three. Treat each page on its own merits — don't OCR pages that have native text just because the appendix is image-based.

**For a URL — there is no source file yet, so make one.**

A live page cannot be verified against: it is not a file, and it changes. Before
converting, save a concrete source artifact — a plain-text transcription of the
article body (preferred) or a print-to-PDF of the page. `scripts/fetch_web_article.py`
does this in one step, writing `<slug>.source.txt` (the artifact to verify
against) and `<slug>.draft.md` (a starting point to clean up):

```bash
python3 scripts/fetch_web_article.py "<url>" --out-dir <dir>
```

Check two things before trusting the draft:

- **Paywall/copyright.** A truncated body, a "subscribe to keep reading" cut, or
  a paid research report puts you in Phase 0a territory — summarize instead of
  reproducing. Free posts, docs, and public blogs convert normally.
- **Body detection.** The script prints which selector matched. If it fell back
  to `largest-div fallback`, or the article is rendered client-side and the
  fetched HTML has no body, load the page in a browser and save the text or
  print to PDF manually, then convert that file.

See `references/conversion-by-type.md` for the full web-article recipe.

## Phase 2 — Convert

The goal is markdown that an AI agent (or a careful human reader) can use as a faithful substitute for the original. Structure beats raw fidelity: even verbatim output should have clear section headers, tables for tabular data, and a metadata block at the top noting the source file, page/sheet count, and any extraction caveats.

Use `references/conversion-by-type.md` for per-format instructions and code recipes. Key patterns that apply across types:

- **Lead with a metadata block.** Source filename, page/sheet count, publisher (for PDFs), and a short note describing how the file was extracted and what was OCR'd vs. native-text. Future agents reading the markdown need this context.
- **Don't invent structure that isn't there, but do impose structure to aid readability.** A workbook with a single "Archetype" sheet that contains a content framework isn't a table — it's a document. Convert it to headed sections, not a 14-column markdown table.
- **Preserve numerics exactly.** When a source has "$2.4 million" or "23%", that exact string should appear in the output. Don't round, don't reformat, don't combine.
- **For image-based PDF pages, OCR per page — not the whole document.** Mixed PDFs are common (text body + image-based appendix). Render only the image-based pages at ≥200 DPI with `pdftoppm`, then OCR with `tesseract`. Note in the metadata block which page ranges were OCR'd so a reader knows where to expect minor artifacts.
- **For chart-heavy slides, transcribe what the chart shows, not what the PDF text-extracts.** This is the single most important rule. See Phase 3.
- **Transcribe text that lives inside images.** Cover cards, pull-quote graphics and screenshots often carry numbers and quotes that no text extractor sees. Put that text in the image's alt text or an italic caption underneath so it survives into the markdown.

## Phase 3 — Verify (do not skip what Phase 0 told you to do)

Two verification mechanisms. Phase 0 told you which to run.

### 3a. Coverage check (almost always)

Run `python3 scripts/coverage_check.py <source_path> <markdown_path>` for each
pair. Supported source types are `.pdf`, `.xlsx`, `.docx`, `.pptx`, `.txt` and
`.md` — an `.html` file or a URL is not verifiable as-is, so point the check at
the saved `.txt` transcription from Phase 1 instead. The script extracts distinctive tokens from the source (percentages, dollar amounts, years, six-word phrase fingerprints) and confirms each appears somewhere in the markdown. It also runs the reverse direction on numeric tokens: any percentage or dollar amount in the markdown that isn't in the source gets flagged as possibly invented, OCR-mangled, or reformatted — trace each one back to the source before trusting the file.

Exit codes: `0` clean, `1` findings to investigate, `2` the check was **not meaningful** — the source yielded almost no extractable text (typical of scanned/image-based documents), `3` the check **could not run** at all (unsupported source type, missing extractor dependency such as `pdfplumber`/`openpyxl`, or an unreadable/password-protected file). On exit 2, do not treat the run as a pass: OCR or visually transcribe the source first, save that transcription as a text file, and re-run the check against it. On exit 3, fix what the message names and re-run — nothing was verified.

When the script reports "missing" items, **don't immediately conclude content was dropped.** The script also reports "likely false positives" — items where a 4-word substring from the phrase WAS found in the markdown, suggesting the content is present but the fingerprint moved due to restructuring. Trust that signal.

Real omissions still need investigation. Three causes of *true* misses to know about:

1. **Genuine drops** — content the conversion lost. Fix by re-extracting that section.
2. **Aggressive restructuring** — you converted a sentence to a bullet list and the fingerprint no longer matches even at 4-word level. Search the markdown for distinctive nouns/numbers from the source content; if found, it's not a real omission.
3. **PDF column-bleed artifacts** — pdfplumber reads multi-column layouts left-to-right across columns, producing word sequences that don't represent real sentences in the source. These show up as "missing phrases" that never existed. Search the original PDF for the phrase fragment; if you can't find it as a contiguous string in the source, it's an artifact, not real content.

Always do the 4-word-overlap check on flagged items before assuming a drop occurred. Don't waste time fixing phantoms.

### 3b. Visual chart verification (PDFs and image-pasted .pptx with charts)

**This is the failure mode that catches everyone.** PDF text extraction returns chart values in the order they appear in the document's layout stream, *not* in the order they appear on the chart. The result: a clean-looking markdown table where the row labels are correct but every value is associated with the wrong row.

For any PDF page that contains a chart with numeric labels (bar charts, stacked bars, donut charts with %s), render the page as a high-resolution image and *read the chart visually* before trusting the extracted table:

```bash
pdftoppm -r 180 -f <page_num> -l <page_num> "<source.pdf>" /tmp/chart -png
# then read /tmp/chart-<page>.png with the Read tool
```

For dense paired stacked-bar charts (e.g., industry breakdowns shown twice for "experienced" vs. "expected"), the per-cell labels are often too small to transcribe reliably even at 300 DPI. In that case, transcribe what you *can* read confidently (overall headline, totals, industries the chart visually highlights as outliers), then write a short qualitative section pointing the reader to the source page for the full breakdown. Do not pretend you read values you didn't.

When you find errors during this pass, fix them in the markdown immediately. Don't defer — the user trusted the first version and a "verification noted errors" file with the errors still in it is worse than no verification at all.

## Phase 4 — Deliver

Hand over the final markdown files the way your harness delivers artifacts
(attachment, file link, or just the repo paths). In the message:

- Say briefly what each file covers.
- Be explicit about anything the verification pass changed, what couldn't be fully verified (e.g., dense charts), and where to look in the source for those.
- Don't repeat the document's contents in the chat — the user can open the files.

## Why each step exists

- **Phase 0 (calibrate)** exists because applying full verification to a simple text document is theater. Skip what doesn't apply, do what does.
- **Phase 1 (clarify)** exists because output-format preferences shape everything downstream — building first and asking later wastes effort. Skip clarifications the user already answered.
- **Phase 2 (convert)** is the obvious step, but the metadata block and OCR notation are easy to skip and make the output much more useful to whoever reads it next.
- **Phase 3 (verify)** exists because PDF and slide-deck extraction *will* silently corrupt chart data. The verification pass is the only protection against handing the user a confidently wrong file. If you skip this step and the user later finds an error, the entire deliverable's credibility is gone — even the correct parts become suspect.
- **Phase 4 (deliver)** keeps the chat clean so the files themselves are the deliverable, not a chat summary of them.

## Quick reference

| Source | Primary tool | Watch out for |
|---|---|---|
| .xlsx | openpyxl (`data_only=True`) | Layout workbooks (frameworks, forms) that aren't really tabular — many merged cells signals this |
| .pdf (text) | pdfplumber `extract_text()` | Multi-column layouts → word-bleed; chart pages → wrong values |
| .pdf (image pages) | pdftoppm + tesseract | OCR only the image pages, not the whole doc; note OCR sections in metadata |
| .pdf (mixed) | Per-page classification | Don't OCR pages that have native text |
| URL / web article | `scripts/fetch_web_article.py` (beautifulsoup4) | No source file exists until you save one; paywall truncation; client-side-rendered bodies |
| .docx | python-docx or mammoth | Tracked changes invisible by default; tables, headings, bullets |
| .pptx | python-pptx | Speaker notes (often forgotten); image-pasted charts need visual verify |

See `references/conversion-by-type.md` for full per-format recipes.
