"""CLI behavior, with an emphasis on refusing to fail quietly.

This repo has shipped silent content loss at exit 0 twice (issues #138, #139).
An empty or missing read-view must be loud.
"""

from __future__ import annotations

from vo_format.models import Archetype
from vo_format.readview.cli import main, readview_path_for


class TestHappyPath:
    def test_writes_an_html_file_beside_the_pdf(self, sample_pdf, capsys) -> None:
        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        assert main([str(pdf_path)]) == 0
        out = readview_path_for(pdf_path)
        assert out.is_file()
        assert out.read_text(encoding="utf-8").startswith("<!doctype html>")

    def test_output_name_follows_the_formatted_convention(self, tmp_path) -> None:
        pdf = tmp_path / "Kingdom Hearts Dark Road - formatted.pdf"
        assert readview_path_for(pdf).name == (
            "Kingdom Hearts Dark Road - formatted - readview.html"
        )

    def test_a_pdf_without_the_suffix_still_gets_a_sane_name(self, tmp_path) -> None:
        assert readview_path_for(tmp_path / "Ep1.pdf").name == "Ep1 - readview.html"

    def test_variant_pdfs_do_not_collide_on_one_output_name(self, tmp_path) -> None:
        """`X - formatted.pdf` and `X - batched.pdf` are different documents.

        Three titles in the production folders ship both. Collapsing them onto
        one filename silently overwrites one with the other, and the mtime skip
        can then serve the wrong cut while reporting success.
        """
        base = "Bloodborne Ep1 - Blood Ministry"
        a = readview_path_for(tmp_path / f"{base} - formatted.pdf")
        b = readview_path_for(tmp_path / f"{base} - batched.pdf")
        assert a != b, f"both variants map to {a.name}"
        assert "formatted" in a.name and "batched" in b.name

    def test_prints_the_line_count_canary(self, sample_pdf, capsys) -> None:
        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        main([str(pdf_path)])
        captured = capsys.readouterr().out
        assert "extracted" in captured
        assert "lines from" in captured
        assert "pages" in captured

    def test_accepts_several_pdfs_at_once(self, sample_pdf) -> None:
        first, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        second, _ = sample_pdf(Archetype.MULTI_VOICE_DRAMA)
        assert main([str(first), str(second)]) == 0
        assert readview_path_for(first).is_file()
        assert readview_path_for(second).is_file()


class TestIdempotence:
    def test_skips_when_the_html_is_newer_than_the_pdf(
        self, sample_pdf, capsys
    ) -> None:
        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        main([str(pdf_path)])
        out = readview_path_for(pdf_path)
        first = out.read_text(encoding="utf-8")
        out.write_text("SENTINEL", encoding="utf-8")

        assert main([str(pdf_path)]) == 0
        assert out.read_text(encoding="utf-8") == "SENTINEL"
        assert "skip" in capsys.readouterr().out.lower()
        assert first  # the first render did happen

    def test_force_rewrites_regardless(self, sample_pdf) -> None:
        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        main([str(pdf_path)])
        out = readview_path_for(pdf_path)
        out.write_text("SENTINEL", encoding="utf-8")

        assert main([str(pdf_path), "--force"]) == 0
        assert out.read_text(encoding="utf-8") != "SENTINEL"

    def test_rerenders_when_the_pdf_is_newer(self, sample_pdf) -> None:
        import os
        import time

        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        main([str(pdf_path)])
        out = readview_path_for(pdf_path)
        out.write_text("SENTINEL", encoding="utf-8")
        # Touch the PDF into the future so it is unambiguously newer.
        future = time.time() + 60
        os.utime(pdf_path, (future, future))

        assert main([str(pdf_path)]) == 0
        assert out.read_text(encoding="utf-8") != "SENTINEL"


class TestFailsLoudly:
    def test_a_textless_pdf_exits_nonzero_and_writes_nothing(
        self, tmp_path, capsys
    ) -> None:
        import fitz

        doc = fitz.open()
        doc.new_page()
        blank = tmp_path / "blank.pdf"
        doc.save(str(blank))
        doc.close()

        assert main([str(blank)]) != 0
        assert not readview_path_for(blank).exists()
        assert "no extractable text" in capsys.readouterr().err

    def test_a_missing_file_exits_nonzero(self, tmp_path, capsys) -> None:
        assert main([str(tmp_path / "nope.pdf")]) != 0
        assert capsys.readouterr().err.strip()

    def test_one_bad_file_does_not_stop_the_others(
        self, sample_pdf, tmp_path
    ) -> None:
        good, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        bad = tmp_path / "missing.pdf"

        assert main([str(bad), str(good)]) != 0, "must still report failure"
        assert readview_path_for(good).is_file(), "the good file must be rendered"

    def test_no_arguments_exits_nonzero(self) -> None:
        assert main([]) != 0


class TestLibraryFlag:
    def test_no_button_without_the_flag(self, sample_pdf) -> None:
        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        assert main([str(pdf_path)]) == 0
        assert 'id="back"' not in readview_path_for(pdf_path).read_text("utf-8")

    def test_flag_threads_through_to_the_page(self, sample_pdf) -> None:
        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        assert main([str(pdf_path), "--library", "index.html"]) == 0
        html = readview_path_for(pdf_path).read_text("utf-8")
        assert 'data-library="index.html"' in html
        assert 'id="back"' in html
