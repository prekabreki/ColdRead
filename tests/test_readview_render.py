"""The rendered page must be self-contained and structurally faithful.

"Self-contained" is not a nicety: the file is read in a booth off a tablet, and
any external reference is a blank line waiting to happen.
"""

from __future__ import annotations

import re

import pytest

from vo_format.models import Archetype
from vo_format.readview import library
from vo_format.readview.extract import ReadLine, ReadScript, extract_lines
from vo_format.readview.render import render
from vo_format.readview.theme import DARK_MAP


def _script(lines: list[ReadLine], title: str = "Test Script") -> ReadScript:
    return ReadScript(
        title=title,
        lines=lines,
        word_count=sum(len(line.text.split()) for line in lines),
        page_count=1,
        derived="2026-07-30T12:00:00+00:00",
    )


def _line(text: str, **kwargs) -> ReadLine:
    defaults = dict(
        color="#000000",
        bold=False,
        italic=False,
        size_ratio=1.0,
        indent=0,
        gap_before=False,
    )
    defaults.update(kwargs)
    return ReadLine(text=text, **defaults)  # type: ignore[arg-type]


class TestSelfContainment:
    #: Parametrised over --sync because the flag inlines a second asset, and an
    #: asset that reaches for the network is exactly the regression these tests
    #: exist to catch. Sync talks to a RELATIVE href by design; the day it grows
    #: an absolute default, this is what fails.
    @pytest.fixture(params=[None, "/state"], ids=["plain", "synced"])
    def html(self, request, sample_pdf) -> str:
        pdf_path, _ = sample_pdf(Archetype.MULTI_VOICE_DRAMA)
        return render(extract_lines(pdf_path), sync=request.param)

    def test_no_external_script_or_stylesheet(self, html: str) -> None:
        assert "<script src" not in html
        assert "<link" not in html
        assert "@import" not in html

    def test_no_remote_urls(self, html: str) -> None:
        remote = re.findall(r"https?://[^\s\"'<>]+", html)
        assert not remote, f"external URL(s) in output: {remote[:3]}"

    def test_the_only_url_function_is_the_inline_video(self, html: str) -> None:
        for url in re.findall(r"url\(([^)]*)\)", html):
            assert url.strip("\"'").startswith("data:"), url

    def test_css_and_js_are_actually_inlined(self, html: str) -> None:
        assert "<style>" in html and "</style>" in html
        assert "<script>" in html and "</script>" in html
        assert "requestAnimationFrame" in html      # from reader.js
        assert "--line-height" in html              # from reader.css

    def test_keep_awake_video_is_embedded(self, html: str) -> None:
        assert 'id="awake"' in html
        assert "data:video/mp4;base64," in html


class TestSpeedCluster:
    """The wpm control is one grouped pair, and it lives inside the HUD."""

    def _html(self) -> str:
        return render(_script([_line("A line of body text.")]))

    def test_the_edge_strips_are_gone(self) -> None:
        html = self._html()
        assert 'class="zone"' not in html
        assert ".zone" not in html          # the CSS rules too

    def test_the_cluster_is_inside_the_hud(self) -> None:
        # Region-based, and the region is what matters: outside #hud the page
        # still LOOKS right while pressing the cluster scrubs the script,
        # because the touch handler's exemption is keyed on #hud.
        html = self._html()
        hud_open = html.index('<div id="hud">')
        hud_end = html.index('<video id="awake"')
        hud_block = html[hud_open:hud_end]
        assert '<div id="speed">' in hud_block
        assert 'id="wpmdown"' in hud_block
        assert 'id="wpmup"' in hud_block

    def test_minus_comes_before_plus(self) -> None:
        html = self._html()
        assert html.index('id="wpmdown"') < html.index('id="wpmup"')

    def test_the_buttons_are_not_split_by_the_status_readout(self) -> None:
        html = self._html()
        status = html.index('id="status"')
        assert not (html.index('id="wpmdown"') < status < html.index('id="wpmup"'))

    def test_the_cluster_has_a_css_rule(self) -> None:
        assert "#speed {" in self._html()


class TestStructure:
    def test_every_line_becomes_one_paragraph(self) -> None:
        html = render(_script([_line("alpha"), _line("bravo"), _line("charlie")]))
        assert len(re.findall(r'<p class="l[^"]*"', html)) == 3

    def test_gap_before_adds_the_gap_class(self) -> None:
        html = render(_script([_line("alpha"), _line("bravo", gap_before=True)]))
        # Both lines are at the default size_ratio (1.0), so they also carry the
        # "bl" (body-line) class the line-height probe targets.
        assert re.search(r'<p class="l bl gap"[^>]*>bravo</p>', html)

    def test_bold_and_italic_become_classes(self) -> None:
        html = render(_script([_line("b", bold=True), _line("i", italic=True)]))
        assert 'class="l bl b"' in html
        assert 'class="l bl i"' in html

    def test_color_is_emitted_as_its_dark_counterpart(self) -> None:
        html = render(_script([_line("spoken", color="#2563EB")]))
        assert DARK_MAP["#2563eb"] in html
        assert "#2563eb" not in html.lower().split("<style>")[1].split("</style>")[0]

    def test_indent_is_emitted_in_character_units(self) -> None:
        html = render(_script([_line("deep", indent=4)]))
        assert "4ch" in html

    def test_size_ratio_is_emitted_for_non_body_lines(self) -> None:
        html = render(_script([_line("head", size_ratio=1.5), _line("body")]))
        assert "1.5em" in html

    def test_words_per_line_reaches_the_client(self) -> None:
        script = _script([_line("one two three"), _line("four five six")])
        assert 'data-words-per-line="3.0"' in render(script)

    def test_word_count_is_stamped_where_the_library_index_can_read_it(self) -> None:
        """library.py greps `data-words` back out of the served file.

        That is the whole contract between the two modules, and the one thing
        that can break the library's length column silently: dropping the
        attribute leaves every row rendering fine, just without a length.
        """
        script = _script([_line("one two three"), _line("four five six")])
        assert 'data-words="6"' in render(script)

    def test_the_word_count_sits_inside_the_prefix_the_index_reads(self) -> None:
        """`library.py` reads a bounded prefix, so the attribute must be in it.

        The inlined CSS grows ahead of the attribute, so this is a live
        constraint rather than a formality — and its failure mode is silent: an
        attribute past the bound leaves every library row rendering perfectly,
        just with no length on it.
        """
        html = render(_script([_line("x")]))
        assert html.index('data-words="1"') < library._PREFIX_BYTES

    def test_the_progress_bar_is_wired_to_the_property_reader_js_sets(self) -> None:
        # reader.js paints completeness by setting --progress on the root; the
        # bar is a pseudo-element rather than a real one so there is nothing to
        # be missing. If either half of that pairing is renamed, the bar silently
        # stops moving while everything else keeps working.
        html = render(_script([_line("x")]))
        assert "#hud::after" in html
        assert "var(--progress" in html
        assert '"--progress"' in html

    def test_title_and_derived_date_are_shown(self) -> None:
        html = render(_script([_line("x")], title="Kingdom Hearts Dark Road"))
        assert "Kingdom Hearts Dark Road" in html
        assert "2026-07-30" in html

    def test_heading_drops_the_filename_variant_suffix(self) -> None:
        html = render(_script([_line("x")], title="Dark Road - formatted"))
        assert ">Dark Road</p>" in html
        assert "Dark Road - formatted</p>" not in html

    def test_subtitle_names_the_variant_so_cuts_stay_tellable_apart(self) -> None:
        html = render(_script([_line("x")], title="Bloodborne Ep1 - batched"))
        assert "batched cut" in html

    def test_a_title_with_no_variant_is_shown_unchanged(self) -> None:
        html = render(_script([_line("x")], title="Some Episode"))
        assert ">Some Episode</p>" in html
        assert "cut" not in html.split("<style>")[0]

    def test_data_title_keeps_the_full_stem_so_storage_keys_stay_distinct(self) -> None:
        """reader.js derives its localStorage key from data-title.

        If the variant were stripped there, the formatted and batched cuts of one
        title would share saved scroll position, speed and font size.
        """
        a = render(_script([_line("x")], title="Warcraft Ep1 - formatted"))
        b = render(_script([_line("x")], title="Warcraft Ep1 - batched"))
        assert 'data-title="Warcraft Ep1 - formatted"' in a
        assert 'data-title="Warcraft Ep1 - batched"' in b

    def test_a_coloured_line_carries_both_theme_colours(self) -> None:
        """Character colour is the voice-switch cue and must survive both themes.

        The dark palette is illegible on white (as low as 1.29:1), so the light
        theme has to fall back to the original print colour.
        """
        html = render(_script([_line("Alfred:", color="#2563EB")]))
        assert "--c:#729af2" in html, "missing the dark-theme colour"
        assert "--cl:#2563eb" in html, "missing the print colour for light theme"

    def test_body_text_carries_no_colour_so_it_follows_the_theme(self) -> None:
        html = render(_script([_line("plain narration")]))
        assert "--c:" not in html


class TestEscaping:
    def test_html_metacharacters_in_script_text_are_escaped(self) -> None:
        html = render(_script([_line("<script>alert(1)</script> & \"quoted\"")]))
        assert "<script>alert(1)" not in html
        assert "&lt;script&gt;alert(1)" in html
        assert "&amp;" in html

    def test_metacharacters_in_the_title_are_escaped(self) -> None:
        html = render(_script([_line("x")], title='Ep "1" <draft> & more'))
        assert "<draft>" not in html
        assert "&lt;draft&gt;" in html


class TestReaderContract:
    """The rendered page must satisfy every element reader.js requires.

    reader.js resolves these ids unconditionally and immediately calls methods on
    the results, so ONE missing id throws and kills the whole script — leaving a
    static page with no controls and no scrolling, and no visible error.
    Deriving the list from reader.js itself keeps the two sides from drifting.

    Two ids are exempt: `back` renders only under --library and `sync` only
    under --sync. What makes that safe is the `if (el.back)` / `if (el.sync)`
    guards, so the exemption is earned by verifying the guard exists rather than
    by trusting the list below.
    """

    #: ids reader.js looks up but guards before touching.
    OPTIONAL = {"back", "sync"}

    @staticmethod
    def _js() -> str:
        from importlib.resources import files

        return (files("vo_format.readview") / "reader.js").read_text(encoding="utf-8")

    def test_every_id_reader_js_requires_is_rendered(self) -> None:
        required = set(re.findall(r'getElementById\("([^"]+)"\)', self._js()))
        assert required, "parsed no ids out of reader.js — the regex needs updating"

        html = render(_script([_line("alpha")]))
        missing = sorted(i for i in required - self.OPTIONAL if f'id="{i}"' not in html)
        assert not missing, f"reader.js needs ids the page lacks: {missing}"

    def test_optional_ids_are_guarded_before_use(self) -> None:
        """Exempting an id from the contract is only safe if the JS checks it."""
        js = self._js()
        for name in sorted(self.OPTIONAL):
            assert re.search(rf"if \(el\.{name}\)", js), (
                f"'{name}' is exempt from the id contract but reader.js does not "
                f"guard it — omitting the element would throw and kill the page"
            )

    def test_optional_ids_do_render_once_enabled(self) -> None:
        html = render(
            _script([_line("alpha")]), library="index.html", sync="/state"
        )
        missing = sorted(i for i in self.OPTIONAL if f'id="{i}"' not in html)
        assert not missing, f"enabled but never rendered: {missing}"

    def test_a_line_element_exists_for_line_height_measurement(self) -> None:
        # reader.js does querySelector(".l") to measure real line height.
        assert 'class="l' in render(_script([_line("alpha")]))

    def test_body_carries_both_data_attributes_reader_js_reads(self) -> None:
        html = render(_script([_line("one two three")], title="T"))
        assert "data-words-per-line=" in html
        assert "data-title=" in html

    def test_probe_measures_a_body_line_not_an_oversized_leading_line(self) -> None:
        """All ten production PDFs open with a title line at 1.33-1.5em.

        If the line-height probe measures that, every scroll rate runs 33-50%
        fast — and the symptom reads as "the wpm setting lies", nowhere near a
        CSS selector. None of the sample fixtures reproduce the shape (they are
        formatted without a title page), so the shape is built explicitly here.
        """
        from importlib.resources import files

        js = (files("vo_format.readview") / "reader.js").read_text(encoding="utf-8")
        match = re.search(
            r'firstLine:\s*document\.querySelector\("([^"]+)"\)', js
        )
        assert match, "could not find reader.js's line-height probe"
        assert match.group(1) == ".bl", (
            f"probe is {match.group(1)!r}; it must be '.bl' so it can only match "
            "a line at the document's modal size"
        )

        html = render(
            _script(
                [
                    _line("KINGDOM HEARTS DARK ROAD", size_ratio=1.5, bold=True),
                    _line("the corridor was quiet,"),
                    _line("and nothing moved"),
                ]
            )
        )
        probed = [
            m
            for m in re.finditer(r'<p class="([^"]*)"([^>]*)>', html)
            if "bl" in m.group(1).split()
        ]
        assert probed, "no body line carried the bl class"
        assert "font-size" not in probed[0].group(2), (
            f"the probed element has an inline font-size: {probed[0].group(2)!r}"
        )

    def test_oversized_lines_do_not_get_the_body_line_class(self) -> None:
        html = render(_script([_line("BIG HEADING", size_ratio=1.5)]))
        assert 'class="l"' in html or 'class="l ' in html
        assert "bl" not in re.search(r'<p class="([^"]*)"', html).group(1).split()

    def test_line_height_is_read_from_computed_style_not_a_bounding_rect(self) -> None:
        """A block <p>'s bounding rect is the whole box, not one line.

        If the probed line wraps — reachable by pressing A+ on a real script —
        the rect reports double the line height and the page scrolls at twice the
        displayed wpm. Computed lineHeight is per-line and wrap-immune.
        """
        from importlib.resources import files

        js = (files("vo_format.readview") / "reader.js").read_text(encoding="utf-8")
        body = js[js.index("function lineHeightPx"):]
        body = body[: body.index("\n  }")]
        assert "getComputedStyle" in body
        assert "getBoundingClientRect" not in body, (
            "lineHeightPx must not measure a bounding rect — it doubles on wrap"
        )

    def test_couplings_reader_js_relies_on_are_present_in_css_and_html(self) -> None:
        """These are referenced by selector or custom property, not by id.

        Drift makes HUD taps freeze and drag the script (the closest() guard), or
        silently deadens A-/A+ (the --font-size property), with no error anywhere.
        """
        from importlib.resources import files

        pkg = files("vo_format.readview")
        js = (pkg / "reader.js").read_text(encoding="utf-8")
        css = (pkg / "reader.css").read_text(encoding="utf-8")
        html = render(_script([_line("alpha")]))

        assert 'closest("#hud")' in js
        # #speed has no exemption of its own; it inherits #hud's by being a
        # child of it. If the cluster ever moves out, this pairing is the only
        # thing standing between a press and a scrubbed script.
        assert 'id="hud"' in html and 'id="speed"' in html
        assert "--font-size" in js and "--font-size" in css
        assert 'data-theme="light"' in css
        assert 'data-theme="dark"' in html

        # --sync adds three couplings of exactly the same kind. reader.js reads
        # the service href off a body data attribute, and paints the indicator
        # by setting a class name that only reader.css gives meaning to — so a
        # rename on either side leaves a badge that is present, silent and
        # colourless, with nothing logged anywhere.
        sync_js = (pkg / "sync.js").read_text(encoding="utf-8")
        synced = render(_script([_line("alpha")]), sync="/state")
        assert "dataset.sync" in js and 'data-sync="/state"' in synced
        assert "#sync {" in css and 'id="sync"' in synced
        for state in ("pending", "blocked"):
            assert f"#sync.{state}" in css, f"no CSS for the {state} state"
            assert f'"{state}"' in sync_js, (
                f"sync.js no longer reports {state!r}; reader.js sets it as a "
                f"class name straight from state()"
            )


class TestLibraryButton:
    """Opt-in only: a lone read-view has no library to return to.

    The button lives in #hud because reader.js exempts #hud from the
    freeze-and-drag gesture. Anywhere else and a finger that slides while
    leaving would scrub the script on the way out.
    """

    def test_absent_by_default(self) -> None:
        html = render(_script([_line("Body copy.")]))
        assert 'id="back"' not in html
        assert "data-library" not in html

    def test_present_when_a_library_is_given(self) -> None:
        html = render(_script([_line("Body copy.")]), library="index.html")
        assert 'id="back"' in html
        assert 'data-library="index.html"' in html

    def test_button_sits_inside_the_hud(self) -> None:
        html = render(_script([_line("Body copy.")]), library="index.html")
        hud = html.split('<div id="hud">')[1].split("</div>")[0]
        assert 'id="back"' in hud

    def test_library_href_is_escaped(self) -> None:
        """The href reaches an HTML attribute, so a quote must not break out."""
        html = render(_script([_line("x")]), library='i.html" onload="evil()')
        assert 'onload="evil()' not in html
        assert "&quot;" in html


class TestSyncWiring:
    """Shared read state, opt-in via --sync.

    Same reasoning as --library and one more: a page must not issue a request
    its deployment never asked for. Off, the page is byte-for-byte the
    device-local read-view it always was.
    """

    @staticmethod
    def _js() -> str:
        from importlib.resources import files

        return (files("vo_format.readview") / "reader.js").read_text(encoding="utf-8")

    def test_sync_is_off_by_default(self) -> None:
        html = render(_script([_line("A line.")]))
        # The DEFINITION, not the name: reader.js always mentions coldreadSync,
        # because the decision to use it is made at run time from data-sync.
        assert "function coldreadSync(" not in html
        assert "data-sync" not in html
        assert 'id="sync"' not in html

    def test_sync_is_inlined_when_asked_for(self) -> None:
        html = render(_script([_line("A line.")]), sync="/state")
        assert "function coldreadSync(" in html
        assert 'data-sync="/state"' in html

    def test_the_sync_client_is_inlined_before_the_reader(self) -> None:
        """Order, not just presence.

        reader.js resolves coldreadSync while it is setting up. Inlined the other
        way round the symbol is undefined at that moment and the `typeof` guard
        quietly falls back to device-local storage — a page that looks perfect,
        works perfectly, and syncs nothing.
        """
        html = render(_script([_line("A line.")]), sync="/state")
        assert html.index("function coldreadSync(") < html.index(
            "function pxPerSecond("
        ), "sync.js is inlined after reader.js; coldreadSync will be undefined"

    def test_the_sync_client_is_still_self_contained(self) -> None:
        html = render(_script([_line("A line.")]), sync="/state")
        assert "<script src" not in html
        assert "<link" not in html

    def test_the_indicator_has_a_home(self) -> None:
        html = render(_script([_line("A line.")]), sync="/state")
        assert 'id="sync"' in html

    def test_the_indicator_sits_inside_the_hud(self) -> None:
        # Not decoration: #hud is what the touch handler exempts from the
        # freeze-and-drag gesture. Anywhere else in the page and the badge is a
        # patch of script that scrubs when brushed.
        html = render(_script([_line("A line.")]), sync="/state")
        hud = html[html.index('<div id="hud">') : html.index('<video id="awake"')]
        assert 'id="sync"' in hud

    def test_the_indicator_follows_the_status_readout(self) -> None:
        html = render(_script([_line("A line.")]), sync="/state")
        assert html.index('id="status"') < html.index('id="sync"')

    def test_the_sync_href_is_escaped(self) -> None:
        """It reaches an HTML attribute, so a quote must not break out."""
        html = render(_script([_line("x")]), sync='/state" onload="evil()')
        assert 'onload="evil()' not in html
        assert "&quot;" in html

    def test_the_store_delegates_rather_than_being_replaced(self) -> None:
        """`store` is the seam, and its get/set signatures must not move.

        Every call site in reader.js — wpm, size, theme, pos, mark — goes through
        it, so a changed signature would be a five-way silent breakage.
        """
        js = self._js()
        assert 'coldreadSync("coldread", syncHref)' in js
        assert "shared.get(namespace, key, fallback)" in js
        assert "shared.set(namespace, key, value)" in js

    def test_the_namespace_is_the_one_the_state_service_expects(self) -> None:
        # The design's data model names it `script:<full stem>`, and the full
        # stem is what keeps a formatted and a batched cut apart.
        js = self._js()
        assert 'var namespace = "script:" + (body.dataset.title || "untitled");' in js

    @pytest.mark.parametrize("event", ["touchstart", "mousedown"])
    def test_the_input_handlers_claim_the_page_before_anything_else(
        self, event: str
    ) -> None:
        """`touchedYet` must be set ahead of each handler's #hud early return.

        Pressing A+ is every bit as much "I am reading this" as dragging the
        script is, and both handlers bail out on #hud targets before doing
        anything else — so the flag has to be the first statement or a whole
        session spent on the controls stays eligible for a remote seek.
        """
        js = self._js()
        match = re.search(
            rf'addEventListener\("{event}", function \(e\) \{{\n\s*(.+)\n', js
        )
        assert match, f"could not find the document-level {event} handler"
        assert match.group(1).strip() == "touchedYet = true;", (
            f"the {event} handler starts with {match.group(1).strip()!r}; it must "
            "set touchedYet first, before the #hud early return"
        )

    def test_a_remote_position_is_applied_only_before_the_first_touch(self) -> None:
        """The one rule this feature is not allowed to get wrong.

        The state fetch is asynchronous, so a position the server won can land
        seconds into a read. Applying it then yanks the script out from under
        someone mid-sentence, which is worse than not syncing it at all. After
        first interaction the value is still stored — it takes effect next load.
        """
        js = self._js()
        calls = [
            line.strip()
            for line in js.splitlines()
            if re.search(r"(?<!function )\brestore\(\)", line)
        ]
        assert calls, "nothing calls restore() — the page never resumes anywhere"
        assert "if (!touchedYet) { restore(); }" in calls, (
            "no touchedYet-guarded restore(): either sync never re-applies what "
            "the server sent, or it does so unconditionally"
        )
        # Exactly one unguarded call, and it is the synchronous load-time one.
        # Every other route into restore() runs asynchronously, by which time the
        # reader may already be reading.
        unguarded = [c for c in calls if "touchedYet" not in c]
        assert unguarded == ["restore();"], (
            f"unexpected unguarded restore() call(s): {unguarded}"
        )

    def test_the_guarded_restore_is_the_sync_callback(self) -> None:
        # The guard is only worth anything if it sits on the path a server
        # update actually takes into the page.
        js = self._js()
        block = js[js.index("shared.onchange(") :]
        block = block[: block.index("\n  }")]
        assert "if (!touchedYet) { restore(); }" in block

    def test_the_indicator_is_painted_from_the_clients_own_state_names(self) -> None:
        js = self._js()
        block = js[js.index("shared.onchange(") :]
        block = block[: block.index("\n  }")]
        assert 'state === "clean"' in block
        assert "el.sync.className = state;" in block


class TestCountdown:
    """The HUD says how long is left, derived from the scroll engine."""

    def _html(self) -> str:
        return render(_script([_line("A line of body text.")]))

    def test_the_countdown_helpers_are_inlined(self) -> None:
        html = self._html()
        assert "function clockText(" in html
        assert "function secondsLeft(" in html

    def test_it_is_derived_from_the_scroll_speed_not_the_word_count(self) -> None:
        # The whole point: one source of truth with the autoscroll. A
        # word-count estimate is a parallel calculation that can drift from
        # the thing actually moving the page.
        html = self._html()
        assert "(maxScroll() - pos) / pxPerSecond()" in html

    def test_the_readout_includes_the_clock(self) -> None:
        assert 'clockText(secondsLeft())' in self._html()

    def test_the_status_field_is_wide_enough_for_it(self) -> None:
        # 15ch fitted "42% · 150 wpm"; the clock needs more or the HUD jitters
        # as the digits change.
        html = self._html()
        assert re.search(r"#status\s*\{[^}]*min-width:\s*22ch", html)
