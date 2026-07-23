"""CustomTkinter GUI for ColdRead."""

from __future__ import annotations

import dataclasses
import logging
import os
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog

log = logging.getLogger(__name__)

import customtkinter
import fitz  # PyMuPDF
from PIL import Image

from .colors import assign_colors
from .formatter import format_script
from .models import (
    Archetype,
    BlockType,
    FormattedBlock,
    FormatToggles,
    MarginPreset,
    NarratorStyle,
    PreflightResult,
    QuotedTextStyle,
)
from .parser import extract_text, normalize_text
from .pdf_writer import generate_pdf
from .cache import PreflightCache
from .backend import VALID_BACKENDS, resolve_backend, run_preflight, run_pronunciation
from .preflight import PreflightError
from .presets import BUILTIN_PRESETS, delete_preset, list_presets, load_preset, save_preset, toggles_to_dict
from .toggles import TOGGLE_DEFINITIONS, resolve_toggles

# Optional dependencies — graceful fallback
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _HAS_DND = True
except ImportError:
    _HAS_DND = False

try:
    from CTkToolTip import CTkToolTip
    _HAS_TOOLTIP = True
except ImportError:
    _HAS_TOOLTIP = False

customtkinter.set_appearance_mode("dark")
customtkinter.set_default_color_theme("blue")

# File type filters for dialogs
INPUT_FILETYPES = [
    ("All supported", "*.txt *.md *.pdf *.docx"),
    ("Text files", "*.txt"),
    ("Markdown", "*.md"),
    ("PDF", "*.pdf"),
    ("Word", "*.docx"),
]
OUTPUT_FILETYPES = [("PDF", "*.pdf")]

_SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}

# Toggles that only work with preflight data
_PREFLIGHT_ONLY_TOGGLES = {"color_characters", "character_legend", "pronunciation_guide"}

RENDER_DPI = 144
MAX_PREVIEW_WIDTH = 900   # cap page display width so pages stay readable on maximize
_LETTER_WIDTH_PT = 612    # US Letter width in points — used to express zoom as a %


def _find_api_key() -> str | None:
    """Return the Anthropic API key from the ANTHROPIC_API_KEY env var."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    return key if key else None


# Display labels for the backend selector. The internal value is what the
# `backend` dispatcher expects.
_BACKEND_LABELS: dict[str, str] = {
    "api": "API",
    "claude-code": "Claude Code",
}
_BACKEND_BY_LABEL = {v: k for k, v in _BACKEND_LABELS.items()}


# ---------------------------------------------------------------------------
# Dynamic base class — include DnD mixin only if available
# ---------------------------------------------------------------------------

if _HAS_DND:
    _AppBase = type("_AppBase", (customtkinter.CTk, TkinterDnD.DnDWrapper), {})
else:
    _AppBase = customtkinter.CTk


class VOFormatterApp(_AppBase):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.title("ColdRead")
        self.geometry("1400x960")
        self.minsize(1000, 700)

        # Initialize TkDnD if available
        self._dnd_available = False
        if _HAS_DND:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self._dnd_available = True
            except Exception as e:
                log.info("Drag-and-drop disabled: TkinterDnD init failed (%s)", e)

        # State
        self.script_path: str | None = None
        self.raw_text: str | None = None
        self.normalized_text: str | None = None
        self.file_type: str | None = None
        self.preflight_result: PreflightResult | None = None
        self.color_map: dict[str, str] = {}
        self.blocks = None
        self.toggle_widgets: dict[str, any] = {}
        self.toggle_vars: dict[str, any] = {}
        self.toggle_value_labels: dict[str, customtkinter.CTkLabel] = {}
        self._busy = False
        self._refresh_timer: str | None = None
        self._has_preflight_data = False
        self._tooltips: list = []
        self._suppress_refresh = False

        # Preflight cache
        self._preflight_cache = PreflightCache()

        # Batch processing state
        self._batch_mode = False
        self._batch_folder: str | None = None
        self._batch_files: list[str] = []

        # Preview state
        self._page_images: list[customtkinter.CTkImage | None] = []
        self._total_pages = 0
        self._preview_tmp_path: str | None = None  # last preview PDF to clean up
        self._fixed_display_width: int | None = None  # None = auto fit-width
        self._raw_page_cache: dict[tuple, Image.Image] = {}  # (path, idx, dpi) -> PIL
        self._render_token: object | None = None  # invalidated on each new render

        # --- Layout: horizontal paned window ---
        self._paned = tk.PanedWindow(
            self, orient=tk.HORIZONTAL, sashwidth=6,
            bg="#2b2b2b", sashrelief=tk.FLAT,
        )
        self._paned.pack(fill="both", expand=True)

        # Left pane: controls
        self._left_container = customtkinter.CTkFrame(self._paned)
        self._paned.add(self._left_container, minsize=450, stretch="never")

        self.main_frame = customtkinter.CTkScrollableFrame(self._left_container)
        self.main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        self.main_frame.columnconfigure(0, weight=1)

        # Right pane: preview
        self._right_container = customtkinter.CTkFrame(self._paned)
        self._paned.add(self._right_container, minsize=400, stretch="always")

        # Build sections
        row = 0
        row = self._build_input_section(row)
        row = self._build_preflight_section(row)
        row = self._build_toggles_section(row)
        row = self._build_intro_outro_section(row)
        row = self._build_output_section(row)
        row = self._build_action_section(row)
        row = self._build_log_section(row)

        self._build_preview_panel()
        self._setup_drag_and_drop()

    # ------------------------------------------------------------------
    # Section builders — left pane
    # ------------------------------------------------------------------

    def _build_input_section(self, row: int) -> int:
        label = customtkinter.CTkLabel(
            self.main_frame, text="INPUT",
            font=customtkinter.CTkFont(size=13, weight="bold"),
        )
        label.grid(row=row, column=0, sticky="w", pady=(0, 4))
        row += 1

        # Mode switch: Single File / Batch Folder
        mode_frame = customtkinter.CTkFrame(self.main_frame, fg_color="transparent")
        mode_frame.grid(row=row, column=0, sticky="w", pady=(0, 6))
        self._mode_var = customtkinter.StringVar(value="Single File")
        self._mode_seg = customtkinter.CTkSegmentedButton(
            mode_frame, values=["Single File", "Batch Folder"],
            variable=self._mode_var, command=self._on_mode_changed,
        )
        self._mode_seg.pack(side="left")
        row += 1

        frame = customtkinter.CTkFrame(self.main_frame, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        frame.columnconfigure(0, weight=1)

        placeholder = "Select or drop a script file..." if self._dnd_available else "Select a script file..."
        self.input_entry = customtkinter.CTkEntry(frame, placeholder_text=placeholder)
        self.input_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.input_entry.configure(state="disabled")

        self.browse_btn = customtkinter.CTkButton(
            frame, text="Browse", width=80, command=self._browse_input,
        )
        self.browse_btn.grid(row=0, column=1)

        row += 1
        return row

    def _build_preflight_section(self, row: int) -> int:
        label = customtkinter.CTkLabel(
            self.main_frame, text="PREFLIGHT",
            font=customtkinter.CTkFont(size=13, weight="bold"),
        )
        label.grid(row=row, column=0, sticky="w", pady=(8, 4))
        row += 1

        btn_frame = customtkinter.CTkFrame(self.main_frame, fg_color="transparent")
        btn_frame.grid(row=row, column=0, sticky="ew", pady=(0, 4))

        self.analyze_btn = customtkinter.CTkButton(
            btn_frame, text="Analyze Script", width=140, command=self._run_preflight,
        )
        self.analyze_btn.pack(side="left")
        self.analyze_btn.configure(state="disabled")

        self.skip_preflight_var = customtkinter.StringVar(value="off")
        self.skip_preflight_cb = customtkinter.CTkSwitch(
            btn_frame, text="Skip", variable=self.skip_preflight_var,
            onvalue="on", offvalue="off", width=60,
            command=self._on_skip_preflight_toggle,
        )
        self.skip_preflight_cb.pack(side="left", padx=(12, 0))

        # Backend selector — "API" uses ANTHROPIC_API_KEY, "Claude Code"
        # shells out to the local `claude` CLI (subscription auth).
        # Tooltip goes on the leading label because CTkSegmentedButton
        # doesn't implement .bind (see CTkSegmentedButton in toggles section).
        default_backend = resolve_backend(None)
        backend_label = customtkinter.CTkLabel(btn_frame, text="Backend:")
        backend_label.pack(side="left", padx=(12, 4))
        if _HAS_TOOLTIP:
            self._tooltips.append(CTkToolTip(
                backend_label,
                message=(
                    "Analysis backend.\n"
                    "API: direct Anthropic API call (needs ANTHROPIC_API_KEY, "
                    "uses credits).\n"
                    "Claude Code: shells out to local 'claude' CLI in --print "
                    "mode (uses your Claude.ai subscription, no API credit "
                    "required)."
                ),
                delay=0.5,
            ))

        self._backend_var = customtkinter.StringVar(
            value=_BACKEND_LABELS[default_backend]
        )
        self._backend_selector = customtkinter.CTkSegmentedButton(
            btn_frame,
            values=[_BACKEND_LABELS[b] for b in VALID_BACKENDS],
            variable=self._backend_var,
            width=180,
        )
        self._backend_selector.pack(side="left")

        self.preflight_status = customtkinter.CTkLabel(
            btn_frame, text="  No file loaded", text_color="gray",
        )
        self.preflight_status.pack(side="left", padx=(8, 0))

        self.preflight_progress = customtkinter.CTkProgressBar(
            btn_frame, width=120, mode="indeterminate",
        )
        self.preflight_progress.pack(side="right", padx=(8, 0))
        self.preflight_progress.pack_forget()

        row += 1

        self.preflight_box = customtkinter.CTkTextbox(
            self.main_frame, height=110,
            font=customtkinter.CTkFont(family="Courier New", size=12),
        )
        self.preflight_box.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        self.preflight_box.configure(state="disabled")
        row += 1

        return row

    def _build_toggles_section(self, row: int) -> int:
        outer = customtkinter.CTkFrame(self.main_frame)
        outer.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        outer.columnconfigure(0, weight=1)

        label = customtkinter.CTkLabel(
            outer, text="FORMAT TOGGLES",
            font=customtkinter.CTkFont(size=13, weight="bold"),
        )
        label.grid(row=0, column=0, sticky="w", padx=12, pady=(8, 4), columnspan=2)

        # Preset row
        preset_frame = customtkinter.CTkFrame(outer, fg_color="transparent")
        preset_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6), columnspan=2)

        preset_label = customtkinter.CTkLabel(preset_frame, text="Preset:")
        preset_label.pack(side="left", padx=(0, 8))

        self._preset_var = customtkinter.StringVar(value="(none)")
        self._preset_combo = customtkinter.CTkComboBox(
            preset_frame,
            values=self._get_preset_names(),
            variable=self._preset_var,
            width=200,
            command=self._on_preset_selected,
        )
        self._preset_combo.pack(side="left", padx=(0, 8))

        self._save_preset_btn = customtkinter.CTkButton(
            preset_frame, text="Save", width=60,
            command=self._save_preset,
        )
        self._save_preset_btn.pack(side="left", padx=(0, 4))

        self._delete_preset_btn = customtkinter.CTkButton(
            preset_frame, text="Delete", width=60,
            fg_color="#DC2626", hover_color="#B91C1C",
            command=self._delete_preset,
        )
        self._delete_preset_btn.pack(side="left")

        # Boolean toggles in 2-column grid
        bool_frame = customtkinter.CTkFrame(outer, fg_color="transparent")
        bool_frame.grid(row=2, column=0, sticky="ew", padx=12, columnspan=2)
        bool_frame.columnconfigure(0, weight=1)
        bool_frame.columnconfigure(1, weight=1)

        bool_row = 0
        bool_col = 0
        for defn in TOGGLE_DEFINITIONS:
            if defn["type"] is not bool:
                continue
            name = defn["name"]
            var = customtkinter.StringVar(value="on" if defn["default"] else "off")

            display_text = defn["display_name"]
            if name in _PREFLIGHT_ONLY_TOGGLES:
                display_text += " *"

            switch = customtkinter.CTkSwitch(
                bool_frame, text=display_text,
                variable=var, onvalue="on", offvalue="off",
                command=self._on_toggle_changed,
            )
            switch.grid(row=bool_row, column=bool_col, sticky="w", padx=(0, 20), pady=3)
            self.toggle_widgets[name] = switch
            self.toggle_vars[name] = var

            # Tooltip
            if _HAS_TOOLTIP:
                tip_text = defn["description"]
                if name in _PREFLIGHT_ONLY_TOGGLES:
                    tip_text += "\n(Requires preflight analysis)"
                tt = CTkToolTip(switch, message=tip_text, delay=0.3)
                self._tooltips.append(tt)

            bool_col += 1
            if bool_col > 1:
                bool_col = 0
                bool_row += 1

        # Preflight note
        note = customtkinter.CTkLabel(
            bool_frame, text="* Requires preflight — disabled without analysis",
            text_color="#FACC15", font=customtkinter.CTkFont(size=11),
        )
        note.grid(row=bool_row + 1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self._preflight_note = note

        # Choice toggles (segmented buttons)
        choice_frame = customtkinter.CTkFrame(outer, fg_color="transparent")
        choice_frame.grid(row=3, column=0, sticky="ew", padx=12, pady=(8, 0), columnspan=2)
        choice_frame.columnconfigure(1, weight=1)

        choice_row = 0
        for defn in TOGGLE_DEFINITIONS:
            if defn["type"] is bool or defn["type"] is float:
                continue
            if "choices" not in defn:
                continue

            clabel = customtkinter.CTkLabel(choice_frame, text=defn["display_name"] + ":")
            clabel.grid(row=choice_row, column=0, sticky="w", padx=(0, 8), pady=4)

            values = [str(c) for c in defn["choices"]]
            var = customtkinter.StringVar(value=str(defn["default"]))
            seg = customtkinter.CTkSegmentedButton(
                choice_frame, values=values, variable=var,
                command=lambda val: self._on_toggle_changed(),
            )
            seg.grid(row=choice_row, column=1, sticky="w", pady=4)
            self.toggle_widgets[defn["name"]] = seg
            self.toggle_vars[defn["name"]] = var

            # Tooltip on the label (CTkSegmentedButton doesn't support .bind)
            if _HAS_TOOLTIP:
                tt = CTkToolTip(clabel, message=defn["description"], delay=0.3)
                self._tooltips.append(tt)

            choice_row += 1

        # Line spacing slider
        for defn in TOGGLE_DEFINITIONS:
            if defn["type"] is not float:
                continue

            slider_frame = customtkinter.CTkFrame(outer, fg_color="transparent")
            slider_frame.grid(row=4, column=0, sticky="ew", padx=12, pady=(8, 10), columnspan=2)
            slider_frame.columnconfigure(1, weight=1)

            slabel = customtkinter.CTkLabel(slider_frame, text=defn["display_name"] + ":")
            slabel.grid(row=0, column=0, sticky="w", padx=(0, 8))

            val_label = customtkinter.CTkLabel(slider_frame, text=str(defn["default"]), width=40)
            val_label.grid(row=0, column=2, padx=(8, 0))
            self.toggle_value_labels[defn["name"]] = val_label

            var = customtkinter.DoubleVar(value=defn["default"])

            def _on_slider(value, lbl=val_label):
                snapped = round(value * 4) / 4
                lbl.configure(text=f"{snapped:.2f}")
                self._on_toggle_changed()

            slider = customtkinter.CTkSlider(
                slider_frame, from_=1.0, to=3.0, number_of_steps=8,
                variable=var, command=_on_slider,
            )
            slider.grid(row=0, column=1, sticky="ew")
            self.toggle_widgets[defn["name"]] = slider
            self.toggle_vars[defn["name"]] = var

            if _HAS_TOOLTIP:
                tt = CTkToolTip(slider, message=defn["description"], delay=0.3)
                self._tooltips.append(tt)

        row += 1
        return row

    def _build_intro_outro_section(self, row: int) -> int:
        label = customtkinter.CTkLabel(
            self.main_frame, text="INTRO / OUTRO",
            font=customtkinter.CTkFont(size=13, weight="bold"),
        )
        label.grid(row=row, column=0, sticky="w", pady=(0, 4))
        row += 1

        hint = customtkinter.CTkLabel(
            self.main_frame,
            text="Optional. Prepended/appended to the script. Blank lines separate paragraphs.",
            text_color="gray", font=customtkinter.CTkFont(size=11),
        )
        hint.grid(row=row, column=0, sticky="w", pady=(0, 4))
        row += 1

        intro_label = customtkinter.CTkLabel(self.main_frame, text="Intro:")
        intro_label.grid(row=row, column=0, sticky="w", pady=(0, 2))
        row += 1

        self.intro_box = customtkinter.CTkTextbox(
            self.main_frame, height=80,
            font=customtkinter.CTkFont(family="Courier New", size=12),
        )
        self.intro_box.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        row += 1

        outro_label = customtkinter.CTkLabel(self.main_frame, text="Outro:")
        outro_label.grid(row=row, column=0, sticky="w", pady=(0, 2))
        row += 1

        self.outro_box = customtkinter.CTkTextbox(
            self.main_frame, height=80,
            font=customtkinter.CTkFont(family="Courier New", size=12),
        )
        self.outro_box.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        row += 1

        return row

    @staticmethod
    def _text_to_paragraph_blocks(text: str, block_type: BlockType) -> list[FormattedBlock]:
        """Split text into paragraphs (separated by blank lines) and build blocks."""
        if not text or not text.strip():
            return []
        paragraphs: list[str] = []
        current: list[str] = []
        for line in text.splitlines():
            if line.strip():
                current.append(line.rstrip())
            elif current:
                paragraphs.append("\n".join(current))
                current = []
        if current:
            paragraphs.append("\n".join(current))

        blocks: list[FormattedBlock] = []
        for i, para in enumerate(paragraphs):
            blocks.append(FormattedBlock(block_type=block_type, text=para))
            if i < len(paragraphs) - 1:
                blocks.append(FormattedBlock(block_type=BlockType.BLANK_LINE, text=""))
        return blocks

    def _get_intro_outro_blocks(self) -> tuple[list[FormattedBlock], list[FormattedBlock]]:
        """Return (intro_blocks, outro_blocks) from the GUI textboxes."""
        intro_text = self.intro_box.get("1.0", "end") if hasattr(self, "intro_box") else ""
        outro_text = self.outro_box.get("1.0", "end") if hasattr(self, "outro_box") else ""
        intro_blocks = self._text_to_paragraph_blocks(intro_text, BlockType.INTRO)
        outro_blocks = self._text_to_paragraph_blocks(outro_text, BlockType.OUTRO)
        # Trailing spacer after intro / leading spacer before outro for breathing room.
        if intro_blocks:
            intro_blocks.append(FormattedBlock(block_type=BlockType.BLANK_LINE, text=""))
        if outro_blocks:
            outro_blocks.insert(0, FormattedBlock(block_type=BlockType.BLANK_LINE, text=""))
        return intro_blocks, outro_blocks

    @staticmethod
    def _wrap_with_intro_outro(
        blocks: list[FormattedBlock],
        intro_blocks: list[FormattedBlock],
        outro_blocks: list[FormattedBlock],
    ) -> list[FormattedBlock]:
        """Insert intro after title page / character legend frontmatter; append outro at end."""
        if not intro_blocks and not outro_blocks:
            return blocks

        # Scan past leading frontmatter (title page + character legend + their
        # separators) so the intro lands on the first content page rather than
        # ahead of the cover.
        frontmatter_types = {
            BlockType.TITLE_PAGE_TITLE,
            BlockType.TITLE_PAGE_INFO,
            BlockType.CHARACTER_LEGEND_HEADER,
            BlockType.CHARACTER_LEGEND_ENTRY,
            BlockType.SECTION_DIVIDER,
            BlockType.PAGE_BREAK,
            BlockType.BLANK_LINE,
        }
        insert_idx = 0
        last_frontmatter_idx = -1
        for i, b in enumerate(blocks):
            if b.block_type in frontmatter_types:
                if b.block_type in (
                    BlockType.TITLE_PAGE_TITLE,
                    BlockType.TITLE_PAGE_INFO,
                    BlockType.CHARACTER_LEGEND_HEADER,
                    BlockType.CHARACTER_LEGEND_ENTRY,
                ):
                    last_frontmatter_idx = i
                continue
            break
        # Insert just after the last real frontmatter block, consuming any
        # trailing separator blocks too.
        if last_frontmatter_idx >= 0:
            insert_idx = last_frontmatter_idx + 1
            while (
                insert_idx < len(blocks)
                and blocks[insert_idx].block_type in frontmatter_types
            ):
                insert_idx += 1

        return blocks[:insert_idx] + intro_blocks + blocks[insert_idx:] + outro_blocks

    def _build_output_section(self, row: int) -> int:
        label = customtkinter.CTkLabel(
            self.main_frame, text="OUTPUT",
            font=customtkinter.CTkFont(size=13, weight="bold"),
        )
        label.grid(row=row, column=0, sticky="w", pady=(0, 4))
        row += 1

        frame = customtkinter.CTkFrame(self.main_frame, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 4))
        frame.columnconfigure(0, weight=1)

        self.output_entry = customtkinter.CTkEntry(frame, placeholder_text="Output folder...")
        self.output_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.output_browse_btn = customtkinter.CTkButton(
            frame, text="Browse", width=80, command=self._browse_output,
        )
        self.output_browse_btn.grid(row=0, column=1)

        row += 1

        self._export_batch_var = customtkinter.StringVar(value="off")
        self._export_batch_switch = customtkinter.CTkSwitch(
            self.main_frame, text="Also export voice batch PDF  (_formatted_batched.pdf)",
            variable=self._export_batch_var, onvalue="on", offvalue="off",
        )
        self._export_batch_switch.grid(row=row, column=0, sticky="w", pady=(0, 8))
        row += 1

        # Page count estimates
        pc_frame = customtkinter.CTkFrame(self.main_frame, fg_color="transparent")
        pc_frame.grid(row=row, column=0, sticky="ew", pady=(0, 12))

        self._page_estimate_label = customtkinter.CTkLabel(
            pc_frame, text="Estimated pages: --",
            text_color="gray", font=customtkinter.CTkFont(size=12),
        )
        self._page_estimate_label.pack(side="left", padx=(0, 20))

        self._page_actual_label = customtkinter.CTkLabel(
            pc_frame, text="Formatted pages: --",
            text_color="gray", font=customtkinter.CTkFont(size=12),
        )
        self._page_actual_label.pack(side="left")

        row += 1
        return row

    def _build_action_section(self, row: int) -> int:
        frame = customtkinter.CTkFrame(self.main_frame, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 12))

        self.preview_btn = customtkinter.CTkButton(
            frame, text="Preview", width=120, command=self._run_preview,
            fg_color="#555555", hover_color="#666666",
        )
        self.preview_btn.pack(side="left", padx=(0, 12))
        self.preview_btn.configure(state="disabled")

        self.generate_btn = customtkinter.CTkButton(
            frame, text="Generate PDF", width=140, command=self._run_generate,
        )
        self.generate_btn.pack(side="left")
        self.generate_btn.configure(state="disabled")

        row += 1
        return row

    def _build_log_section(self, row: int) -> int:
        self.log_box = customtkinter.CTkTextbox(
            self.main_frame, height=100,
            font=customtkinter.CTkFont(family="Courier New", size=11),
        )
        self.log_box.grid(row=row, column=0, sticky="ew", pady=(0, 4))
        self.log_box.configure(state="disabled")
        row += 1
        return row

    # ------------------------------------------------------------------
    # Preview panel — right pane
    # ------------------------------------------------------------------

    def _build_preview_panel(self) -> None:
        """Build the embedded PDF preview panel in the right pane."""
        # Header bar with page count and mode toggle
        header = customtkinter.CTkFrame(self._right_container, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(8, 4))

        self._page_label = customtkinter.CTkLabel(header, text="No preview yet")
        self._page_label.pack(side="left")

        self._preview_mode_var = customtkinter.StringVar(value="Scroll")
        self._preview_mode_btn = customtkinter.CTkSegmentedButton(
            header,
            values=["Scroll", "Page"],
            variable=self._preview_mode_var,
            command=self._on_preview_mode_changed,
            width=120,
        )
        self._preview_mode_btn.pack(side="left", padx=(12, 0))

        # Zoom controls
        zoom_frame = customtkinter.CTkFrame(header, fg_color="transparent")
        zoom_frame.pack(side="left", padx=(16, 0))

        self._zoom_fit_btn = customtkinter.CTkButton(
            zoom_frame, text="Fit", width=44, height=26,
            command=self._on_zoom_fit,
            fg_color="#444444", hover_color="#555555",
        )
        self._zoom_fit_btn.pack(side="left")

        self._zoom_out_btn = customtkinter.CTkButton(
            zoom_frame, text="−", width=28, height=26,
            command=self._on_zoom_out,
            fg_color="#444444", hover_color="#555555",
        )
        self._zoom_out_btn.pack(side="left", padx=(4, 0))

        self._zoom_pct_label = customtkinter.CTkLabel(zoom_frame, text="Fit", width=52)
        self._zoom_pct_label.pack(side="left", padx=(2, 2))

        self._zoom_in_btn = customtkinter.CTkButton(
            zoom_frame, text="+", width=28, height=26,
            command=self._on_zoom_in,
            fg_color="#444444", hover_color="#555555",
        )
        self._zoom_in_btn.pack(side="left")

        self._preview_status = customtkinter.CTkLabel(
            header, text="", text_color="gray",
        )
        self._preview_status.pack(side="right")

        # --- Scroll mode: scrollable frame with all pages stacked ---
        self._preview_scroll = customtkinter.CTkScrollableFrame(self._right_container)
        self._preview_scroll.pack(fill="both", expand=True, padx=10, pady=(4, 4))

        self._preview_placeholder = customtkinter.CTkLabel(
            self._preview_scroll,
            text="No preview yet\n\nLoad a script and click Preview,\nor toggle any setting after previewing.",
            text_color="gray",
            font=customtkinter.CTkFont(size=14),
        )
        self._preview_placeholder.pack(expand=True, pady=40)

        # Track page label widgets inside the scroll frame
        self._preview_page_labels: list[customtkinter.CTkLabel] = []

        # --- Page mode: single page with nav buttons ---
        self._page_mode_frame = customtkinter.CTkFrame(self._right_container)
        # Not packed initially — only shown when Page mode is active

        page_nav = customtkinter.CTkFrame(self._page_mode_frame, fg_color="transparent")
        page_nav.pack(fill="x", padx=10, pady=(4, 4))

        self._prev_btn = customtkinter.CTkButton(
            page_nav, text="< Prev", width=70, command=self._prev_page,
        )
        self._prev_btn.pack(side="left")

        self._page_indicator = customtkinter.CTkLabel(page_nav, text="0 / 0")
        self._page_indicator.pack(side="left", expand=True)

        self._next_btn = customtkinter.CTkButton(
            page_nav, text="Next >", width=70, command=self._next_page,
        )
        self._next_btn.pack(side="right")

        self._single_page_label = customtkinter.CTkLabel(
            self._page_mode_frame, text="",
        )
        self._single_page_label.pack(fill="both", expand=True, pady=(0, 8))

        self._current_page: int = 0

        # Store the last-rendered PDF path for resize re-renders
        self._last_pdf_path: str | None = None
        self._resize_timer: str | None = None

        # Listen for right-pane resizes to refit pages
        self._right_container.bind("<Configure>", self._on_preview_resize)
        self._last_preview_width: int = 0

    # ------------------------------------------------------------------
    # Preview rendering (embedded, scrollable)
    # ------------------------------------------------------------------

    def _on_preview_resize(self, event) -> None:
        """Re-render pages when the right pane changes width (fit mode only)."""
        if self._total_pages == 0 or not self._last_pdf_path:
            return
        if self._fixed_display_width is not None:
            return  # user set a fixed zoom; window resize shouldn't re-render
        new_pane_w = event.width
        if abs(new_pane_w - self._last_preview_width) < 20:
            return
        self._last_preview_width = new_pane_w
        if self._resize_timer is not None:
            self.after_cancel(self._resize_timer)
        self._resize_timer = self.after(300, self._refit_preview)

    def _refit_preview(self) -> None:
        """Re-render the current PDF at the new panel width."""
        self._resize_timer = None
        if self._fixed_display_width is not None:
            return
        if self._last_pdf_path and os.path.isfile(self._last_pdf_path):
            self._load_pdf(self._last_pdf_path)

    def _on_preview_mode_changed(self, mode: str) -> None:
        """Switch between Scroll and Page preview modes."""
        if mode == "Scroll":
            self._page_mode_frame.pack_forget()
            self._preview_scroll.pack(fill="both", expand=True, padx=10, pady=(4, 4))
        else:
            self._preview_scroll.pack_forget()
            self._page_mode_frame.pack(fill="both", expand=True, padx=10, pady=(4, 4))
            # _page_images is [None]*n while rendering; _show_single_page handles None
            if self._total_pages > 0:
                self._current_page = 0
                self._show_single_page()

    def _prev_page(self) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            self._show_single_page()

    def _next_page(self) -> None:
        if self._current_page < self._total_pages - 1:
            self._current_page += 1
            self._show_single_page()

    def _show_single_page(self) -> None:
        """Display the current page in single-page mode."""
        if not self._total_pages:
            return
        idx = max(0, min(self._current_page, len(self._page_images) - 1))
        img = self._page_images[idx] if self._page_images else None
        if img is None:
            self._single_page_label.configure(text="Rendering…", image=None)
        else:
            self._single_page_label.configure(image=img, text="")
        self._page_indicator.configure(text=f"{idx + 1} / {self._total_pages}")
        self._prev_btn.configure(state="normal" if idx > 0 else "disabled")
        self._next_btn.configure(
            state="normal" if idx < self._total_pages - 1 else "disabled"
        )

    def _compute_display_width(self) -> int:
        """Return the page display width in pixels based on zoom mode."""
        if self._fixed_display_width is not None:
            return max(200, self._fixed_display_width)
        try:
            pane_w = self._right_container.winfo_width()
        except Exception:
            pane_w = 700
        return max(300, min(MAX_PREVIEW_WIDTH, pane_w - 50))

    def _on_zoom_fit(self) -> None:
        self._fixed_display_width = None
        self._zoom_pct_label.configure(text="Fit")
        if self._last_pdf_path and os.path.isfile(self._last_pdf_path):
            self._load_pdf(self._last_pdf_path)

    def _on_zoom_in(self) -> None:
        new_w = min(1800, int(self._compute_display_width() * 1.15))
        self._fixed_display_width = new_w
        self._zoom_pct_label.configure(text=f"{int(new_w / _LETTER_WIDTH_PT * 100)}%")
        if self._last_pdf_path and os.path.isfile(self._last_pdf_path):
            self._load_pdf(self._last_pdf_path)

    def _on_zoom_out(self) -> None:
        new_w = max(200, int(self._compute_display_width() * 0.87))
        self._fixed_display_width = new_w
        self._zoom_pct_label.configure(text=f"{int(new_w / _LETTER_WIDTH_PT * 100)}%")
        if self._last_pdf_path and os.path.isfile(self._last_pdf_path):
            self._load_pdf(self._last_pdf_path)

    def _load_pdf(self, pdf_path: str) -> None:
        """Set up preview placeholders immediately; render pages in background thread."""
        try:
            doc = fitz.open(pdf_path)
            total_pages = len(doc)
            doc.close()
        except Exception:
            return

        self._last_pdf_path = pdf_path
        self._total_pages = total_pages

        display_width = self._compute_display_width()
        try:
            self._last_preview_width = self._right_container.winfo_width()
        except Exception:
            self._last_preview_width = display_width + 50

        # Invalidate any in-progress render
        token = object()
        self._render_token = token

        # Reset page images (Nones filled in as each page renders)
        self._page_images = [None] * total_pages  # type: ignore[assignment]

        # Clear scroll frame
        if self._preview_placeholder is not None:
            self._preview_placeholder.destroy()
            self._preview_placeholder = None
        for lbl in self._preview_page_labels:
            lbl.destroy()
        self._preview_page_labels = []

        # Auto-switch to Page mode for large documents (50+ pages)
        if total_pages >= 50 and self._preview_mode_var.get() == "Scroll":
            self._preview_mode_var.set("Page")
            self._on_preview_mode_changed("Page")

        # Always create scroll placeholders (hidden when in Page mode but kept
        # so switching back to Scroll shows content without a full re-render).
        placeholder_h = max(200, int(display_width * 1.294))  # ~letter aspect ratio
        for _ in range(total_pages):
            lbl = customtkinter.CTkLabel(
                self._preview_scroll, text="…",
                width=display_width, height=placeholder_h,
                text_color="gray",
            )
            lbl.pack(pady=(0, 8))
            self._preview_page_labels.append(lbl)

        if self._preview_mode_var.get() == "Page":
            self._show_single_page()

        self._page_label.configure(text=f"{total_pages} pages")

        # Background render — uses raw PIL cache to avoid re-rasterising on zoom
        def _render_worker() -> None:
            try:
                doc = fitz.open(pdf_path)
                for i, page in enumerate(doc):
                    if self._render_token is not token:
                        break

                    cache_key = (pdf_path, i, RENDER_DPI)
                    raw_img = self._raw_page_cache.get(cache_key)
                    if raw_img is None:
                        mat = fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
                        pix = page.get_pixmap(matrix=mat)
                        raw_img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                        if self._render_token is token:
                            self._raw_page_cache[cache_key] = raw_img
                            # Keep cache bounded (~80 pages ≈ 450 MB raw PIL)
                            while len(self._raw_page_cache) > 80:
                                del self._raw_page_cache[next(iter(self._raw_page_cache))]

                    if self._render_token is not token:
                        break

                    scale = display_width / raw_img.width
                    dh = int(raw_img.height * scale)
                    scaled = raw_img.resize((display_width, dh), Image.LANCZOS)

                    self.after(0, lambda idx=i, im=scaled, dw=display_width, h=dh:
                               self._on_page_ready(token, idx, im, dw, h))
                doc.close()
            except Exception as e:
                log.error("PDF render worker: %s", e)

        threading.Thread(target=_render_worker, daemon=True).start()

    def _on_page_ready(
        self, token: object, idx: int, img: Image.Image, dw: int, dh: int
    ) -> None:
        """Called on the main thread when one page finishes rendering."""
        if self._render_token is not token:
            return

        ctk_img = customtkinter.CTkImage(light_image=img, dark_image=img, size=(dw, dh))

        if idx < len(self._page_images):
            self._page_images[idx] = ctk_img

        if idx < len(self._preview_page_labels):
            self._preview_page_labels[idx].configure(
                image=ctk_img, text="", width=dw, height=dh
            )

        if self._preview_mode_var.get() == "Page" and idx == self._current_page:
            self._show_single_page()

    def _update_preview(self, pdf_path: str) -> None:
        """Reload the preview with a new PDF."""
        self._load_pdf(pdf_path)
        self._preview_status.configure(text="Updated")
        self.after(2000, lambda: self._preview_status.configure(text=""))

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"> {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # ------------------------------------------------------------------
    # Drag-and-drop
    # ------------------------------------------------------------------

    def _setup_drag_and_drop(self) -> None:
        """Register drag-and-drop targets if DnD is available."""
        if not self._dnd_available:
            return
        for widget in [self.input_entry, self._left_container]:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_file_drop)

    def _on_file_drop(self, event) -> None:
        """Handle a file dropped onto the application."""
        raw = event.data.strip()
        if raw.startswith("{") and raw.endswith("}"):
            raw = raw[1:-1]
        path = raw.split("\n")[0].strip()
        if not path:
            return

        ext = os.path.splitext(path)[1].lower()
        if ext not in _SUPPORTED_EXTENSIONS:
            self._log(f"Unsupported file type: {ext}")
            return
        if not os.path.isfile(path):
            self._log(f"File not found: {path}")
            return

        self.script_path = path
        self.input_entry.configure(state="normal")
        self.input_entry.delete(0, "end")
        self.input_entry.insert(0, path)
        self.input_entry.configure(state="disabled")
        self._on_file_selected(path)

    # ------------------------------------------------------------------
    # File browsing
    # ------------------------------------------------------------------

    def _browse_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Script File",
            filetypes=INPUT_FILETYPES,
        )
        if not path:
            return
        self.script_path = path
        self.input_entry.configure(state="normal")
        self.input_entry.delete(0, "end")
        self.input_entry.insert(0, path)
        self.input_entry.configure(state="disabled")
        self._on_file_selected(path)

    def _on_file_selected(self, path: str) -> None:
        """Common logic after a file is selected (via browse or drag-and-drop)."""
        out_dir = os.path.dirname(os.path.abspath(path))
        self.output_entry.delete(0, "end")
        self.output_entry.insert(0, out_dir)

        skipping = self.skip_preflight_var.get() == "on"
        if not skipping:
            self.analyze_btn.configure(state="normal", text="Analyze Script")
        self.preview_btn.configure(state="normal")
        self.generate_btn.configure(state="normal")

        # Reset state
        self.preflight_result = None
        self.raw_text = None
        self.normalized_text = None
        self.blocks = None
        self.color_map = {}
        self._has_preflight_data = False

        if not skipping:
            self.preflight_status.configure(text="  Ready to analyze", text_color="gray")
        self.preflight_box.configure(state="normal")
        self.preflight_box.delete("1.0", "end")
        self.preflight_box.configure(state="disabled")

        self._update_preflight_dependent_toggles()

        # Reset page count labels
        self._page_estimate_label.configure(text="Estimated pages: --", text_color="gray")
        self._page_actual_label.configure(text="Formatted pages: --", text_color="gray")

        self._log(f"Loaded: {os.path.basename(path)}")

    def _browse_output(self) -> None:
        folder = filedialog.askdirectory(title="Select Output Folder")
        if not folder:
            return
        self.output_entry.delete(0, "end")
        self.output_entry.insert(0, folder)

    # ------------------------------------------------------------------
    # Read + normalize input
    # ------------------------------------------------------------------

    def _ensure_text_loaded(self) -> bool:
        """Load and normalize the script text if not already done."""
        if self.normalized_text is not None:
            return True
        if not self.script_path:
            self._log("ERROR: No file selected.")
            return False
        try:
            self.raw_text, self.file_type = extract_text(self.script_path)
            self.normalized_text = normalize_text(self.raw_text)
            line_count = self.normalized_text.count("\n") + 1
            self._log(f"Read {os.path.basename(self.script_path)} ({line_count} lines, .{self.file_type})")
            self._update_page_estimates()
            return True
        except (FileNotFoundError, ValueError, ImportError) as e:
            self._log(f"ERROR: {e}")
            return False

    # ------------------------------------------------------------------
    # Toggle collection + auto-refresh
    # ------------------------------------------------------------------

    def _collect_toggles(self) -> FormatToggles:
        """Read current widget values into a FormatToggles instance."""
        toggles = FormatToggles()
        for defn in TOGGLE_DEFINITIONS:
            name = defn["name"]
            var = self.toggle_vars.get(name)
            if var is None:
                continue

            if defn["type"] is bool:
                val = var.get() == "on"
                setattr(toggles, name, val)
            elif defn["type"] is float:
                raw = var.get()
                snapped = round(raw * 4) / 4
                setattr(toggles, name, snapped)
            elif defn["type"] is int and "choices" in defn:
                setattr(toggles, name, int(var.get()))
            elif defn["type"] is str and "choices" in defn:
                raw_val = var.get()
                if name == "narrator_style":
                    setattr(toggles, name, NarratorStyle(raw_val))
                elif name == "quoted_text_style":
                    setattr(toggles, name, QuotedTextStyle(raw_val))
                elif name == "margins":
                    setattr(toggles, name, MarginPreset(raw_val))
                else:
                    setattr(toggles, name, raw_val)

        return toggles

    def _update_toggle_widgets(self, toggles: FormatToggles) -> None:
        """Update widget values from a FormatToggles instance."""
        self._suppress_refresh = True
        try:
            for defn in TOGGLE_DEFINITIONS:
                name = defn["name"]
                var = self.toggle_vars.get(name)
                if var is None:
                    continue

                value = getattr(toggles, name)

                if defn["type"] is bool:
                    var.set("on" if value else "off")
                elif defn["type"] is float:
                    var.set(value)
                    lbl = self.toggle_value_labels.get(name)
                    if lbl:
                        lbl.configure(text=f"{value:.2f}")
                elif defn["type"] is int and "choices" in defn:
                    var.set(str(value))
                elif defn["type"] is str:
                    display = value.value if hasattr(value, "value") else str(value)
                    var.set(display)
        finally:
            self._suppress_refresh = False

    def _on_toggle_changed(self, *_args) -> None:
        """Called when any toggle widget changes. Debounced preview refresh."""
        if self._suppress_refresh:
            return
        self._update_page_estimates()  # Lightweight — runs immediately
        if self._refresh_timer is not None:
            self.after_cancel(self._refresh_timer)
        self._refresh_timer = self.after(600, self._auto_refresh_preview)

    def _auto_refresh_preview(self) -> None:
        """Refresh the embedded preview if it has content."""
        self._refresh_timer = None
        if self._total_pages == 0:
            return
        self._run_preview()

    # ------------------------------------------------------------------
    # Preflight-dependent toggle management
    # ------------------------------------------------------------------

    def _update_preflight_dependent_toggles(self) -> None:
        """Enable/disable toggles that require preflight data."""
        has_data = self._has_preflight_data
        for name in _PREFLIGHT_ONLY_TOGGLES:
            widget = self.toggle_widgets.get(name)
            if widget is None:
                continue
            if has_data:
                widget.configure(state="normal")
            else:
                var = self.toggle_vars.get(name)
                if var:
                    var.set("off")
                widget.configure(state="disabled")

        if has_data:
            self._preflight_note.configure(
                text="* Preflight active \u2014 all features available",
                text_color="#4ADE80",
            )
        else:
            self._preflight_note.configure(
                text="* Requires preflight \u2014 disabled without analysis",
                text_color="#FACC15",
            )

    # ------------------------------------------------------------------
    # Presets
    # ------------------------------------------------------------------

    def _get_preset_names(self) -> list[str]:
        return ["(none)"] + list_presets()

    def _on_preset_selected(self, name: str) -> None:
        if name == "(none)":
            return
        try:
            toggles = load_preset(name)
            self._update_toggle_widgets(toggles)
            self._log(f"Loaded preset: {name}")
        except Exception as e:
            self._log(f"Failed to load preset '{name}': {e}")

    def _save_preset(self) -> None:
        dialog = customtkinter.CTkInputDialog(
            text="Preset name:", title="Save Preset",
        )
        name = dialog.get_input()
        if not name or not name.strip():
            return
        name = name.strip()
        toggles = self._collect_toggles()
        try:
            save_preset(name, toggles)
            self._preset_combo.configure(values=self._get_preset_names())
            self._preset_var.set(name)
            self._log(f"Saved preset: {name}")
        except Exception as e:
            self._log(f"Failed to save preset: {e}")

    def _delete_preset(self) -> None:
        name = self._preset_var.get()
        if name == "(none)":
            return
        if name in BUILTIN_PRESETS:
            self._log(f"Cannot delete built-in preset: {name}")
            return
        try:
            delete_preset(name)
            self._preset_combo.configure(values=self._get_preset_names())
            self._preset_var.set("(none)")
            self._log(f"Deleted preset: {name}")
        except Exception as e:
            self._log(f"Failed to delete preset: {e}")

    # ------------------------------------------------------------------
    # Page count estimates
    # ------------------------------------------------------------------

    def _estimate_page_count(self) -> int | None:
        """Estimate page count from raw text using current toggle settings."""
        if not self.normalized_text:
            return None

        toggles = self._collect_toggles()

        # Usable page height in points (letter = 11 inches = 792pt)
        margin_map = {"normal": 1.0, "wide": 1.5, "extra": 2.0}
        margin_val = margin_map.get(toggles.margins.value, 1.5)
        usable_height = 792 - (2 * margin_val * 72)  # top + bottom margins in pt

        # Lines per page based on font size and spacing
        leading = toggles.font_size * toggles.line_spacing
        lines_per_page = max(1, int(usable_height / leading))

        # Count non-blank lines as content proxy
        lines = self.normalized_text.split("\n")
        content_lines = sum(1 for ln in lines if ln.strip())

        # Add ~20% overhead for spacing, headers, section breaks
        effective_lines = int(content_lines * 1.2)

        return max(1, (effective_lines + lines_per_page - 1) // lines_per_page)

    def _update_page_estimates(self) -> None:
        """Update the estimated page count label."""
        est = self._estimate_page_count()
        if est is not None:
            self._page_estimate_label.configure(
                text=f"Estimated pages: ~{est}",
                text_color="#E5E7EB",
            )
        else:
            self._page_estimate_label.configure(
                text="Estimated pages: --",
                text_color="gray",
            )

    # ------------------------------------------------------------------
    # Batch mode
    # ------------------------------------------------------------------

    def _on_mode_changed(self, mode: str) -> None:
        """Switch between Single File and Batch Folder modes."""
        self._batch_mode = (mode == "Batch Folder")
        if self._batch_mode:
            self.browse_btn.configure(command=self._browse_batch_folder)
            self.input_entry.configure(state="normal")
            self.input_entry.delete(0, "end")
            self.input_entry.configure(
                state="disabled",
                placeholder_text="Select a folder of scripts...",
            )
            self.output_browse_btn.configure(command=self._browse_output_folder)
            self.output_entry.delete(0, "end")
            self.output_entry.configure(placeholder_text="Output folder...")
            self.preview_btn.configure(state="disabled")
            self.generate_btn.configure(text="Generate All", state="disabled")
            # Reset single-file state
            self.script_path = None
            self.raw_text = None
            self.normalized_text = None
            self._batch_folder = None
            self._batch_files = []
        else:
            self.browse_btn.configure(command=self._browse_input)
            self.input_entry.configure(state="normal")
            self.input_entry.delete(0, "end")
            placeholder = "Select or drop a script file..." if self._dnd_available else "Select a script file..."
            self.input_entry.configure(state="disabled", placeholder_text=placeholder)
            self.output_browse_btn.configure(command=self._browse_output)
            self.output_entry.delete(0, "end")
            self.output_entry.configure(placeholder_text="Output folder...")
            self.preview_btn.configure(state="disabled")
            self.generate_btn.configure(text="Generate PDF", state="disabled")
            self._batch_folder = None
            self._batch_files = []

    def _browse_batch_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select Folder of Scripts")
        if not folder:
            return
        self._batch_folder = folder
        files = []
        for f in sorted(os.listdir(folder)):
            if os.path.splitext(f)[1].lower() in _SUPPORTED_EXTENSIONS:
                files.append(os.path.join(folder, f))
        self._batch_files = files

        self.input_entry.configure(state="normal")
        self.input_entry.delete(0, "end")
        self.input_entry.insert(0, f"{folder}  ({len(files)} files)")
        self.input_entry.configure(state="disabled")

        # Default output folder
        default_out = os.path.join(folder, "formatted")
        self.output_entry.delete(0, "end")
        self.output_entry.insert(0, default_out)

        self.generate_btn.configure(state="normal" if files else "disabled")
        self._log(f"Batch folder: {os.path.basename(folder)} ({len(files)} scripts)")

    def _browse_output_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select Output Folder")
        if not folder:
            return
        self.output_entry.delete(0, "end")
        self.output_entry.insert(0, folder)

    def _collect_toggles_as_dict(self) -> dict:
        """Read current widget values as a dict for resolve_toggles overrides."""
        return toggles_to_dict(self._collect_toggles())

    def _run_batch_generate(self) -> None:
        if self._busy or not self._batch_files:
            return

        output_folder = self.output_entry.get().strip()
        if not output_folder:
            self._log("ERROR: No output folder specified.")
            return

        gui_overrides = self._collect_toggles_as_dict()
        api_key = self._get_api_key()
        backend_choice = self._get_backend()
        skipping_preflight = self.skip_preflight_var.get() == "on"
        files = list(self._batch_files)
        total = len(files)
        intro_blocks, outro_blocks = self._get_intro_outro_blocks()

        self._busy = True
        self.generate_btn.configure(state="disabled")
        self._log(f"Batch: processing {total} files...")

        def _batch_worker():
            os.makedirs(output_folder, exist_ok=True)
            results = []

            for idx, file_path in enumerate(files):
                filename = os.path.basename(file_path)
                self.after(0, lambda i=idx, fn=filename: self._log(
                    f"  [{i+1}/{total}] {fn}"
                ))

                entry = {"file": filename, "status": "success", "error": None, "pages": 0}

                try:
                    raw_text, file_type = extract_text(file_path)
                    norm_text = normalize_text(raw_text)

                    # Preflight (with caching).
                    # 'api' needs ANTHROPIC_API_KEY; 'claude-code' uses
                    # subscription auth via the local CLI.
                    preflight = None
                    can_run_preflight = (
                        not skipping_preflight
                        and (backend_choice == "claude-code" or bool(api_key))
                    )
                    if can_run_preflight:
                        text_hash = PreflightCache.hash_text(norm_text)
                        cached = self._preflight_cache.get(text_hash)
                        if cached:
                            preflight = cached
                            arch = preflight.archetype.value
                            self.after(0, lambda a=arch: self._log(
                                f"    Preflight: cached ({a})"
                            ))
                        else:
                            self.after(0, lambda b=backend_choice: self._log(
                                f"    Preflight: analyzing ({b})..."
                            ))
                            try:
                                preflight = run_preflight(
                                    backend_choice, norm_text, filename, api_key=api_key,
                                )
                                self._preflight_cache.put(text_hash, preflight)
                                arch = preflight.archetype.value
                                chars = len(preflight.characters)
                                self.after(0, lambda a=arch, c=chars: self._log(
                                    f"    Preflight: {a}, {c} character(s)"
                                ))
                            except PreflightError as pe:
                                pe_msg = str(pe)
                                self.after(0, lambda m=pe_msg: self._log(f"    Preflight failed: {m}"))
                    elif skipping_preflight:
                        self.after(0, lambda: self._log(
                            "    Preflight: skipped (toggle off)"
                        ))
                    elif not api_key:
                        self.after(0, lambda: self._log(
                            "    Preflight: skipped (no API key — switch backend to 'Claude Code' to use subscription)"
                        ))

                    if preflight is None:
                        preflight = PreflightResult(
                            archetype=Archetype.MULTI_VOICE_DRAMA,
                            characters=[], has_narrator=True, source_types=[],
                            sections=[], detected_stage_directions=False,
                            detected_sound_cues=False, metadata_blocks=[],
                            pronunciation_flags=[], suggested_toggles={}, warnings=[],
                        )

                    file_toggles = resolve_toggles(
                        preflight.archetype,
                        preflight.suggested_toggles,
                        gui_overrides,
                    )

                    blocks = format_script(norm_text, preflight, file_toggles, filename)
                    blocks = VOFormatterApp._wrap_with_intro_outro(blocks, intro_blocks, outro_blocks)

                    base = os.path.splitext(filename)[0]
                    out_path = os.path.join(output_folder, f"{base}_formatted_batch.pdf")
                    generate_pdf(blocks, out_path, file_toggles)
                    self.after(0, lambda p=out_path: self._log(
                        f"    -> {os.path.basename(p)}"
                    ))

                    # Count pages
                    try:
                        doc = fitz.open(out_path)
                        entry["pages"] = len(doc)
                        doc.close()
                    except Exception as e:
                        log.warning("Could not read page count for %s: %s", out_path, e)

                except Exception as e:
                    entry["status"] = "failed"
                    entry["error"] = str(e)

                results.append(entry)

            self.after(0, lambda: self._on_batch_done(results))

        threading.Thread(target=_batch_worker, daemon=True).start()

    def _on_batch_done(self, results: list[dict]) -> None:
        self._busy = False
        self.generate_btn.configure(state="normal")

        success = sum(1 for r in results if r["status"] == "success")
        failed = sum(1 for r in results if r["status"] == "failed")
        total = len(results)
        total_pages = sum(r.get("pages", 0) for r in results)

        self._log(f"Batch complete: {success}/{total} succeeded, {failed} failed, {total_pages} total pages")

        for r in results:
            if r["status"] == "failed":
                self._log(f"  FAILED: {r['file']} — {r['error']}")

    # ------------------------------------------------------------------
    # API key
    # ------------------------------------------------------------------

    def _get_api_key(self) -> str | None:
        """Get the API key from the environment."""
        return _find_api_key()

    def _get_backend(self) -> str:
        """Resolve the currently-selected analysis backend."""
        label = self._backend_var.get() if hasattr(self, "_backend_var") else ""
        return _BACKEND_BY_LABEL.get(label) or resolve_backend(None)

    # ------------------------------------------------------------------
    # Preflight
    # ------------------------------------------------------------------

    def _run_preflight(self) -> None:
        if self._busy:
            return
        if not self._ensure_text_loaded():
            return

        # Determine if we should bypass cache (button says "Re-analyze")
        force = self.analyze_btn.cget("text") == "Re-analyze"

        # Check cache first (unless forced)
        if not force and self.normalized_text:
            text_hash = PreflightCache.hash_text(self.normalized_text)
            cached = self._preflight_cache.get(text_hash)
            if cached is not None:
                self._on_preflight_done(cached, from_cache=True)
                return

        api_key = self._get_api_key()
        backend_choice = self._get_backend()
        if backend_choice == "api" and not api_key:
            self._log("WARNING: No API key found.")
            self._log("  Set ANTHROPIC_API_KEY, or switch the backend to 'Claude Code' to use your subscription.")
            self._apply_defaults()
            return

        self._busy = True
        self.analyze_btn.configure(state="disabled")
        if len(self.normalized_text) > 200_000:
            self._log("Script is very long — sampling for analysis...")
        self._log(
            f"Preflight: analyzing via {_BACKEND_LABELS[backend_choice]} "
            f"(this usually takes 10–30s)..."
        )
        self.preflight_status.configure(
            text=f"  Analyzing ({_BACKEND_LABELS[backend_choice]})...",
            text_color="#4A9EFF",
        )
        self.preflight_progress.pack(side="right", padx=(8, 0))
        self.preflight_progress.start()

        # Snapshot state so a mid-analysis file change in the main thread
        # can't corrupt the API call or the cache key.
        text_snapshot = self.normalized_text
        filename_snapshot = os.path.basename(self.script_path)

        def _worker():
            try:
                result = run_preflight(
                    backend_choice,
                    text_snapshot,
                    filename_snapshot,
                    api_key=api_key,
                )
                self.after(0, lambda: self._on_preflight_done(result, source_text=text_snapshot))
            except PreflightError as e:
                msg = str(e)
                self.after(0, lambda: self._on_preflight_error(msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_preflight_done(
        self,
        result: PreflightResult,
        from_cache: bool = False,
        source_text: str | None = None,
    ) -> None:
        self._busy = False
        self.preflight_progress.stop()
        self.preflight_progress.pack_forget()
        self.analyze_btn.configure(state="normal", text="Re-analyze")
        self.preflight_result = result
        self.color_map = assign_colors(result.characters, result.has_narrator)
        self._has_preflight_data = True

        # Cache against the text the preflight was actually run on, not whatever
        # is currently loaded (which may have changed mid-analysis).
        if not from_cache:
            cache_text = source_text if source_text is not None else self.normalized_text
            if cache_text:
                text_hash = PreflightCache.hash_text(cache_text)
                self._preflight_cache.put(text_hash, result)

        toggles = resolve_toggles(result.archetype, result.suggested_toggles)
        self._update_toggle_widgets(toggles)
        self._update_preflight_dependent_toggles()

        if from_cache:
            self.preflight_status.configure(text="  Done (cached)", text_color="#60A5FA")
        else:
            self.preflight_status.configure(text="  Done", text_color="#4ADE80")
        self._populate_preflight_summary(result)
        cache_tag = " [cached]" if from_cache else ""
        self._log(
            f"Preflight{cache_tag}: {result.archetype.value.replace('_', ' ').title()}, "
            f"{len(result.characters)} characters, {len(result.sections)} sections"
        )

    def _on_preflight_error(self, error: str) -> None:
        self._busy = False
        self.preflight_progress.stop()
        self.preflight_progress.pack_forget()
        self.analyze_btn.configure(state="normal")
        self.preflight_status.configure(text="  Failed", text_color="#EF4444")
        self._log(f"Preflight failed: {error}")
        self._log("Using default settings.")
        self._apply_defaults()

    def _apply_defaults(self, archetype: Archetype | None = None) -> None:
        """Apply default preflight result when API is unavailable."""
        arch = archetype or Archetype.MULTI_VOICE_DRAMA
        self.preflight_result = PreflightResult(
            archetype=arch,
            characters=[],
            has_narrator=True,
            source_types=[],
            sections=[],
            detected_stage_directions=False,
            detected_sound_cues=False,
            metadata_blocks=[],
            pronunciation_flags=[],
            suggested_toggles={},
            warnings=[],
        )
        self.color_map = {}
        self._has_preflight_data = False
        toggles = resolve_toggles(self.preflight_result.archetype)
        self._update_toggle_widgets(toggles)
        self._update_preflight_dependent_toggles()

    def _on_skip_preflight_toggle(self) -> None:
        """Handle the Skip preflight switch."""
        skipping = self.skip_preflight_var.get() == "on"
        if skipping:
            self.analyze_btn.configure(state="disabled")
            self.preflight_status.configure(
                text="  Skipped (using defaults)", text_color="#FACC15",
            )
            self._apply_defaults()
            self._log("Preflight skipped. Using default toggles \u2014 adjust manually.")
        else:
            self._has_preflight_data = bool(
                self.preflight_result and self.preflight_result.characters
            )
            if self.script_path:
                self.analyze_btn.configure(state="normal")
            self.preflight_status.configure(text="  Ready to analyze", text_color="gray")
            self._update_preflight_dependent_toggles()

    def _populate_preflight_summary(self, result: PreflightResult) -> None:
        """Fill the preflight summary textbox."""
        lines = []
        arch_display = result.archetype.value.replace("_", " ").title()
        lines.append(f"Script type: {arch_display}")

        if result.characters:
            lines.append(f"Characters: {len(result.characters)}")
            sorted_chars = sorted(result.characters, key=lambda c: c.line_count, reverse=True)
            char_strs = [f"  {c.name} ({c.line_count} lines)" for c in sorted_chars[:8]]
            lines.extend(char_strs)
            if len(result.characters) > 8:
                lines.append(f"  ... and {len(result.characters) - 8} more")

        if result.sections:
            lines.append(f"Sections: {len(result.sections)}")

        if result.detected_stage_directions:
            lines.append("Stage directions detected")
        if result.detected_sound_cues:
            lines.append("Sound cues detected")

        if result.pronunciation_flags:
            words = [p.word for p in result.pronunciation_flags[:6]]
            lines.append(f"Pronunciation flags: {', '.join(words)}")

        if result.warnings:
            for w in result.warnings:
                lines.append(f"WARNING: {w}")

        self.preflight_box.configure(state="normal")
        self.preflight_box.delete("1.0", "end")
        self.preflight_box.insert("1.0", "\n".join(lines))
        self.preflight_box.configure(state="disabled")

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _run_preview(self) -> None:
        if self._busy:
            return
        if not self._ensure_text_loaded():
            return

        if self.preflight_result is None:
            self._apply_defaults()

        toggles = self._collect_toggles()
        filename = os.path.basename(self.script_path)

        # Snapshot to insulate the worker from main-thread mutations.
        text_snapshot = self.normalized_text
        preflight_snapshot = self.preflight_result
        api_key = self._get_api_key()
        backend_choice = self._get_backend()
        intro_blocks, outro_blocks = self._get_intro_outro_blocks()

        self._busy = True
        self.preview_btn.configure(state="disabled")

        def _worker():
            try:
                # Pronunciation guide (same logic as _run_generate)
                pronunciation_guide = {}
                can_pronounce = (
                    toggles.pronunciation_guide
                    and preflight_snapshot
                    and preflight_snapshot.pronunciation_flags
                    and (backend_choice == "claude-code" or bool(api_key))
                )
                if can_pronounce:
                    words = [p.word for p in preflight_snapshot.pronunciation_flags]
                    arch_ctx = preflight_snapshot.archetype.value.replace("_", " ")
                    pronunciation_guide = run_pronunciation(
                        backend_choice, words,
                        script_context=f"{arch_ctx} script", api_key=api_key,
                    )

                blocks = format_script(
                    text_snapshot,
                    preflight_snapshot,
                    toggles,
                    filename,
                    pronunciation_guide=pronunciation_guide or None,
                )
                blocks = VOFormatterApp._wrap_with_intro_outro(blocks, intro_blocks, outro_blocks)
                self.blocks = blocks

                tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
                tmp_path = tmp.name
                tmp.close()
                generate_pdf(blocks, tmp_path, toggles)

                self.after(0, lambda: self._on_preview_done(tmp_path, filename, len(blocks)))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: self._on_preview_error(msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_preview_done(self, pdf_path: str, filename: str, block_count: int) -> None:
        self._busy = False
        self.preview_btn.configure(state="normal")

        # Clean up the previous preview temp file (created with delete=False).
        prev = self._preview_tmp_path
        if prev and prev != pdf_path and os.path.isfile(prev):
            try:
                os.unlink(prev)
            except OSError as e:
                log.info("Could not remove old preview temp %s: %s", prev, e)
        self._preview_tmp_path = pdf_path

        if self._total_pages > 0:
            self._update_preview(pdf_path)
        else:
            self._load_pdf(pdf_path)
            self._log(f"Preview: {block_count} blocks rendered")

        # Update actual page count
        self._page_actual_label.configure(
            text=f"Formatted pages: {self._total_pages}",
            text_color="#4ADE80",
        )

    def _on_preview_error(self, error: str) -> None:
        self._busy = False
        self.preview_btn.configure(state="normal")
        self._log(f"ERROR during preview: {error}")

    # ------------------------------------------------------------------
    # PDF generation
    # ------------------------------------------------------------------

    def _run_generate(self) -> None:
        if self._batch_mode:
            return self._run_batch_generate()
        if self._busy:
            return
        if not self._ensure_text_loaded():
            return

        output_folder = self.output_entry.get().strip()
        if not output_folder:
            self._log("ERROR: No output folder specified.")
            return

        base = os.path.splitext(os.path.basename(self.script_path))[0]
        output_path = os.path.join(output_folder, f"{base}_formatted.pdf")
        export_batch = self._export_batch_var.get() == "on"
        batch_path = os.path.join(output_folder, f"{base}_formatted_batched.pdf") if export_batch else None

        if self.preflight_result is None:
            self._apply_defaults()

        toggles = self._collect_toggles()
        filename = os.path.basename(self.script_path)

        # Snapshot to insulate the worker from main-thread mutations.
        text_snapshot = self.normalized_text
        preflight_snapshot = self.preflight_result
        api_key = self._get_api_key()
        backend_choice = self._get_backend()
        intro_blocks, outro_blocks = self._get_intro_outro_blocks()

        self._busy = True
        self.generate_btn.configure(state="disabled")
        self._log("Generating PDF...")

        def _worker():
            try:
                pronunciation_guide = {}
                if (
                    toggles.pronunciation_guide
                    and preflight_snapshot
                    and preflight_snapshot.pronunciation_flags
                ):
                    if backend_choice == "claude-code" or api_key:
                        words = [p.word for p in preflight_snapshot.pronunciation_flags]
                        arch_ctx = preflight_snapshot.archetype.value.replace("_", " ")
                        pronunciation_guide = run_pronunciation(
                            backend_choice, words,
                            script_context=f"{arch_ctx} script", api_key=api_key,
                        )
                    else:
                        self.after(0, lambda: self._log("Pronunciation guide skipped (no API key)"))

                blocks = format_script(
                    text_snapshot,
                    preflight_snapshot,
                    toggles,
                    filename,
                    pronunciation_guide=pronunciation_guide or None,
                )
                blocks = VOFormatterApp._wrap_with_intro_outro(blocks, intro_blocks, outro_blocks)
                self.blocks = blocks

                os.makedirs(output_folder, exist_ok=True)
                result_path = generate_pdf(blocks, output_path, toggles)
                if batch_path:
                    batch_toggles = dataclasses.replace(toggles, voice_batch=True)
                    batch_blocks = format_script(
                        text_snapshot,
                        preflight_snapshot,
                        batch_toggles,
                        filename,
                        pronunciation_guide=pronunciation_guide or None,
                    )
                    batch_blocks = VOFormatterApp._wrap_with_intro_outro(
                        batch_blocks, intro_blocks, outro_blocks
                    )
                    generate_pdf(batch_blocks, batch_path, batch_toggles)
                    self.after(0, lambda p=batch_path: self._log(
                        f"Batch PDF saved: {os.path.basename(p)}"
                    ))
                self.after(0, lambda: self._on_generate_done(result_path, len(blocks)))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: self._on_generate_error(msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_generate_done(self, path: str, block_count: int) -> None:
        self._busy = False
        self.generate_btn.configure(state="normal")
        self._log(f"Formatted {block_count} blocks")
        self._log(f"PDF saved: {path}")

        # Update actual page count from generated PDF
        try:
            doc = fitz.open(path)
            page_count = len(doc)
            doc.close()
            self._page_actual_label.configure(
                text=f"Formatted pages: {page_count}",
                text_color="#4ADE80",
            )
        except Exception as e:
            log.warning("Could not read page count for %s: %s", path, e)

    def _on_generate_error(self, error: str) -> None:
        self._busy = False
        self.generate_btn.configure(state="normal")
        self._log(f"ERROR: {error}")
