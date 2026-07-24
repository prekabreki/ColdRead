# ColdRead — Full Repository Audit

Non-security code-quality audit (correctness, architecture, simplification,
abstraction, tests, build, docs). Generated from six parallel deep-dives.
Baseline: **39 tests pass** (`.venv/bin/python -m pytest tests/`), ~7,949 LOC.

## Executive summary

**Overall health: B–.** The pipeline is coherent, degrades gracefully on API
failure, and the trickiest isolated pieces (font fallback, subprocess hardening,
DoS bounds, cache atomicity) are genuinely well done. But the codebase carries
three systemic problems that dominate everything else:

1. **The core pipeline is copy-pasted across CLI and GUI** (5+ open-coded
   `format_script → wrap → generate_pdf` sequences, duplicated `PreflightResult`
   defaults) — no `run_pipeline` seam. Every change must be made in many places
   and they have **already drifted** (batch silently skips pronunciation).
2. **The two big files are duplication-bound god-objects** — `formatter.py`
   (1884) is ~5× near-identical archetype formatters; `gui.py` (1947) is a
   single class with **no teardown**, cross-thread cache mutation, and a preview
   that fires **paid API calls on every toggle nudge**.
3. **Critical paths are untested and CI never runs the tests** — `cold_read.py`,
   `pdf_writer.py`, `preflight` JSON parsing, `cli.py` all have zero tests; the
   only workflow builds/publishes to PyPI without a `pytest` step.

Plus two non-code exposures: **PyMuPDF is AGPL but the released bundles are
labeled MIT**, and several correctness bugs bypass the graceful-degradation
contract (non-dict JSON crash; `--diagnose` with an extensionless `-o` overwrites
the PDF).

**Top 5 to fix first:** CI runs tests (TBD-6) · non-dict JSON crash (API-1) ·
`--diagnose` overwrites PDF (CLI-H1) · extract `run_pipeline` seam
(CLI-H3/GUI-DUP1) · GUI teardown + kill orphaned subprocess (GUI-D1).

## Architecture overview

`extract_text → normalize_text → backend.run_preflight → resolve_toggles →
[pronunciation] → format_script → [cold_read] → generate_pdf → [diagnostic]`.
Claude analyzes (JSON only); Python formats deterministically; script text is
never sent back for rewriting. Two analysis backends (`backend.py` dispatcher →
direct-API `preflight.py` or subprocess `claude_code_backend.py`). Two render
targets today (ReportLab PDF, Rich terminal preview) each with their own
per-`BlockType` dispatcher — a third (HTML read-view, issue #4) would make three.

## Cross-cutting synthesis (dedup — filed as single canonical issues)

- **Pipeline duplication** — CLI-H3 + GUI-DUP1 + GUI-DUP3 + CLI-M1 all point at
  the same missing `run_pipeline`/`default_preflight` seam. Filed once.
- **Layout geometry duplication** — PIPE-1 (margins in cold_read vs pdf_writer)
  + GUI-C6 (page-estimate margins). Filed once.
- **Render-op layer for a new target** — CLI-JC1 + CLI-M2 (preview/pdf dual
  dispatch). This directly de-risks the read-view epic (**#4**); filed once and
  linked to #4.
- **cold_read root cause** (#3, already filed) is *not* re-filed; but distinct
  breaker defects (abbreviation over-break PIPE-10, orphan overflow PIPE-5,
  priority-vs-proximity PIPE-8, dead locals PIPE-9) are separate issues.

---

## Findings by area

### API / backends (`preflight.py`, `backend.py`, `claude_code_backend.py`, `cache.py`)

| ID | Sev | Location | Finding |
|---|---|---|---|
| API-1 | High | `preflight.py:225,438,257,628`; `claude_code_backend.py:416` | Non-dict JSON (top-level array/string/number) passes `json.loads`, then `.get`/`.items()` → `AttributeError` that escapes every `except PreflightError`. Add `isinstance(result, dict)` guard in `_extract_json`. |
| API-2 | High | `claude_code_backend.py:359-571` vs `preflight.py:357-633` | ~150 lines of message-building + result-parsing copy-pasted between the two backends (differ only in transport). Extract shared `_build_*_message`/`_parse_*`; bug fixes currently need doing twice. |
| API-3 | Med | `backend.py:58-113`; `claude_code_backend.py:56-66` | No real backend abstraction: 3 near-identical `if chosen==` dispatchers; `claude_code_backend` imports 6 *private* names from `preflight`; default model string duplicated 6×. Add a `Backend` Protocol + factory + single model constant. |
| API-4 | Med | `claude_code_backend.py:365,426,481`; `backend.py:66` | `force_api_key` threaded through + documented but never set by any real caller — dead parameter surface. Wire it or drop it. |
| API-5 | Med | `claude_code_backend.py:39-48` | Always-on unbounded debug log at `~/vo-format-claude-debug.log`; logs script-derived result snippets forever, no cap/opt-out. Gate on env var + truncate. |
| API-6 | Low | `preflight.py:459` | `run_diagnostic` (designed non-fatal) calls `_get_api_key` *before* the try → raises on missing key instead of returning its stub. |
| API-7 | Low | `preflight.py:417,501,605` | Direct-API calls have no explicit `timeout=` (SDK ~10min default); subprocess path has 1200s. Add explicit timeout. |
| API-8 | Low | `backend.py:37-49` (`cli.py:466`) | Bad `VO_FORMAT_BACKEND`/`--backend` raises `ValueError` outside the fallback guard → raw traceback. Catch + clean message. |

### Formatter (`formatter.py`, 1884 LOC)

| ID | Sev | Location | Finding |
|---|---|---|---|
| FMT-C1 | Med | `formatter.py:1542` | `_insert_breathing_marks` breaks after abbreviations (`Mr. ` → `Mr. [breath] `); comment falsely claims it's guarded. Add abbreviation lookbehind or drop the claim. |
| FMT-C2 | Med | `formatter.py:66` | `RE_PLAIN_CHARACTER` requires `:\s+` so a plain-caps name alone on its line loses all attribution; `RE_BOLD_CHARACTER` handles it. Change `\s+`→`\s*`. (Load-bearing invariant per CLAUDE.md.) |
| FMT-C3 | Low-Med | `formatter.py:1616` | Pronunciation-hint match is naive substring (`"cat"` fires on `"category"`). Use `\b` word boundaries. |
| FMT-C4 | Med | `formatter.py:768,1030` | Turning `source_labels` off silently reclassifies plain sound-cue/stage-direction lines (delegate formatters don't run plain-cue detection). |
| FMT-C5 | Low | `formatter.py:339,357,935,951…` | Sound cues / stage directions are **deleted** (not demoted to narration) when their toggle is off — silent data loss. Decide + document. |
| FMT-C6 | Low | `formatter.py:187` | `RE_NEWS_TICKER` label lambda is a no-op f-string discarding its capture; dead everywhere. |
| FMT-D1 | Low | `formatter.py:1633` | Unused `OrderedDict` import. |
| FMT-D2 | Low-Med | `formatter.py:1732-1739` | Dead if/else in `_batch_by_voice` — both arms append; `else` sets an already-`None` var. Collapse to one append. |
| FMT-D3 | Low | ~20 sites | `else None` on `font_size` unreachable (`int`, default 16). Disappears under FMT-J1. |
| FMT-A1 | High | `formatter.py:302,506,723,802,1067` | Section-header handling copy-pasted 5×. |
| FMT-A2 | High | sound/stage cue sites ×4 | Sound-cue + stage-direction blocks copy-pasted 4×. |
| FMT-A3 | High | `378-441` vs `1284-1341` | Character-name (bold+plain) handling duplicated drama↔mixed-media. |
| FMT-A4 | Med | prelude ×5 | Blank/image-skip/rule prelude copy-pasted 5×. |
| FMT-A5 | Low-Med | drama only | `match_pattern` populated only in one of five formatters → `--diagnose` quality depends on archetype. |
| FMT-A6 | Med | `formatter.py:119-144,180` | Hardcoded fixture-overfit source patterns (Lazarus, SARIF INDUSTRIES) baked into the engine; move to data/config. Also type label callables (`Callable[[re.Match],str]`, not `object`). |
| FMT-A7 | Low | `1573` vs `1606` | Adjacent post-processing helpers use inconsistent mutate-vs-return conventions. |

### Output / CLI (`pdf_writer.py`, `preview.py`, `cli.py`)

| ID | Sev | Location | Finding |
|---|---|---|---|
| CLI-H1 | High | `cli.py:592` | `--diagnose` derives `debug_path` via `output_path.replace(".pdf",…)`; with `-o report` (no ext) or `-o out.PDF`, debug JSON **overwrites the PDF**. Use `os.path.splitext` stem for both. |
| CLI-H2 | High | `cli.py:368-411` | `_display_diagnostic` guard tests 3 fields (`missed_stage_directions`, `missed_sound_cues`, `unstripped_metadata`) it never renders → empty report + no "no issues" line. |
| CLI-H3 | High | `cli.py:453-579` vs `gui.py` | Core pipeline + default-`PreflightResult` open-coded in 5 places, no `run_pipeline`. **Canonical pipeline-seam issue** (absorbs GUI-DUP1/DUP3, CLI-M1). |
| CLI-M1 | Med | `cli.py:470 vs 541,632` | Pronunciation + diagnostic API calls not wrapped in the graceful-fallback guard preflight uses → traceback after PDF already shipped. |
| CLI-M2 | Med | `preview.py:52-204` vs `pdf_writer.py:429` | Preview re-implements per-`BlockType` dispatch with hardcoded (and internally inconsistent) indentation, diverging from the PDF. Fold into render-op layer (CLI-JC1). |
| CLI-M3 | Med | `cli.py:435-641` | `main()` is a 145-line monolith mixing orchestration, I/O, and 36-line inline debug-JSON assembly. Extract `_build_debug_data` + pronunciation-auto helper. |
| CLI-L1 | Low | `pdf_writer.py:226` | `import re as _re` mid-module. |
| CLI-L2 | Low | `cli.py:479,577,580,583` | f-strings with no placeholders. |
| CLI-L3 | Low-Med | `pdf_writer.py:136` | `_register_fonts()` runs at import (`os.walk` of up to 7 font dirs) — even `--list-samples`/tests pay it. Make lazy. |
| CLI-L4 | Low | `cli.py:462` | `line_count` off-by-one on trailing newline. |
| CLI-L5 | Low-Med | `pdf_writer.py:602-613` | Chained `keep_with_next` dropped — only pairwise keep-together works; a 3-block run can split. |

### GUI (`gui.py`, 1947 LOC; `gui_main.py`)

| ID | Sev | Location | Finding |
|---|---|---|---|
| GUI-D1 | High | `gui_main.py:6`; `gui.py:104` | **No teardown handler at all.** Close leaks preview temp PDF, abandons in-flight workers, and orphans a `claude` subprocess (1200s timeout). Add `WM_DELETE_WINDOW` → `_on_close`. |
| GUI-C1 | High | `gui.py:1173,1768` | Preview auto-refresh fires **paid pronunciation API / 1200s subprocess on every toggle change**, uncached. Remove network from the preview path. |
| GUI-DUP1 | High | `gui.py:1495,1782,1891,1905` | 4 copies of build-and-export pipeline, already drifted (batch skips pronunciation). Merge → part of CLI-H3 seam. |
| GUI-D2 | Med | `gui.py:946,1427,1519,1608,1797…` | Workers call `self.after()` with no destroyed-interpreter guard → `TclError` on close-during-work. Add `_post()` helper gated on `_closing`. |
| GUI-D3 | Med | `gui.py:920-952` | `_render_worker` mutates `_raw_page_cache`/`_page_images` off-thread → dict-changed-size races under rapid zoom/resize. Lock or move to main thread. |
| GUI-D4 | Med | `gui.py:1600,1764,1873` | Long backend calls uncancellable; no abort while busy. Add Cancel + subprocess kill. |
| GUI-C2 | Med | `gui.py:1768 vs 1876` | Pronunciation gating diverges preview↔generate (one logs skip, one silent). |
| GUI-C3 | Med | `gui.py:1421-1500` | Batch mode silently ignores the pronunciation toggle. |
| GUI-C4 | Med | `gui.py:1792` | Preview temp files leak on `generate_pdf` failure and for the final preview. |
| GUI-DUP2 | Med | `gui.py:1111,1382` | `_collect_toggles` + `_collect_toggles_as_dict` read the same widgets twice (enum-coercion asymmetry). Use `toggles_to_dict`. |
| GUI-N4 | Med | `gui.py:1499,1854` | "batch" overloaded 3 ways; suffixes `_batch.pdf` vs `_batched.pdf` differ by one letter for unrelated features. Rename. |
| GUI-C5 | Low | `gui.py:1158` | Slider value label found by `width==40` magic scan. Store the ref at build time. |
| GUI-C6 | Low | `gui.py:1280` | `_estimate_page_count` re-hardcodes margins (→ layout-geometry issue PIPE-1). |
| GUI-DUP3 | Low | `gui.py:1481,1665` | Default `PreflightResult` literal duplicated (→ CLI-H3 seam). |
| GUI-DUP4 | Low | `gui.py:442,1125,1393` | `round(x*4)/4` snap in 3 places, redundant with `number_of_steps=8`. |
| GUI-N1 | Low | `gui.py:8` | Unused `import sys`. |
| GUI-N2 | Low | `gui.py:126` | `self.blocks` is write-only dead state. |
| GUI-N3 | Low | `gui.py:127` | `dict[str, any]` uses builtin `any`, not `typing.Any`. |
| GUI-N5 | Low | `gui.py:1542` | `_get_api_key` trivial wrapper over `_find_api_key`. |

### Pipeline core (`models.py`, `toggles.py`, `cold_read.py`, `parser.py`, `colors.py`)

| ID | Sev | Location | Finding |
|---|---|---|---|
| PIPE-1 | High | `cold_read.py:76` vs `pdf_writer.py:141` | Margin geometry + page width copy-pasted between the two files that must stay in lockstep (indents already centralized; margins missed). Centralize like `WRAPPABLE_INDENT_UNITS`. |
| PIPE-2 | Med | `parser.py:93-103` | `latin-1` never raises, so the `errors="replace"` branch is dead; non-UTF-8 files silently become mojibake. Drop dead branch + warn on latin-1 fallthrough. |
| PIPE-3 | Med | `colors.py:62-69` | `suggested_color` override can assign the same color to two speakers (no used-color set) → defeats speaker distinction + grayscale invariant. |
| PIPE-4 | Med | `colors.py:25,53` | `has_narrator` param is never used — misleading API. |
| PIPE-5 | Med | `cold_read.py:219-232` | `_fix_orphans` merges without a length re-check → can exceed `max_chars`, defeating `_SAFETY_FACTOR` (ReportLab re-wraps). |
| PIPE-6 | Med | `toggles.py:208-228` | `coerce_value` class comparisons are permanently `False` under PEP 563 (`type` is a string); scalars (`font_size="16"`) never coerced. Drive from a `{field:EnumClass}` map. |
| PIPE-7 | Med | `toggles.py:147` | `margins` choices omit `"narrow"` though renderer/CLI/width-math support it → unreachable from GUI/interactive editor. |
| PIPE-8 | Med | `cold_read.py:120-130` | Break search lets priority override proximity → lines far shorter than the "balanced" target contract. Score priority *and* distance. |
| PIPE-9 | Low | `cold_read.py:180-181` | Dead locals `min_target`/`max_target`. |
| PIPE-10 | Low | `cold_read.py:36` | Sentence-break regex false-positives on abbreviations (distinct from #3). |
| PIPE-11 | Low | `toggles.py:266-274` | `resolve_toggles` silently drops unknown/misspelled keys. |
| PIPE-12 | Low | `toggles.py:261-265` | `toggle_name_map` maps legacy names the live preflight never emits; only a test exercises them (false confidence). |
| PIPE-13 | Low | various | Hygiene cluster: per-call dict rebuilds (`toggles.py:210`, `colors.py:65`), `import math` inside fn (`cold_read.py:164`), two-step `WRAPPABLE_INDENT_UNITS` forward-decl (`models.py:46`), no `font_size`/`line_spacing` validation on the dataclass. |

### Tests / build / deps / docs

| ID | Sev | Location | Finding |
|---|---|---|---|
| TBD-6 | High | `.github/workflows/release.yml` | **CI never runs pytest** — only builds+publishes to PyPI on tag; no push/PR trigger. Add a `test` job; `pypi` `needs:` it. |
| TBD-10 | High (legal) | `README.md:107`; `pyproject.toml:11` | PyMuPDF is AGPL-3.0 but the project's own released bundles embed it and are labeled MIT. Disclose AGPL on releases (or swap to `pypdf`). **needs-human decision.** |
| TBD-1 | High | `cold_read.py` | Entire breath-group engine untested (pure, deterministic — easiest to test, most intricate). Add table-driven tests + an indent-sync guard test. |
| TBD-2 | High | `preflight.py:225,257` | `_extract_json`/`_validate_and_build` untested — the no-key-needed robustness core covering both backends. |
| TBD-3 | High | `formatter.py` | 4 membership-only smoke tests for the 1884-LOC engine; can't catch over-classification. Add exact block-sequence + negative tests. |
| TBD-4 | Med-High | `pdf_writer.py` | `generate_pdf` never rendered in a test; no test that every `BlockType` has a style. Add a valid-PDF smoke + style-coverage test. |
| TBD-5 | Med | `cli.py:644,48,435` | `_collect_cli_overrides.toggle_fields` is a hand-maintained dup of `TOGGLE_DEFINITIONS` with no agreement test; `main`/`build_parser` untested. |
| TBD-7 | Med | repo root | No linter/formatter/type-check config (deliberate per CLAUDE.md, but a gap for a public package). Add `ruff`. |
| TBD-8 | Med | `pyproject.toml:21-30` | All deps float (`>=`, no upper bound, no lockfile); `anthropic` drift would break `preflight.py` uncaught. Add bounds + lock CI. |
| TBD-9 | Low-Med | `ColdRead.spec:36,26` | `upx=True` → non-deterministic size + Windows AV false positives on an unsigned download. Set `upx=False`. |
| TBD-11 | High (doc) | `CLAUDE.md` | Architecture omits the entire backend layer (684 LOC, `backend.py` + `claude_code_backend.py`); says `preflight.py` is "all Claude API interaction". Correct it. |
| TBD-12 | Med | `CLAUDE.md` | "API key" section stale: two model-pin points now; subscription backend runs *without* a key. |
| TBD-13 | Low | `cli.py:164`; docs | `--margins narrow` valid but absent from preflight prompt + README/CLAUDE.md; CLAUDE.md omits `--backend`/`--list-samples`. |
| TBD-BRITTLE | Low | `tests/test_parser.py:10,67` | Disjunctive/tautological assertions; delete/tighten. |

---

## Code judo (highest-leverage restructurings)

1. **`run_pipeline` + `default_preflight` seam** (CLI-H3) — collapses the 5-way
   pipeline copy in CLI+GUI into one testable function; kills GUI-DUP1/DUP3,
   fixes the batch-pronunciation drift (GUI-C3), gives the documented invariant a
   real home. *Highest impact.*
2. **Target-agnostic render-op layer** (CLI-JC1 + CLI-M2) — split "what a block
   means" from "how a target emits it"; turns PDF/preview/**HTML read-view (#4)**
   into thin visitors instead of three parallel dispatchers. *Directly de-risks
   the read-view epic.*
3. **Formatter common-prelude + block-builders** (FMT-J1/J2) — one
   `_handle_common_line` + `_make_*_block` helpers erase ~350-450 lines of the
   5× archetype duplication; drop formatter count 5→3. Do *before* splitting the
   file.
4. **Centralize layout geometry** (PIPE-1) — one source for page size + margins +
   indents feeding both `pdf_writer` and `cold_read`.
5. **Data-driven `coerce_value`** (PIPE-6) — `{field:EnumClass}` map replaces the
   dead PEP-563 class-comparison ladder.
6. **GUI decomposition** — extract `PdfPreviewPane`, a pipeline controller,
   layout, and toggles into modules; the god-class shell keeps only wiring +
   `_on_close`.

## Prioritized remediation order (impact × effort)

1. CI runs tests (TBD-6, S) — everything else is unguarded without it.
2. Correctness quick wins: API-1 (S), CLI-H1 (S), CLI-H2 (S), FMT-C2 (S).
3. GUI safety: GUI-D1 + GUI-D2 (teardown/guard), GUI-C1 (kill preview API).
4. `run_pipeline` seam (CLI-H3, M) — unblocks GUI dedup + batch fix.
5. Tests for pure critical paths: TBD-1, TBD-2 (S–M).
6. Layout geometry (PIPE-1) + breaker defects (PIPE-5/8/10) — pair with issue #3.
7. Render-op layer (CLI-JC1) — pair with read-view epic #4.
8. Formatter + GUI decomposition (FMT-J1/J2, GUI split) — large, do after seams.
9. Docs (TBD-11/12/13), license disclosure (TBD-10, needs-human), remaining Low.

## Healthy areas

Font fallback (`pdf_writer.py:99-136`), subprocess hardening
(`claude_code_backend.py`), parser DoS bounds + `finally` close, `cache.py`
atomic write + per-entry error isolation, `PreflightError` taxonomy, argparse
`--x/--no-x` tri-state wiring, `WRAPPABLE_INDENT_UNITS` sharing (on the indent
axis), `str,Enum` models, `PALETTE` grayscale lightness ranking,
`--list-samples` via `importlib.resources`, graceful preflight degradation.
