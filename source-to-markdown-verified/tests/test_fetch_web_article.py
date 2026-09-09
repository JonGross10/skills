"""Unit tests for the web-article fetcher.

Every test converts a saved HTML fixture (no network), which is how the
script is meant to be re-run on an archived page anyway.
"""
import pytest

bs4 = pytest.importorskip("bs4", reason="beautifulsoup4 is not installed")

PAGE = """
<html><head>
  <title>Fallback title</title>
  <meta property="og:title" content="Rebuilding the forecast stack" />
  <meta name="description" content="INTERVIEWS | EPISODE 6" />
</head><body>
  <nav>Home Archive Subscribe</nav>
  <div class="available-content">
    <figure>
      <img src="https://cdn.example.com/hero.png" alt="Episode 6 cover" />
      <figcaption>Jeff Cobourn, Gusto</figcaption>
    </figure>
    <p>Forecast delivery moved from business day 10 to business day 5.</p>
    <h2>The stack</h2>
    <p><strong><span>Julian: </span></strong><span>Where does the data live?</span></p>
    <p><strong>Jeff:</strong> In <a href="https://example.com/warehouse">the warehouse</a>.</p>
    <ul><li>Zero to 80% in six months.</li></ul>
    <ol><li>First</li></ol>
    <blockquote>We deleted the spreadsheets.</blockquote>
    <p>Filler paragraph so the container clears the body-detection length
    threshold, because a selector match with almost no text is more likely a
    teaser or a paywalled stub than the real article body. This sentence exists
    only to make the fixture long enough to look like a genuine post body, and
    it repeats itself a little to get there without adding new numerics.</p>
    <div class="subscription-widget-wrap"><p>Subscribe for free.</p></div>
    <img src="https://cdn.example.com/avatar.png" alt="avatar" />
  </div>
  <footer>Copyright</footer>
</body></html>
"""


@pytest.fixture(scope="session")
def fw():
    import importlib.util
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "fetch_web_article.py"
    spec = importlib.util.spec_from_file_location("fetch_web_article", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def body(fw):
    soup = bs4.BeautifulSoup(PAGE, "html.parser")
    el, selector = fw.find_body(soup)
    assert selector == "div.available-content"
    return fw.clean(el)


class TestMetadata:
    def test_og_title_wins_over_title_tag(self, fw):
        soup = bs4.BeautifulSoup(PAGE, "html.parser")
        assert fw.page_title(soup) == "Rebuilding the forecast stack"

    def test_description_is_read(self, fw):
        soup = bs4.BeautifulSoup(PAGE, "html.parser")
        assert fw.page_description(soup) == "INTERVIEWS | EPISODE 6"

    @pytest.mark.parametrize("url,expected", [
        ("https://example.com/p/some-post", "some-post"),
        ("https://example.com/p/some-post/", "some-post"),
        ("https://example.com/p/some-post?utm_source=x", "some-post"),
        ("https://example.com/", "example-com"),
    ])
    def test_slugify(self, fw, url, expected):
        assert fw.slugify(url) == expected


class TestBodyDetection:
    def test_falls_back_to_largest_div(self, fw):
        html = "<html><body><div>" + ("word " * 200) + "</div></body></html>"
        el, selector = fw.find_body(bs4.BeautifulSoup(html, "html.parser"))
        assert selector == "largest-div fallback"
        assert el is not None

    def test_no_body_returns_none(self, fw):
        el, selector = fw.find_body(bs4.BeautifulSoup("<html><body>hi</body></html>", "html.parser"))
        assert (el, selector) == (None, None)

    def test_chrome_is_stripped(self, fw, body):
        text = body.get_text(" ")
        assert "Subscribe for free" not in text
        assert "Archive" not in text


class TestMarkdown:
    def test_emphasis_keeps_the_space_that_hugged_it(self, fw, body):
        assert "**Julian:** Where does the data live?" in fw.to_markdown(body)

    def test_link_is_rendered(self, fw, body):
        assert "[the warehouse](https://example.com/warehouse)" in fw.to_markdown(body)

    def test_heading_level_shifts_down_one(self, fw, body):
        assert "### The stack" in fw.to_markdown(body)

    def test_figure_becomes_image_plus_italic_caption(self, fw, body):
        md = fw.to_markdown(body)
        assert "![Episode 6 cover](https://cdn.example.com/hero.png)" in md
        assert "*Jeff Cobourn, Gusto*" in md

    def test_caption_is_not_emitted_twice(self, fw, body):
        assert fw.to_markdown(body).count("Jeff Cobourn, Gusto") == 1

    def test_decorative_images_are_skipped(self, fw, body):
        assert "avatar.png" not in fw.to_markdown(body)

    def test_list_markers(self, fw, body):
        md = fw.to_markdown(body)
        assert "- Zero to 80% in six months." in md
        assert "1. First" in md

    def test_blockquote_prefix(self, fw, body):
        assert "> We deleted the spreadsheets." in fw.to_markdown(body)

    def test_numerics_are_preserved_verbatim(self, fw, body):
        assert "business day 10 to business day 5" in fw.to_markdown(body)


class TestPlainText:
    def test_carries_no_markdown_syntax(self, fw, body):
        text = fw.to_plain_text(body)
        assert "**" not in text and "![" not in text
        assert "Julian: Where does the data live?" in text

    def test_keeps_every_numeric(self, fw, body):
        assert "80%" in fw.to_plain_text(body)
        assert "business day 10" in fw.to_plain_text(body)


class TestArtifacts:
    def _run(self, fw, monkeypatch, tmp_path, html=PAGE, extra_argv=()):
        src = tmp_path / "page.html"
        src.write_text(html, encoding="utf-8")
        argv = ["fetch_web_article.py", "https://example.com/p/some-post",
                "--out-dir", str(tmp_path / "out"), "--html", str(src), *extra_argv]
        monkeypatch.setattr(fw.sys, "argv", argv)
        with pytest.raises(SystemExit) as exc:
            fw.main()
        return exc.value.code, tmp_path / "out"

    def test_writes_source_text_and_draft(self, fw, monkeypatch, tmp_path):
        code, out = self._run(fw, monkeypatch, tmp_path)
        assert code == 0
        assert (out / "some-post.source.txt").exists()
        draft = (out / "some-post.draft.md").read_text(encoding="utf-8")
        assert draft.startswith("# Rebuilding the forecast stack")
        assert "**Source:** https://example.com/p/some-post" in draft
        assert "div.available-content" in draft

    def test_source_text_records_the_url(self, fw, monkeypatch, tmp_path):
        _code, out = self._run(fw, monkeypatch, tmp_path)
        text = (out / "some-post.source.txt").read_text(encoding="utf-8")
        assert "Source: https://example.com/p/some-post" in text

    def test_keep_html_writes_the_raw_page(self, fw, monkeypatch, tmp_path):
        _code, out = self._run(fw, monkeypatch, tmp_path, extra_argv=("--keep-html",))
        assert (out / "some-post.raw.html").exists()

    def test_raw_html_not_written_by_default(self, fw, monkeypatch, tmp_path):
        _code, out = self._run(fw, monkeypatch, tmp_path)
        assert not (out / "some-post.raw.html").exists()

    def test_slug_override(self, fw, monkeypatch, tmp_path):
        _code, out = self._run(fw, monkeypatch, tmp_path, extra_argv=("--slug", "custom"))
        assert (out / "custom.source.txt").exists()

    def test_undetectable_body_exits_three(self, fw, monkeypatch, tmp_path):
        code, _out = self._run(fw, monkeypatch, tmp_path,
                               html="<html><body><p>too short</p></body></html>")
        assert code == 3
