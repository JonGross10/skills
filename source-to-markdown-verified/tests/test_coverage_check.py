"""Unit tests for the source-to-markdown-verified coverage checker.

The .xlsx/.docx/.pptx extractors are exercised against fixtures built in
tmp_path, and skip when their optional library is absent. The .pdf branch
is not covered — it needs a real PDF fixture, and the vault's own PDFs are
client material.
"""
import pytest


class TestNormalize:
    def test_curly_quotes_become_straight(self, cc):
        assert cc.normalize("it\u2019s \u201cbig\u201d") == "it's \"big\""

    def test_dashes_become_hyphens(self, cc):
        assert cc.normalize("2024\u20132025 \u2014 growth") == "2024-2025 - growth"

    def test_collapses_whitespace_and_strips(self, cc):
        assert cc.normalize("  a\n\tb   c  ") == "a b c"


class TestNormalizeForMatch:
    def test_lowercases_and_drops_punctuation(self, cc):
        assert cc.normalize_for_match("Revenue, Growth: 40%!") == "revenue growth 40"

    def test_keeps_word_characters(self, cc):
        assert cc.normalize_for_match("ARR_2025 up") == "arr_2025 up"


class TestExtractTokens:
    def test_percentages(self, cc):
        assert ("pct", "42%") in cc.extract_tokens("margin grew 42% last year")

    def test_decimal_percentage(self, cc):
        assert ("pct", "12.5%") in cc.extract_tokens("churn of 12.5% observed")

    @pytest.mark.parametrize("label", ["0%", "25%", "50%", "75%", "100%"])
    def test_chart_axis_labels_skipped(self, cc, label):
        assert ("pct", label) not in cc.extract_tokens(f"axis {label} tick")

    @pytest.mark.parametrize("artifact", ["00%", "05%"])
    def test_leading_zero_artifacts_skipped(self, cc, artifact):
        assert ("pct", artifact) not in cc.extract_tokens(f"bleed {artifact} bleed")

    def test_leading_zero_decimal_is_kept(self, cc):
        assert ("pct", "0.5%") in cc.extract_tokens("a mere 0.5% of users")

    def test_dollar_amount_with_magnitude_word(self, cc):
        assert ("money", "$4.2 million") in cc.extract_tokens("raised $4.2 million total")

    def test_dollar_amount_with_thousands_separator(self, cc):
        assert ("money", "$1,250") in cc.extract_tokens("fee is $1,250 per seat")

    def test_us_dollar_prefix(self, cc):
        assert ("money", "US$3.1 billion") in cc.extract_tokens("US$3.1 billion in claims")

    @pytest.mark.parametrize("year", ["1998", "2026"])
    def test_years(self, cc, year):
        assert ("year", year) in cc.extract_tokens(f"filed in {year} by counsel")

    def test_four_digit_non_year_ignored(self, cc):
        assert ("year", "3200") not in cc.extract_tokens("about 3200 units shipped")

    def test_phrase_fingerprint_is_first_six_words_lowercased(self, cc):
        text = "The Company Reported Strong Recurring Revenue across every segment."
        assert ("phrase", "the company reported strong recurring revenue") in cc.extract_tokens(text)

    def test_short_sentences_yield_no_phrase(self, cc):
        assert not [t for t in cc.extract_tokens("Too short.") if t[0] == "phrase"]

    def test_sentence_under_six_words_yields_no_phrase(self, cc):
        long_but_few_words = "Antidisestablishmentarianism notwithstanding, extraordinarily consequential."
        assert not [t for t in cc.extract_tokens(long_but_few_words) if t[0] == "phrase"]

    def test_very_long_sentence_skipped(self, cc):
        assert not [t for t in cc.extract_tokens("word " * 60) if t[0] == "phrase"]

    def test_tokens_are_deduplicated(self, cc):
        tokens = cc.extract_tokens("up 42% then 42% again")
        assert [t for t in tokens if t[0] == "pct"] == [("pct", "42%")]


class TestCheckCoverage:
    def test_present_numeric_token_is_not_missing(self, cc):
        missing, fps = cc.check_coverage({("pct", "42%")}, "margins hit 42% in Q3")
        assert (missing, fps) == ([], [])

    def test_absent_numeric_token_is_truly_missing(self, cc):
        missing, fps = cc.check_coverage({("money", "$4.2 million")}, "no numbers here")
        assert missing == [("money", "$4.2 million")]
        assert fps == []

    def test_matching_is_case_insensitive(self, cc):
        missing, _ = cc.check_coverage({("money", "$4.2 Million")}, "raised $4.2 million")
        assert missing == []

    def test_restructured_phrase_is_a_likely_false_positive(self, cc):
        tokens = {("phrase", "the company reported strong recurring revenue")}
        md = "The company reported strong results, with recurring revenue up."
        missing, fps = cc.check_coverage(tokens, md)
        assert missing == []
        assert fps == [("phrase", "the company reported strong recurring revenue")]

    def test_phrase_absent_entirely_is_truly_missing(self, cc):
        phrase = ("phrase", "the company reported strong recurring revenue")
        missing, fps = cc.check_coverage({phrase}, "unrelated markdown body")
        assert missing == [phrase]
        assert fps == []

    def test_phrase_shorter_than_four_words_cannot_overlap_match(self, cc):
        missing, fps = cc.check_coverage({("phrase", "alpha beta gamma")}, "alpha beta delta")
        assert missing == [("phrase", "alpha beta gamma")]
        assert fps == []

    def test_punctuation_only_difference_is_a_clean_match(self, cc):
        tokens = {("phrase", "revenue, growth and margin expansion")}
        missing, fps = cc.check_coverage(tokens, "Revenue growth and margin expansion continued.")
        assert missing == []
        assert fps == []

    def test_markdown_syntax_around_words_is_a_clean_match(self, cc):
        tokens = {("phrase", "julian: how are you handling data")}
        md = "**Julian:** How are you handling [data](https://example.com) mapping?"
        missing, fps = cc.check_coverage(tokens, md)
        assert missing == []
        assert fps == []


class TestCheckUnsourcedNumbers:
    def test_number_only_in_markdown_is_flagged(self, cc):
        unsourced = cc.check_unsourced_numbers("we grew 42%", "we grew a lot")
        assert unsourced == [("pct", "42%")]

    def test_number_present_in_source_is_not_flagged(self, cc):
        assert cc.check_unsourced_numbers("we grew 42%", "we grew 42% last year") == []

    def test_years_are_never_flagged(self, cc):
        assert cc.check_unsourced_numbers("converted in 2026", "no year in source") == []

    def test_phrases_are_never_flagged(self, cc):
        md = "This added sentence never appeared in the original source document."
        assert cc.check_unsourced_numbers(md, "unrelated source text") == []

    def test_source_smart_punctuation_is_normalized_before_comparing(self, cc):
        assert cc.check_unsourced_numbers("worth $1,250", "the fee\u2014worth $1,250\u2014stands") == []


class TestExtractSourceText:
    @pytest.mark.parametrize("suffix", [".txt", ".md"])
    def test_reads_plain_text_sources(self, cc, tmp_path, suffix):
        path = tmp_path / f"src{suffix}"
        path.write_text("hello vault", encoding="utf-8")
        assert cc.extract_source_text(path) == "hello vault"

    def test_suffix_matching_is_case_insensitive(self, cc, tmp_path):
        path = tmp_path / "SRC.MD"
        path.write_text("shouty", encoding="utf-8")
        assert cc.extract_source_text(path) == "shouty"

    def test_undecodable_bytes_are_replaced_not_raised(self, cc, tmp_path):
        path = tmp_path / "src.txt"
        path.write_bytes(b"caf\xff")
        assert cc.extract_source_text(path).startswith("caf")

    def test_unsupported_suffix_raises(self, cc, tmp_path):
        path = tmp_path / "src.rtf"
        path.write_text("nope", encoding="utf-8")
        with pytest.raises(cc.CannotRun, match=r"Unsupported source type: \.rtf"):
            cc.extract_source_text(path)

    def test_xlsx_cells_across_sheets(self, cc, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        wb = openpyxl.Workbook()
        wb.active.append(["revenue", 4200000, None])
        wb.create_sheet("Notes").append(["margin 42%"])
        path = tmp_path / "src.xlsx"
        wb.save(path)
        text = cc.extract_source_text(path)
        assert "revenue" in text
        assert "4200000" in text
        assert "margin 42%" in text
        assert "None" not in text

    def test_docx_paragraphs_and_tables(self, cc, tmp_path):
        docx = pytest.importorskip("docx")
        doc = docx.Document()
        doc.add_paragraph("body paragraph")
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "cell one"
        table.rows[0].cells[1].text = "cell two"
        path = tmp_path / "src.docx"
        doc.save(path)
        text = cc.extract_source_text(path)
        assert "body paragraph" in text
        assert "cell one" in text
        assert "cell two" in text

    def test_pptx_shapes_tables_and_notes(self, cc, tmp_path):
        pptx = pytest.importorskip("pptx")
        prs = pptx.Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.title.text = "slide title"
        inches = pptx.util.Inches
        table = slide.shapes.add_table(1, 2, inches(1), inches(2), inches(4), inches(1)).table
        table.rows[0].cells[0].text = "table cell"
        slide.notes_slide.notes_text_frame.text = "speaker note"
        path = tmp_path / "src.pptx"
        prs.save(path)
        text = cc.extract_source_text(path)
        assert "slide title" in text
        assert "table cell" in text
        assert "speaker note" in text


class TestMain:
    """End-to-end runs of the CLI, checking exit codes and report output."""

    LONG_SOURCE = (
        "The company reported strong recurring revenue across every segment. "
        "Annual contract value reached $4.2 million by the end of the period. "
        "Gross margin expanded to 42% while churn settled at 12.5% overall. "
        "Management expects the same trajectory to continue through 2026.\n"
    )

    def _run(self, cc, monkeypatch, capsys, tmp_path, source_text, md_text, *extra,
             write_source=True, write_markdown=True):
        source = tmp_path / "source.txt"
        markdown = tmp_path / "note.md"
        if write_source:
            source.write_text(source_text, encoding="utf-8")
        if write_markdown:
            markdown.write_text(md_text, encoding="utf-8")
        monkeypatch.setattr(
            cc.sys, "argv",
            ["coverage_check.py", str(source), str(markdown), *extra],
        )
        with pytest.raises(SystemExit) as exc:
            cc.main()
        captured = capsys.readouterr()
        return exc.value.code, captured.out, captured.err

    def test_faithful_markdown_exits_zero(self, cc, monkeypatch, capsys, tmp_path):
        code, out, _ = self._run(cc, monkeypatch, capsys, tmp_path,
                                 self.LONG_SOURCE, self.LONG_SOURCE)
        assert code == 0
        assert "No truly-missing items detected." in out

    def test_missing_content_exits_one(self, cc, monkeypatch, capsys, tmp_path):
        md = self.LONG_SOURCE.replace("$4.2 million", "an undisclosed sum")
        code, out, _ = self._run(cc, monkeypatch, capsys, tmp_path, self.LONG_SOURCE, md)
        assert code == 1
        assert "TRULY MISSING" in out
        assert "$4.2 million" in out

    def test_invented_number_exits_one(self, cc, monkeypatch, capsys, tmp_path):
        md = self.LONG_SOURCE + "Net new logos grew 91% year over year.\n"
        code, out, _ = self._run(cc, monkeypatch, capsys, tmp_path, self.LONG_SOURCE, md)
        assert code == 1
        assert "IN MARKDOWN BUT NOT IN SOURCE" in out
        assert "91%" in out

    def test_thin_source_exits_two(self, cc, monkeypatch, capsys, tmp_path):
        code, out, _ = self._run(cc, monkeypatch, capsys, tmp_path,
                                 "scanned pdf, no text layer", self.LONG_SOURCE)
        assert code == 2
        assert "COVERAGE CHECK NOT MEANINGFUL" in out

    def test_show_likely_fp_flag_lists_moved_fingerprints(self, cc, monkeypatch, capsys, tmp_path):
        md = self.LONG_SOURCE.replace(
            "The company reported strong recurring revenue across every segment.",
            "The company reported strong results, with recurring revenue in every segment.",
        )
        code, out, _ = self._run(cc, monkeypatch, capsys, tmp_path,
                                 self.LONG_SOURCE, md, "--show-likely-fp")
        assert code == 0
        assert "LIKELY FALSE POSITIVES" in out

    def test_likely_fps_hidden_without_flag(self, cc, monkeypatch, capsys, tmp_path):
        md = self.LONG_SOURCE.replace(
            "The company reported strong recurring revenue across every segment.",
            "The company reported strong results, with recurring revenue in every segment.",
        )
        _, out, _err = self._run(cc, monkeypatch, capsys, tmp_path, self.LONG_SOURCE, md)
        assert "LIKELY FALSE POSITIVES" not in out

    def test_missing_source_file_is_reported(self, cc, monkeypatch, capsys, tmp_path):
        code, _out, err = self._run(cc, monkeypatch, capsys, tmp_path,
                                    self.LONG_SOURCE, self.LONG_SOURCE, write_source=False)
        assert code == 3
        assert "Source file not found" in err

    def test_missing_markdown_file_is_reported(self, cc, monkeypatch, capsys, tmp_path):
        code, _out, err = self._run(cc, monkeypatch, capsys, tmp_path,
                                    self.LONG_SOURCE, self.LONG_SOURCE, write_markdown=False)
        assert code == 3
        assert "Markdown file not found" in err

    def test_unsupported_source_type_exits_three(self, cc, monkeypatch, capsys, tmp_path):
        source = tmp_path / "source.rtf"
        source.write_text(self.LONG_SOURCE, encoding="utf-8")
        markdown = tmp_path / "note.md"
        markdown.write_text(self.LONG_SOURCE, encoding="utf-8")
        monkeypatch.setattr(
            cc.sys, "argv", ["coverage_check.py", str(source), str(markdown)],
        )
        with pytest.raises(SystemExit) as exc:
            cc.main()
        assert exc.value.code == 3
        out = capsys.readouterr().out
        assert "COVERAGE CHECK COULD NOT RUN" in out
        assert "Unsupported source type: .rtf" in out


def test_min_meaningful_source_chars_threshold(cc):
    assert cc.MIN_MEANINGFUL_SOURCE_CHARS == 200
