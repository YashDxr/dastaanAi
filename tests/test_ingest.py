"""Document extraction.

The parsing libraries are trusted to parse; what is tested here is the code
around them - deciding what a file actually is, and removing the page furniture
that would otherwise be fed to a model as if it were story.
"""

import pytest
from daastaan_agent import ingest, prompts
from daastaan_contracts import limits


class TestSniff:
    """Content type comes from the bytes, not the client. A browser will label
    anything, and a mislabelled file should be found here rather than inside a
    parser."""

    def test_pdf(self) -> None:
        assert ingest.sniff(b"%PDF-1.7\n...") == ingest.PDF

    def test_png(self) -> None:
        assert ingest.sniff(b"\x89PNG\r\n\x1a\n\x00\x00") == "image/png"

    def test_jpeg(self) -> None:
        assert ingest.sniff(b"\xff\xd8\xff\xe0\x00\x10JFIF") == "image/jpeg"

    def test_webp(self) -> None:
        assert ingest.sniff(b"RIFF\x24\x00\x00\x00WEBPVP8 ") == "image/webp"

    def test_legacy_doc_is_identified_so_it_can_be_refused(self) -> None:
        assert ingest.sniff(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 8) == ingest.DOC

    def test_plain_text(self) -> None:
        assert ingest.sniff(b"Once upon a time.") == "text/plain"

    def test_a_declared_type_does_not_override_the_bytes(self) -> None:
        assert ingest.sniff(b"%PDF-1.4", declared="text/plain") == ingest.PDF

    def test_binary_junk_is_not_mistaken_for_text(self) -> None:
        assert ingest.sniff(b"\x00\x01\x02\x03garbage") == "application/octet-stream"


class TestExtractRouting:
    def test_legacy_doc_gets_an_actionable_message(self) -> None:
        data = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32
        with pytest.raises(ingest.ExtractionError, match="save it as"):
            ingest.extract(data, ingest.DOC)

    def test_unsupported_binary_is_refused(self) -> None:
        with pytest.raises(ingest.ExtractionError, match="not supported"):
            ingest.extract(b"\x00\x01\x02\x03\x04", "application/octet-stream")

    def test_plain_text_round_trips(self) -> None:
        result = ingest.extract(b"The rain did not stop for nine days.", "text/plain")
        assert result.method is ingest.Method.PLAIN_TEXT
        assert "nine days" in result.text

    def test_utf8_is_decoded(self) -> None:
        result = ingest.extract("Café — naïve".encode(), "text/plain")
        assert "Café" in result.text

    def test_an_empty_file_is_an_error_not_an_empty_story(self) -> None:
        with pytest.raises(ingest.ExtractionError):
            ingest.extract(b"   \n\n  ", "text/plain")


class TestStripBoilerplate:
    def test_page_numbers_are_dropped(self) -> None:
        pages = ["The door opened.\n12", "She stepped through.\n13"]
        text = ingest.strip_boilerplate(pages)
        assert "12" not in text
        assert "The door opened." in text

    def test_roman_numeral_page_numbers_are_dropped(self) -> None:
        assert "xiv" not in ingest.strip_boilerplate(["Prologue text here.\nxiv"])

    def test_running_headers_are_dropped(self) -> None:
        pages = [f"A TALE OF TWO CITIES\nParagraph {i} of the story.\n{i}" for i in range(6)]
        text = ingest.strip_boilerplate(pages)
        assert "A TALE OF TWO CITIES" not in text
        assert "Paragraph 3 of the story." in text

    def test_a_repeated_line_mid_page_is_kept(self) -> None:
        """A refrain is story. Only lines at the edge of a page are chrome."""
        pages = [f"Opening {i}.\nAnd so it goes.\nClosing {i}." for i in range(6)]
        assert "And so it goes." in ingest.strip_boilerplate(pages)

    def test_two_pages_are_too_few_to_infer_a_header(self) -> None:
        pages = ["Chapter One\nHe waited.", "Chapter One\nShe did not."]
        assert "Chapter One" in ingest.strip_boilerplate(pages)

    def test_hyphenation_across_a_line_break_is_repaired(self) -> None:
        assert "wonderful" in ingest.strip_boilerplate(["It was a won-\nderful evening."])

    def test_runs_of_blank_lines_collapse(self) -> None:
        assert "\n\n\n" not in ingest.strip_boilerplate(["One.\n\n\n\n\nTwo."])

    def test_runs_of_spaces_collapse(self) -> None:
        assert "  " not in ingest.strip_boilerplate(["Wide     gaps    here."])

    def test_no_pages_is_empty_not_an_error(self) -> None:
        assert ingest.strip_boilerplate([]) == ""


class TestCleanupPrompt:
    """A 31,000-character story once came back from this stage as an 1,100-
    character synopsis, which is within any upper bound and useless to the script
    stage. These assertions pin the wording that stopped it."""

    def test_summarising_is_forbidden_in_the_prompt(self) -> None:
        text = prompts.story_cleanup_prompt(max_chars=limits.MAX_STORY_INPUT_CHARS)
        assert "NOT a summary" in text
        assert "word for word" in text

    def test_budget_states_a_floor_and_not_only_a_ceiling(self) -> None:
        """The upper bound alone let the model return a twentieth of it."""
        budget = limits.MAX_STORY_INPUT_CHARS
        text = prompts.story_cleanup_prompt(max_chars=budget)
        assert str(budget) in text
        assert str(int(budget * 0.6)) in text

    def test_short_sources_are_passed_through_whole(self) -> None:
        text = prompts.story_cleanup_prompt(max_chars=limits.MAX_STORY_INPUT_CHARS)
        assert "do not shorten" in text

    def test_input_budget_matches_the_longest_script_the_pipeline_can_emit(self) -> None:
        """Accepting less than the script stage can use means throwing away story
        before the stage that knows how to choose what to keep."""
        assert limits.MAX_STORY_INPUT_CHARS == limits.MAX_LINES * limits.MAX_LINE_CHARS
