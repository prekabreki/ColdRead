"""Assemble a ReadScript into one self-contained teleprompter page.

Everything is inlined — CSS, JS, and the keep-awake video as a data URI — so the
file works with the network off, which is the only condition that matters in a
booth.
"""

from __future__ import annotations

import re
from html import escape
from importlib.resources import files

from .assets import KEEP_AWAKE_MP4_BASE64
from .extract import ReadLine, ReadScript
from .theme import DARK_BACKGROUND, dark_color

_VARIANT_RE = re.compile(r"\s+-\s+(formatted|batched)$", re.IGNORECASE)


def _split_variant(title: str) -> tuple[str, str]:
    """Separate a display title from its cut variant.

    Filenames keep the variant so a formatted and a batched cut of one title
    cannot overwrite each other, but "Dark Road - formatted" is bookkeeping, not
    something a reader needs at the top of the page. Returns (display, variant),
    where variant is "" when the title carries none.
    """
    match = _VARIANT_RE.search(title)
    if not match:
        return title, ""
    return title[: match.start()], match.group(1).lower()


def _asset(name: str) -> str:
    return (files(__package__) / name).read_text(encoding="utf-8")


def _line_html(line: ReadLine) -> str:
    classes = ["l"]
    if line.size_ratio == 1.0:
        # The probe reader.js uses to measure real line height must land on a
        # body-sized line. Every production PDF opens with a title line at
        # 1.33-1.5em, so "first line" is the wrong target.
        classes.append("bl")
    if line.gap_before:
        classes.append("gap")
    if line.bold:
        classes.append("b")
    if line.italic:
        classes.append("i")

    styles: list[str] = []
    color = dark_color(line.color)
    # Body text takes its color from the theme's --fg so the light/dark toggle
    # can move it; only genuinely colored lines carry explicit values. A colored
    # line carries BOTH its dark-theme color (--c) and its original print color
    # (--cl) as custom properties rather than a single inline `color:` — an
    # inline color would outrank the light-theme CSS rule and strand the reader
    # with dark-palette colors (as low as 1.29:1 contrast) on a white background.
    if color.lower() != dark_color("#000000").lower():
        styles.append(f"--c:{color};--cl:{line.color.lower()}")
    if line.indent:
        styles.append(f"margin-left:{line.indent}ch")
    if line.size_ratio != 1.0:
        styles.append(f"font-size:{line.size_ratio:g}em")

    attrs = f' style="{";".join(styles)}"' if styles else ""
    return f'<p class="{" ".join(classes)}"{attrs}>{escape(line.text)}</p>'


def render(
    script: ReadScript,
    library: str | None = None,
    sync: str | None = None,
) -> str:
    """Render `script` as one self-contained HTML document.

    `library` is the href of a library index to offer a way back to. It is
    opt-in because a read-view knows nothing about a library: the index is
    built by whatever publishes these pages, so defaulting the button on would
    ship a dead link to everyone converting a single PDF on their own machine.

    `sync` is the href of a state service to share read marks and preferences
    through, and is opt-in for the same reason and one more: a page must not
    issue a request its deployment never asked for. Without it the page behaves
    exactly as before, storing everything in this device's `localStorage`.
    """
    title = escape(script.title)
    display, variant = _split_variant(script.title)
    display = escape(display)
    variant_suffix = f" &middot; {variant} cut" if variant else ""
    lines = "\n".join(_line_html(line) for line in script.lines)
    # The button lives in #hud on purpose. The touch handler exempts #hud from
    # the freeze-and-drag gesture, so a control placed anywhere else would scrub
    # the script under the finger on its way out. #speed rides on the same
    # exemption, which is why it is a child of #hud rather than a sibling.
    library_attr = f' data-library="{escape(library)}"' if library else ""
    back_button = (
        '<button id="back" type="button" aria-label="Back to library">'
        "&larr; Library</button>\n"
        if library
        else ""
    )
    # The indicator rides in #hud for the same reason the Library button does,
    # and it is a <span> rather than a <button> because there is nothing to
    # press: it reports, it does not act.
    sync_attr = f' data-sync="{escape(sync)}"' if sync else ""
    sync_badge = '<span id="sync"></span>\n' if sync else ""
    # BEFORE reader.js, not after. reader.js calls coldreadSync() while it is
    # setting up; inlined the other way round the symbol is undefined at that
    # moment and the page falls back to device-local storage — silently, which
    # is the one failure mode this feature is not allowed to have.
    sync_js = f"<script>\n{_asset('sync.js')}\n</script>\n" if sync else ""
    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, \
maximum-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="{DARK_BACKGROUND}">
<title>{title}</title>
<style>
{_asset("reader.css")}
</style>
</head>
<body data-words-per-line="{script.words_per_line:.1f}" data-title="{title}"\
 data-words="{script.word_count}"{library_attr}{sync_attr}>
<div id="script">
<p class="hdr l b" style="font-size:1.4em">{display}</p>
<p class="hdr l i">{len(script.lines)} lines &middot; {script.word_count} words \
&middot; derived {escape(script.derived[:10])}{variant_suffix}</p>
{lines}
</div>
<div id="hud">
{back_button}<button id="smaller" type="button" aria-label="Smaller text">\
A&minus;</button>
<button id="bigger" type="button" aria-label="Larger text">A+</button>
<button id="play" type="button" aria-label="Play or pause">&#9654;</button>
<span id="status"></span>
{sync_badge}<button id="theme" type="button" aria-label="Toggle light or dark">&#9790;\
</button>
<div id="speed">
<button id="wpmdown" type="button" aria-label="Slower">&minus;</button>
<button id="wpmup" type="button" aria-label="Faster">+</button>
</div>
</div>
<video id="awake" muted loop playsinline preload="auto"
 src="data:video/mp4;base64,{KEEP_AWAKE_MP4_BASE64}"></video>
{sync_js}<script>
{_asset("reader.js")}
</script>
</body>
</html>
"""
