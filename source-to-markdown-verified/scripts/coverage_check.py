#!/usr/bin/env python3
"""
Coverage check for source-to-markdown-verified.

For a (source_file, markdown_file) pair, extract distinctive tokens from
the source — percentages, dollar amounts, years, and six-word phrase
fingerprints — and confirm each appears somewhere in the markdown.

Also runs the reverse direction for numeric tokens: any percentage or
dollar amount in the markdown that does NOT appear in the source is
flagged as possibly invented or transposed during conversion.

Usage:
    python coverage_check.py <source_path> <markdown_path>

Supported source types: .xlsx, .pdf, .docx, .pptx, .txt, .md

Exit codes:
    0 — nothing truly missing, no unsourced numbers
    1 — findings to investigate (missing content or unsourced numbers)
    2 — check NOT MEANINGFUL: source yielded almost no extractable text
        (likely a scanned/image-based document). A pass here would be
        vacuous — OCR or visually transcribe the source, then verify
        against that instead.
    3 — check COULD NOT RUN: unsupported source type, missing extractor
        dependency, or an unreadable file. Distinct from exit 1 so a setup
        problem is never mistaken for "no findings to investigate".

Most "missing" flags from this script are false positives — re-check each
one against the markdown by searching for any 4+ consecutive content
words from the phrase. If found, the flag is a false positive (likely
restructuring or PDF column-bleed). If not found, it's a real omission
and needs to be fixed in the markdown.
"""
import re
import sys
import argparse
from pathlib import Path

# Exit codes — see the module docstring.
EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_NOT_MEANINGFUL = 2
EXIT_CANNOT_RUN = 3

# pip package name for each extractor import, so a missing dependency yields
# an install command instead of a bare ImportError traceback.
EXTRACTOR_PACKAGES = {
    ".xlsx": "openpyxl",
    ".pdf": "pdfplumber",
    ".docx": "python-docx",
    ".pptx": "python-pptx",
}


class CannotRun(Exception):
    """The check cannot be performed at all (setup or input problem)."""


def normalize(s: str) -> str:
    """Normalize unicode punctuation and whitespace."""
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def normalize_for_match(s: str) -> str:
    """Strip punctuation too, for substring matching."""
    s = normalize(s).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _require(module: str, package: str):
    """Import an optional dependency, or exit with an actionable message."""
    try:
        return __import__(module)
    except ImportError:
        sys.exit(
            f"Missing dependency '{package}' needed to read this source type. "
            f"Install it with: pip install {package}"
        )


def extract_source_text(path: Path) -> str:
    """Extract all text content from a source file.

    Raises CannotRun for anything that makes the check impossible rather than
    merely inconclusive: unsupported type, missing extractor dependency, or an
    unreadable/corrupt file.
    """
    suffix = path.suffix.lower()
    try:
        return _extract_source_text(path, suffix)
    except ImportError as e:
        package = EXTRACTOR_PACKAGES.get(suffix, "the required extractor")
        raise CannotRun(
            f"Reading {suffix} needs {package}, which is not installed "
            f"(pip install {package}). Underlying error: {e}"
        ) from e
    except OSError as e:
        raise CannotRun(f"Cannot read {path}: {e}") from e
    except CannotRun:
        raise
    except Exception as e:
        raise CannotRun(
            f"{suffix} extractor failed on {path} ({type(e).__name__}: {e}). "
            f"The file may be corrupt or password-protected."
        ) from e


def _extract_source_text(path: Path, suffix: str) -> str:
    """Per-format extraction. Extractor deps are imported lazily so only the
    formats actually used need to be installed."""
    if suffix in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace")

    if suffix == ".xlsx":
        openpyxl = _require("openpyxl", "openpyxl")
        wb = openpyxl.load_workbook(path, data_only=True)
        out = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                for v in row:
                    if v is not None:
                        out.append(str(v))
        return "\n".join(out)

    if suffix == ".pdf":
        pdfplumber = _require("pdfplumber", "pdfplumber")
        out = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                out.append(t)
        # Image-only PDFs yield no text layer here; the MIN_MEANINGFUL_SOURCE_CHARS
        # guard in main() catches that case and exits 2 rather than passing vacuously.
        return "\n".join(out)

    if suffix == ".docx":
        _require("docx", "python-docx")
        from docx import Document
        doc = Document(path)
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.extend(c.text for c in row.cells)
        return "\n".join(parts)

    if suffix == ".pptx":
        _require("pptx", "python-pptx")
        from pptx import Presentation
        prs = Presentation(path)
        parts = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    parts.append(shape.text_frame.text)
                if shape.has_table:
                    for row in shape.table.rows:
                        parts.extend(c.text for c in row.cells)
            if slide.has_notes_slide:
                parts.append(slide.notes_slide.notes_text_frame.text)
        return "\n".join(parts)

    raise CannotRun(
        f"Unsupported source type: {suffix or '(no extension)'} "
        f"(supported: {', '.join(sorted(EXTRACTOR_PACKAGES) + ['.txt', '.md'])})"
    )


def numeric_present(tok: str, text: str) -> bool:
    """Word-boundary match for numeric tokens (pct/money/year).

    A plain substring test would treat a source figure as "present" when it
    is merely a fragment of a *different* number in the target — e.g. "20%"
    inside "120%", or the year "2021" inside "12021". Reject matches that
    are flanked by another digit or a decimal point.
    """
    return re.search(r"(?<![\d.])" + re.escape(tok) + r"(?![\d])", text) is not None


def extract_tokens(text: str):
    """Extract distinctive tokens worth checking."""
    text = normalize(text)
    tokens = set()

    # Percentages — but skip pure axis labels like 0%, 25%, 50%, 75%, 100%
    # and skip 1-2 digit ambiguous fragments like "00%" or "05%" that often
    # come from chart axis-label extraction artifacts.
    AXIS_LABELS = {"0%", "25%", "50%", "75%", "100%"}
    for m in re.findall(r"\b\d+(?:\.\d+)?%", text):
        if m in AXIS_LABELS:
            continue
        # Skip leading-zero artifacts like "00%", "05%" that come from
        # PDF extractors splitting "100%" or " 5%" awkwardly.
        if len(m) >= 3 and m.startswith("0") and not m.startswith("0."):
            continue
        tokens.add(("pct", m))

    # Dollar amounts
    for m in re.findall(
        r"\$\d[\d,\.]*(?:\s*(?:million|billion|thousand|k|m|b))?",
        text, re.I,
    ):
        # rstrip trailing sentence punctuation so "$5." -> "$5" (internal
        # separators like "$1,000" are preserved).
        tokens.add(("money", m.strip().rstrip(".,;:")))

    # US$ amounts (common in legal/consulting reports)
    for m in re.findall(r"US\$\d[\d,\.]*(?:\s+(?:million|billion))?", text):
        tokens.add(("money", m.strip().rstrip(".,;:")))

    # Years
    for m in re.findall(r"\b(?:19|20)\d{2}\b", text):
        tokens.add(("year", m))

    # Six-word phrase fingerprints from substantive sentences
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for s in sentences:
        s = s.strip()
        if 30 <= len(s) <= 200 and len(s.split()) >= 6:
            words = s.split()
            fp = " ".join(words[:6]).lower()
            tokens.add(("phrase", fp))

    return tokens


def check_coverage(source_tokens, md_text):
    """For each source token, check if it appears in the markdown.

    Two-pass check:
    1. Numeric tokens (pct/money/year) via word-boundary match so a figure
       isn't counted present merely as a fragment of a different number.
    2. Phrases via direct substring, falling back to 4-word overlap to
       filter out false positives from restructuring.
    """
    md_lower = md_text.lower()
    md_for_overlap = normalize_for_match(md_text)

    truly_missing = []
    likely_false_positives = []

    for kind, tok in source_tokens:
        if kind != "phrase":
            if not numeric_present(tok.lower(), md_lower):
                truly_missing.append((kind, tok))
            continue

        needle = tok.lower()
        if needle in md_lower:
            continue

        # Same phrase, punctuation-insensitive: markdown syntax inserted around
        # the words (**bold**, [link](url), list bullets) breaks a raw substring
        # match without changing the content.
        if normalize_for_match(tok) in md_for_overlap:
            continue

        # Phrase — try 4-word overlap
        words = normalize_for_match(tok).split()
        if len(words) < 4:
            truly_missing.append((kind, tok))
            continue

        found_overlap = False
        for i in range(len(words) - 3):
            chunk = " ".join(words[i:i + 4])
            if chunk in md_for_overlap:
                found_overlap = True
                break

        if found_overlap:
            likely_false_positives.append((kind, tok))
        else:
            truly_missing.append((kind, tok))

    return truly_missing, likely_false_positives


def check_unsourced_numbers(md_text, src_text):
    """Reverse check: numeric tokens in the markdown missing from the source.

    Restricted to percentages and dollar amounts — years appear in
    legitimately-added metadata blocks (conversion dates) and would flood
    this with noise. A hit means the number was either invented, mangled
    (e.g., OCR misread), or reformatted; each one needs to be traced back
    to the source before the markdown can be trusted.
    """
    src_lower = normalize(src_text).lower()
    unsourced = []
    for kind, tok in extract_tokens(md_text):
        if kind not in ("pct", "money"):
            continue
        if not numeric_present(tok.lower(), src_lower):
            unsourced.append((kind, tok))
    return unsourced


# Below this many characters of extracted source text, a coverage pass is
# vacuous — there is nothing to check against. Typical cause: a scanned
# (image-based) PDF where pdfplumber extracts no text layer.
MIN_MEANINGFUL_SOURCE_CHARS = 200


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="Source file (.xlsx/.pdf/.docx/.pptx)")
    parser.add_argument("markdown", type=Path, help="Converted markdown file")
    parser.add_argument(
        "--show-likely-fp", action="store_true",
        help="Also print likely-false-positive phrases for manual review",
    )
    args = parser.parse_args()

    if not args.source.exists():
        print(f"Source file not found: {args.source}", file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)
    if not args.markdown.exists():
        print(f"Markdown file not found: {args.markdown}", file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)

    print(f"Source:   {args.source}")
    print(f"Markdown: {args.markdown}")
    print()

    try:
        src_text = extract_source_text(args.source)
        md_text = args.markdown.read_text(encoding="utf-8", errors="replace")
    except CannotRun as e:
        print("!" * 70)
        print("COVERAGE CHECK COULD NOT RUN")
        print(str(e))
        print("Fix the above and re-run — this is NOT a passing check.")
        print("!" * 70)
        sys.exit(EXIT_CANNOT_RUN)
    except OSError as e:
        print(f"Cannot read {args.markdown}: {e}", file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)

    meaningful_chars = len(normalize(src_text))
    if meaningful_chars < MIN_MEANINGFUL_SOURCE_CHARS:
        print("!" * 70)
        print("COVERAGE CHECK NOT MEANINGFUL")
        print(f"Source yielded only {meaningful_chars} chars of extractable text")
        print(f"(threshold: {MIN_MEANINGFUL_SOURCE_CHARS}). This is typical of a scanned or")
        print("image-based document with no text layer. A 'pass' against an empty")
        print("source verifies nothing. OCR or visually transcribe the source")
        print("first, then run this check against that text instead.")
        print("!" * 70)
        sys.exit(EXIT_NOT_MEANINGFUL)

    tokens = extract_tokens(src_text)
    truly_missing, likely_fp = check_coverage(tokens, md_text)
    unsourced = check_unsourced_numbers(md_text, src_text)

    print(f"Tokens checked:           {len(tokens)}")
    print(f"Likely false positives:   {len(likely_fp)}  (phrase fingerprint mismatch, content present)")
    print(f"Truly missing:            {len(truly_missing)}")
    print(f"Unsourced numbers in md:  {len(unsourced)}")
    print()

    if truly_missing:
        print("TRULY MISSING (investigate each):")
        for kind, tok in truly_missing:
            print(f"  [{kind}] {tok!r}")
    else:
        print("No truly-missing items detected.")

    if unsourced:
        print()
        print("IN MARKDOWN BUT NOT IN SOURCE (possible invention/OCR mangle/reformat):")
        for kind, tok in unsourced:
            print(f"  [{kind}] {tok!r}")

    if args.show_likely_fp and likely_fp:
        print()
        print("LIKELY FALSE POSITIVES (content overlap found, fingerprint moved):")
        for kind, tok in likely_fp[:50]:
            print(f"  [{kind}] {tok!r}")

    # Exit code: 0 clean, 1 findings to investigate
    sys.exit(EXIT_FINDINGS if (truly_missing or unsourced) else EXIT_CLEAN)


if __name__ == "__main__":
    main()
