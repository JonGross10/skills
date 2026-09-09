#!/usr/bin/env python3
"""
Fetch an HTML web article and emit two artifacts:

  1. a plain-text transcription of the article body (the *source artifact*
     that `coverage_check.py` can verify against — a live URL is not a file,
     so without this there is nothing to verify), and
  2. a draft markdown conversion with a metadata block.

The draft is a starting point, not the deliverable: read it, fix headings,
captions and list structure by hand, then run

    python coverage_check.py <saved.txt> <final.md>

Usage:
    python fetch_web_article.py <url> --out-dir DIR [--slug NAME]
                                [--html PATH] [--keep-html]

Only dependency beyond the standard library is beautifulsoup4.

Exit codes:
    0 — artifacts written
    3 — could not fetch or could not locate an article body
"""
import argparse
import datetime
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

EXIT_OK = 0
EXIT_CANNOT_RUN = 3

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Body containers, most specific first. Substack/beehiiv/Ghost/WordPress all
# wrap the post body in one of these; the generic fallback handles the rest.
BODY_SELECTORS = [
    "div.available-content",           # Substack
    "div.body.markup",                 # Substack (older)
    "div#content-blocks",              # beehiiv
    "div.post-content",                # Ghost / WordPress
    "article div.entry-content",       # WordPress
    "article",
    "main",
]

# Chrome, nav, share bars and subscribe widgets that sit inside the body.
STRIP_SELECTORS = [
    "script", "style", "noscript", "svg", "form", "button",
    "nav", "header", "footer", "aside",
    ".subscribe-widget", ".subscription-widget-wrap", ".subscription-widget",
    ".post-footer", ".comments-page", ".paywall", ".pencraft-modal",
    ".share-dialog", ".button-wrapper", ".footer-buttons",
    ".captioned-image-container .image-link-expand",
]

BLOCK_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote",
              "figure", "figcaption", "pre", "tr")

# Skip decorative bitmaps: subscribe buttons, avatars, tracking pixels.
SKIP_IMAGE_HINTS = ("avatar", "icon", "logo", "pixel", "emoji")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    return raw.decode(resp.headers.get_content_charset() or "utf-8", "replace")


def find_body(soup):
    for sel in BODY_SELECTORS:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 500:
            return el, sel
    # Fallback: the div with the most text.
    best, best_len = None, 0
    for div in soup.find_all("div"):
        n = len(div.get_text(strip=True))
        if n > best_len:
            best, best_len = div, n
    if best is not None and best_len > 500:
        return best, "largest-div fallback"
    return None, None


def clean(body):
    for sel in STRIP_SELECTORS:
        for el in body.select(sel):
            el.decompose()
    return body


def page_title(soup) -> str:
    for sel in ('meta[property="og:title"]', 'meta[name="twitter:title"]'):
        el = soup.select_one(sel)
        if el and el.get("content"):
            return el["content"].strip()
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return "Untitled"


def page_description(soup) -> str:
    el = soup.select_one('meta[name="description"]')
    return el["content"].strip() if el and el.get("content") else ""


def _emphasize(node, marker: str) -> str:
    """Wrap an emphasis run in markers, keeping any space that hugs it outside
    the markers — `**Name:** text`, never `**Name:**text`."""
    raw = inline_markdown(node)
    inner = raw.strip()
    if not inner:
        return raw
    lead = " " if raw[:1].isspace() else ""
    trail = " " if raw[-1:].isspace() else ""
    return f"{lead}{marker}{inner}{marker}{trail}"


def image_markdown(img) -> str:
    src = img.get("src") or ""
    if not src or any(h in src.lower() for h in SKIP_IMAGE_HINTS):
        return ""
    return f"![{img.get('alt', '')}]({src})"


def inline_markdown(el) -> str:
    """Render an element's inline children as markdown (links, bold, italics)."""
    out = []
    for node in el.children:
        name = getattr(node, "name", None)
        if name is None:
            out.append(str(node))
        elif name in ("strong", "b"):
            out.append(_emphasize(node, "**"))
        elif name in ("em", "i"):
            out.append(_emphasize(node, "*"))
        elif name == "code":
            out.append(f"`{node.get_text()}`")
        elif name == "a":
            text = inline_markdown(node).strip()
            href = node.get("href", "")
            out.append(f"[{text}]({href})" if href and text else text)
        elif name == "br":
            out.append("\n")
        elif name == "img":
            out.append(image_markdown(node))
        else:
            out.append(inline_markdown(node))
    # Collapse runs of spaces but keep leading/trailing ones: callers strip at
    # the block level, and emphasis needs to know whether a space hugged it.
    return re.sub(r"[ \t]+", " ", "".join(out))


def to_markdown(body) -> str:
    """Walk block-level elements in document order and emit markdown."""
    lines = []
    for el in body.find_all(BLOCK_TAGS):
        # Skip nested duplicates (e.g. a <p> inside an <li>, a caption inside a
        # <figure> that the figure branch already renders).
        if el.name == "p" and el.find_parent(["li", "blockquote", "figcaption"]):
            continue
        if el.name == "figcaption" and el.find_parent("figure"):
            continue
        if el.name == "figure":
            img = el.find("img")
            parts = [image_markdown(img)] if img else []
            cap = el.find("figcaption")
            if cap:
                cap_text = inline_markdown(cap).strip()
                if cap_text:
                    parts.append(f"*{cap_text}*")
            parts = [p for p in parts if p]
            if parts:
                lines.extend(parts)
                lines.append("")
            continue
        text = inline_markdown(el).strip()
        if not text:
            continue
        if el.name.startswith("h") and el.name[1:].isdigit():
            level = min(int(el.name[1]) + 1, 6)  # article H1 becomes the doc H2
            # A heading's level already carries the emphasis; drop bold runs.
            lines.append(f"{'#' * level} {text.replace('**', '')}")
        elif el.name == "li":
            ordered = el.find_parent("ol") is not None
            lines.append(f"{'1.' if ordered else '-'} {text}")
        elif el.name == "blockquote":
            lines.extend(f"> {ln}" for ln in text.split("\n"))
        elif el.name == "figcaption":
            lines.append(f"*{text}*")
        elif el.name == "pre":
            lines.append(f"```\n{el.get_text()}\n```")
        else:
            lines.append(text)
        lines.append("")
    return "\n".join(lines)


def to_plain_text(body) -> str:
    """Plain-text transcription: one block per line, no markdown syntax.

    This is the source artifact the coverage check reads, so it must contain
    every numeric and every sentence the page shows.
    """
    lines = []
    for el in body.find_all(BLOCK_TAGS):
        if el.name == "figure":
            continue  # its <figcaption> is emitted on its own
        if el.name == "p" and el.find_parent(["li", "blockquote", "figcaption"]):
            continue
        text = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if text:
            lines.append(text)
    return "\n\n".join(lines)


def slugify(url: str) -> str:
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    tail = re.sub(r"[?#].*$", "", tail)
    return re.sub(r"[^a-z0-9-]+", "-", tail.lower()).strip("-") or "article"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("url")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--slug", help="Base filename (default: derived from the URL)")
    ap.add_argument("--html", type=Path, help="Read a saved HTML file instead of fetching")
    ap.add_argument("--keep-html", action="store_true", help="Also write the raw HTML")
    args = ap.parse_args()

    try:
        html = args.html.read_text(encoding="utf-8", errors="replace") if args.html else fetch(args.url)
    except (urllib.error.URLError, OSError) as e:
        print(f"Could not fetch {args.url}: {e}", file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("Missing dependency 'beautifulsoup4'. Install it with: "
              "pip install beautifulsoup4", file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)

    soup = BeautifulSoup(html, "html.parser")
    title = page_title(soup)
    description = page_description(soup)
    body, selector = find_body(soup)
    if body is None:
        print(f"Could not locate an article body in {args.url}. Save the page "
              f"manually (print-to-PDF or copy the text to .txt) and convert that.",
              file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)
    clean(body)

    slug = args.slug or slugify(args.url)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    fetched = datetime.date.today().isoformat()

    txt_path = args.out_dir / f"{slug}.source.txt"
    txt_path.write_text(
        f"{title}\n\n{description}\n\nSource: {args.url}\nFetched: {fetched}\n\n"
        + to_plain_text(body) + "\n",
        encoding="utf-8",
    )

    md_path = args.out_dir / f"{slug}.draft.md"
    md_path.write_text(
        f"# {title}\n\n"
        f"> **Source:** {args.url}\n"
        f"> **Fetched:** {fetched}\n"
        f"> **Extraction:** HTML fetch, body selector `{selector}`, "
        f"converted with `scripts/fetch_web_article.py`\n"
        + (f"> **Standfirst:** {description}\n" if description else "")
        + "\n" + to_markdown(body) + "\n",
        encoding="utf-8",
    )

    if args.keep_html:
        (args.out_dir / f"{slug}.raw.html").write_text(html, encoding="utf-8")

    print(f"body selector: {selector}")
    print(f"source text:   {txt_path}  ({len(txt_path.read_text(encoding='utf-8'))} chars)")
    print(f"draft md:      {md_path}")
    print("Next: clean the draft, then run coverage_check.py <source.txt> <final.md>")
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
