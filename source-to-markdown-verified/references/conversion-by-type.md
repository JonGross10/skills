# Conversion recipes by file type

Per-format guidance with code patterns. Read the section for whichever file types you're working with.

## Table of contents
- [HTML web article (a URL)](#html-web-article-a-url)
- [.xlsx (Excel)](#xlsx-excel)
- [.pdf — native text](#pdf--native-text)
- [.pdf — image-based pages](#pdf--image-based-pages)
- [.pdf — chart-heavy slide decks](#pdf--chart-heavy-slide-decks)
- [.docx (Word)](#docx-word)
- [.pptx (PowerPoint)](#pptx-powerpoint)

---

## HTML web article (a URL)

A URL is not a source file. Verification compares a markdown file against a
source *file*, so the first move is always to save a concrete artifact: a
plain-text transcription of the article body, or a print-to-PDF of the page.
Without it there is nothing to verify against, and the page may change or
disappear before anyone re-reads your markdown.

```bash
python3 scripts/fetch_web_article.py "<url>" --out-dir inbox/_work
# writes <slug>.source.txt  (verify against this)
#        <slug>.draft.md    (clean this up into the deliverable)
# --html <path>   convert an already-saved page instead of fetching
# --keep-html     also write the raw HTML alongside the artifacts
```

The script strips nav, share bars, subscribe widgets and comments, then walks
block-level elements in document order, so headings, lists, blockquotes, links,
figures and captions survive. It prints the CSS selector it matched
(`div.available-content` for Substack, `div#content-blocks` for beehiiv,
`div.post-content` for Ghost/WordPress, then `article`/`main`).

**Check before trusting the draft:**

- **Paywall.** A body that stops mid-article, or ends in a subscribe prompt,
  means you only fetched the preview. Full-text conversion of paid content is a
  Phase 0a problem — summarize instead, or ask the user.
- **`largest-div fallback`, or a suspiciously short body.** Client-side-rendered
  pages ship no article HTML. Open the page in a browser, save the text (or print
  to PDF), and convert that file with `--html` or directly.
- **Text inside images.** Cover cards and pull-quote graphics carry numbers no
  extractor sees. Read the images and transcribe their text into alt text or an
  italic caption — otherwise it is silently lost.
- **Boilerplate the strip list removes on purpose** (subscribe CTAs, "Thanks for
  reading" footers). If you verify against an unstripped dump of the page, these
  show up as missing phrases; that is expected, and the only findings you should
  accept without a fix.

Then clean the draft: replace the draft's metadata blockquote with the vault's
frontmatter conventions, fix any heading levels the site used decoratively, and
keep every numeric string exactly as written. Finish with:

```bash
python3 scripts/coverage_check.py inbox/_work/<slug>.source.txt inbox/<slug>.md
```

A useful second pass: dump the article body's text *without* the strip list and
run the check against that too. Every finding should be boilerplate; anything
else means the strip list ate real content.

---

## .xlsx (Excel)

Use `openpyxl` with `data_only=True` so formula results (not formulas themselves) come through.

```python
import openpyxl
wb = openpyxl.load_workbook(path, data_only=True)
for ws in wb.worksheets:
    print(f"Sheet: {ws.title}  dims: {ws.dimensions}  max_row={ws.max_row} max_col={ws.max_column}")
    for r in range(1, ws.max_row + 1):
        row = [str(ws.cell(row=r, column=c).value or "") for c in range(1, ws.max_column + 1)]
        print(f"R{r}: " + " | ".join(row))
```

**Decision: tabular or layout?**

- **Tabular workbook** (clean header row + rows of data): convert to a markdown table. Preserve column headers and every row.
- **Layout workbook** (merged cells, section labels in column A, framework-style content scattered across columns): convert to *headed sections*, not a table. Group related cells under semantic headings. Add a "Source layout reference" appendix at the end that maps each section back to original cells/rows so future readers can verify nothing was lost.

**Common traps:**
- Multi-sheet workbooks where sheets have different shapes — convert each sheet under its own H2 heading.
- Merged-cell headers — `openpyxl` reads the value from the top-left cell of the merge; downstream cells return `None`. Don't treat these as "missing data".
- Hidden columns or sheets — note their presence in the metadata block but extract their contents too.

---

## .pdf — native text

Use `pdfplumber` for text-heavy PDFs.

```python
import pdfplumber
with pdfplumber.open(path) as pdf:
    for i, page in enumerate(pdf.pages, 1):
        text = page.extract_text() or ""
        chars = len(page.chars)
        imgs = len(page.images)
        # If chars is small but imgs is large, this is an image-based page → OCR it
```

**Layout awareness.** Single-column reports (legal documents, articles) extract cleanly. Multi-column layouts (consulting reports, two-column whitepapers) cause **word-bleed**: pdfplumber reads left-to-right across the whole page width, so words from column 1 line up next to words from column 2, producing sentences like "I am the they will listen to others" that never existed in the source. This is a known artifact of the extraction, not real content.

If you see word-bleed and the layout matters (e.g., the document has paired left/right column content like a benefits matrix), use `page.extract_text(layout=True)` or extract by region. For most narrative PDFs the bleed is cosmetic and the actual content is recoverable.

**Escalation path: docling.** If word-bleed is severe across many pages, or the document is full of complex unruled tables that `extract_tables()` mangles, [docling](https://github.com/docling-project/docling) (`pip install docling`, Python 3.10+) does ML-based layout analysis with reading-order detection and table-structure recognition, and outputs markdown directly (`docling <file>` on the CLI). Caveats: the first run downloads sizable models, it's slower than pdfplumber, and it does NOT remove the need for Phase 3 — its output goes through the same coverage check and visual chart verification as any other conversion. Don't reach for it on documents pdfplumber handles cleanly; it's for the multi-column/table-heavy minority.

**Watch for:**
- Repeated headers/footers — strip them in light-cleaning mode, keep in verbatim mode.
- Page numbers — same treatment.
- Footnotes — preserve as inline references or a footnotes section at the end of the relevant page.
- Tables — `page.extract_tables()` works for ruled tables but is unreliable for visual-only tables. For complex tables, fall back to rendering the page as an image and reading it visually.

---

## .pdf — image-based pages

If `len(page.chars)` is near zero but `len(page.images)` is nonzero, the page is image-based (scanned, or text rendered to image). Use OCR.

```bash
# Render the image-based pages at high DPI
pdftoppm -r 200 -f <first_page> -l <last_page> "<source.pdf>" /tmp/page -png

# OCR each rendered page
for f in /tmp/page-*.png; do
    echo "=== $f ==="
    tesseract "$f" -
done
```

Tesseract is good but not perfect. Expect:
- Smart-quote/apostrophe variants (`'` rendered as `'` or omitted)
- Occasional `I` ↔ `l` confusions in sans-serif fonts
- Word-order preserved but punctuation occasionally lost

**Always note OCR'd sections in the markdown's metadata block** so readers know where to expect these artifacts. Example: "Pages 1–11 contain native text and were extracted verbatim. Pages 12–15 are image-based; their text was recovered via OCR and may contain minor character-level artifacts."

### When to skip tesseract and transcribe visually

For very short image-based PDFs (fewer than ~5 pages), or for pages where tesseract output comes back garbled, scrambled, or jumbled across columns, the cleanest move is often to skip tesseract entirely and transcribe by reading the rendered images yourself. Render the page with `pdftoppm` at 250 DPI and use the `Read` tool on the PNG — you can see the page just as a person would and write the text directly.

Visual transcription beats tesseract specifically when:
- The page has a non-linear layout (radial diagrams, zigzag stage charts, decorative side-bars) that tesseract reads in a confusing order.
- Tables span the page in a visual grid that tesseract flattens incorrectly.
- The page has heavy decorative typography or stylized lettering.
- The page is short and you can read it faster than you can debug tesseract's output.

For long image-based runs (10+ pages), tesseract is still the right call — it's faster than visual transcription at scale and its artifacts are predictable. Note the choice in the metadata block either way.

---

## .pdf — chart-heavy slide decks

**This is the most error-prone category.** Consulting decks, survey reports, board decks: PDF text extraction returns chart percentages in the order the PDF layout engine wrote them, *not* in the order they appear on the chart. The result: a clean-looking markdown table with the labels correct but values shifted by one row, or columns swapped, or industries misaligned to their numbers.

**Always render and visually verify chart pages.** Do not trust `extract_text()` output for any page that contains a chart with numeric data labels.

```bash
pdftoppm -r 180 -f <page_num> -l <page_num> "<source.pdf>" /tmp/chart -png
# Then read /tmp/chart-<page>.png with the Read tool
```

For dense paired stacked-bar charts (e.g., 12 industries × 4 segments × 2 panels), labels are often too small to transcribe reliably even at 300 DPI. The honest move is:

1. Transcribe the headline verbatim.
2. Transcribe values you can read *confidently*. If the source visually highlights certain industries (e.g., red circles around outliers), name those and read their values.
3. Add a short note: *"Per-industry stacked-bar percentages are not transcribed in full because cell labels are too small to read reliably from rendered images. See page N of the source PDF for exact figures."*

This is much better than fabricating a precise-looking table with wrong values.

**Pages worth special attention in any slide deck:**
- Charts with paired year comparisons (2025 vs. 2024) — easy to swap columns
- Stacked bars with 3+ segments — easy to mis-order the segment values
- Industry/category breakdowns — easy to misalign categories to their values

---

## .docx (Word)

Use `python-docx`.

```python
from docx import Document
doc = Document(path)

# Paragraphs (preserves heading levels)
for p in doc.paragraphs:
    style = p.style.name if p.style else "Normal"
    print(f"[{style}] {p.text}")

# Tables
for ti, table in enumerate(doc.tables, 1):
    print(f"=== Table {ti} ===")
    for row in table.rows:
        cells = [c.text.strip() for c in row.cells]
        print(" | ".join(cells))
```

**Map Word styles to markdown:**
- `Heading 1` → `# `, `Heading 2` → `## `, etc.
- `Title` → `# `
- Bullet/numbered lists → markdown lists
- Bold/italic runs → `**...**` / `*...*`

**Watch for:**
- Tracked changes — `python-docx` ignores them by default; the resulting text is the accepted state. If the user needs to see tracked changes, this requires more work (or use `mammoth` which exposes them).
- Comments — same; ignored by default.
- Embedded images — extract via `doc.part.related_parts` if needed; usually safe to skip unless the user explicitly wants them.
- Footnotes/endnotes — preserved in the doc but you need `doc.part.footnotes_part` to access them.

For complex .docx documents (letterheads, multi-column legal briefs), `mammoth` produces cleaner markdown than custom python-docx code:

```bash
pip install mammoth --break-system-packages
```

```python
import mammoth
with open(path, "rb") as f:
    result = mammoth.convert_to_markdown(f)
    print(result.value)
    for msg in result.messages:
        print("WARNING:", msg)  # mammoth reports lossy conversions
```

---

## .pptx (PowerPoint)

Use `python-pptx`.

```python
from pptx import Presentation
prs = Presentation(path)

for i, slide in enumerate(prs.slides, 1):
    print(f"\n=== Slide {i} ===")
    for shape in slide.shapes:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                print(para.text)
        if shape.has_table:
            table = shape.table
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                print(" | ".join(cells))

    # Speaker notes — DO NOT forget these
    if slide.has_notes_slide:
        notes = slide.notes_slide.notes_text_frame.text
        if notes.strip():
            print(f"\n[Speaker notes for slide {i}]\n{notes}")
```

**Speaker notes are the most commonly missed content in pptx conversions.** Always check `slide.has_notes_slide` and extract them under a "Speaker notes" subsection per slide.

**For charts embedded in slides:**
- Native PowerPoint charts have data accessible via `shape.chart.plots[*].categories` and `chart.series` — extract this when possible.
- Image-based charts (screenshots pasted in) need the same treatment as PDF chart pages: render the slide as an image and verify visually.

To render a slide as an image:

```bash
# Convert pptx to PDF first, then to images
libreoffice --headless --convert-to pdf "<source.pptx>" --outdir /tmp/
pdftoppm -r 180 /tmp/source.pdf /tmp/slide -png
```

---

## After conversion, always run verification

Regardless of source type, run `scripts/coverage_check.py` and (for any PDF/pptx with charts) do a visual chart verification pass. See the main SKILL.md Phase 3 for details on interpreting the coverage check results — most flagged "missing" items are false positives from restructuring or column-bleed; the real ones must be fixed.
