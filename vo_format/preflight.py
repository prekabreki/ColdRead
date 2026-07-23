"""Claude API preflight analysis and diagnostic review."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import anthropic

log = logging.getLogger(__name__)

from .models import (
    Archetype,
    CharacterInfo,
    DiagnosticEntry,
    DiagnosticReport,
    FormattedBlock,
    MetadataBlock,
    PreflightResult,
    PronunciationFlag,
    Section,
    SourceType,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PreflightError(Exception):
    """Base exception for preflight failures."""


class APIConnectionError(PreflightError):
    """Cannot reach the Anthropic API."""


class APIResponseError(PreflightError):
    """API returned an error status."""


class JSONParseError(PreflightError):
    """Could not parse API response as valid JSON."""


class ValidationError(PreflightError):
    """API response JSON does not match expected schema."""


# ---------------------------------------------------------------------------
# Preflight system prompt
# ---------------------------------------------------------------------------

PREFLIGHT_SYSTEM_PROMPT = """\
You are a voice-over script structural analyst. Analyze the provided script and \
return ONLY a valid JSON object. Do not include any text outside the JSON. Do not \
wrap in markdown code fences.

Analyze the script for the following:

1. ARCHETYPE: Classify the script as exactly one of:
   - "document_archive": Scripts organized around in-game documents, lore entries, \
or bureaucratic records. Usually has a narrator reading/presenting documents.
   - "multi_voice_drama": Scripts with multiple named characters having dialogue \
exchanges. Characters are clearly attributed with NAME: format.
   - "single_narrator": Scripts with one primary narrator who may quote other sources \
or read in-game text. The narrator is the dominant voice.
   - "continuous_prose": Scripts that read as continuous narrative without character \
attribution or dialogue formatting. No speaker labels. Pure storytelling flow.
   - "mixed_media": Scripts containing multiple source types like broadcasts, emails, \
transmissions, memos. Each block has a distinct media type label.

2. CHARACTERS: Identify all named speakers/characters. Look for patterns:
   - "**NAME:**" (markdown bold with colon)
   - "NAME:" (plain text with colon, all caps or title case)
   - Named characters introduced in voice cast tables
   For each character, count their approximate number of dialogue lines and suggest \
a color from this palette: #2563EB (Blue), #DC2626 (Red), #16A34A (Green), \
#9333EA (Purple), #EA580C (Orange), #0891B2 (Cyan), #CA8A04 (Amber), #DB2777 (Pink).
   Assign blue to the character with the most lines, then red, etc.
   The narrator (if any) should NOT receive a color.

3. SECTIONS: Identify structural divisions (acts, chapters, parts, major headings). \
Record the title and approximate start/end line numbers (1-based).

4. STAGE DIRECTIONS: Detect acting/performance cues:
   - *(parenthetical italic text)* like *(pause)*, *(voice hardens)*
   - *[bracketed italic text]* like *[Beat.]*, *[He laughs]*

5. SOUND CUES: Detect audio/production cues:
   - **[ALL CAPS BRACKETED BOLD]** like **[TAPE CLICK]**
   - *[Sound/tape descriptions]* like *[Tape noise. Static.]*

6. SOURCE TYPES: For mixed_media and document_archive, identify source types \
(broadcast, email, transmission, document reference) with labels or prefixes.

7. METADATA BLOCKS: Identify sections NOT part of the performable script. These include:
   - YouTube titles, video descriptions, channel intros/outros
   - Runtime estimates, word counts
   - Cassette labels, production notes, revision notes
   - Voice cast tables (the table itself, not the character names)
   - Series concept descriptions, episode previews
   - Editorial notes, lore accuracy checklists
   - Image references (markdown image syntax)
   Record start_line and end_line (1-based, inclusive).

8. PRONUNCIATION FLAGS: Identify fantasy names, foreign words, unusual proper nouns \
that a voice actor might stumble on.

9. WARNINGS: Flag ambiguities:
   - Unclear speaker attribution
   - Duplicate/repeated content blocks
   - Inconsistent formatting
   - Very long unbroken passages (>500 words without a break)

Return this exact JSON structure:
{
  "archetype": "<archetype_string>",
  "characters": [
    {"name": "<name>", "line_count": <int>, "suggested_color": "<hex>"}
  ],
  "has_narrator": <bool>,
  "source_types": [
    {"type": "<type>", "label": "<label_or_null>", "prefix": "<prefix_or_null>", "count": <int>}
  ],
  "sections": [
    {"title": "<title>", "start_line": <int>, "end_line": <int>}
  ],
  "detected_stage_directions": <bool>,
  "detected_sound_cues": <bool>,
  "metadata_blocks": [
    {"type": "<type>", "start_line": <int>, "end_line": <int>, "text": "<optional_summary>"}
  ],
  "pronunciation_flags": [
    {"word": "<word>", "line": <int>}
  ],
  "suggested_toggles": {
    "color_characters": <bool>,
    "narrator_style": "<normal|italic|bold>",
    "section_breaks": <bool>,
    "stage_directions": <bool>,
    "sound_cues": <bool>,
    "source_labels": <bool>,
    "quoted_text_style": "<indent|italic|indent+italic|none>",
    "strip_metadata": <bool>,
    "title_page": <bool>,
    "character_legend": <bool>,
    "font_size": <int>,
    "line_spacing": <float>,
    "margins": "<normal|wide|extra|narrow>"
  },
  "warnings": ["<warning_string>"]
}"""

# ---------------------------------------------------------------------------
# Pronunciation guide prompt
# ---------------------------------------------------------------------------

PRONUNCIATION_SYSTEM_PROMPT = """\
You are a pronunciation guide generator for voice actors. Given a list of \
fantasy/unusual words from a script, provide phonetic spellings that a voice \
actor can read at a glance during a cold read.

Return ONLY a valid JSON object mapping each word to its phonetic spelling:
{
  "Quel'Thalas": "KWEL-thah-lahs",
  "Byrgenwerth": "BUR-gen-werth",
  "Neuropozyne": "NOOR-oh-poh-zeen"
}

Rules:
- Use capital letters for stressed syllables
- Use simple phonetic notation (no IPA)
- Separate syllables with hyphens
- If unsure, give your best guess based on linguistic origin
- Keep it concise — voice actors read these at speed"""


# ---------------------------------------------------------------------------
# Diagnostic review prompt
# ---------------------------------------------------------------------------

DIAGNOSTIC_SYSTEM_PROMPT = """\
You are a voice-over script formatting quality reviewer. You will receive:
1. The original script text
2. The preflight analysis JSON that was used
3. A list of how each line was classified by the formatter

Your job is to identify formatting issues. Return ONLY a valid JSON object with this structure:
{
  "misclassified_lines": [
    {"line_number": <int>, "original_text": "<text>", "assigned_type": "<type>", \
"issue": "<what's wrong>", "suggestion": "<what it should be>"}
  ],
  "missed_characters": ["<character names the formatter didn't detect>"],
  "missed_stage_directions": [<line numbers>],
  "missed_sound_cues": [<line numbers>],
  "unstripped_metadata": [<line numbers that look like metadata but weren't stripped>],
  "unhandled_patterns": ["<formatting patterns the current rules don't handle>"],
  "summary": "<brief overall assessment>"
}"""


# ---------------------------------------------------------------------------
# API interaction
# ---------------------------------------------------------------------------


def _get_api_key(cli_key: str | None = None) -> str:
    """Resolve the Anthropic API key."""
    if cli_key:
        return cli_key
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        return env_key
    raise PreflightError(
        "No API key found. Set ANTHROPIC_API_KEY environment variable "
        "or pass --api-key on the command line."
    )


def _extract_json(text: str) -> dict[str, Any]:
    """Extract and parse JSON from API response text.

    Tries direct parsing first, then looks for JSON within markdown fences.
    Raises JSONParseError if the result is not a JSON object (dict).
    """
    text = text.strip()

    def _parse(raw: str) -> dict[str, Any]:
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise JSONParseError(
                f"Expected JSON object (dict), got {type(result).__name__}"
            )
        return result

    # Try direct parse
    try:
        return _parse(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code fences
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return _parse(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding JSON object boundaries
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        try:
            return _parse(text[brace_start : brace_end + 1])
        except json.JSONDecodeError:
            pass

    raise JSONParseError(f"Could not parse API response as JSON. Response: {text[:500]}")


def _validate_and_build(data: dict[str, Any]) -> PreflightResult:
    """Validate the parsed JSON and build a PreflightResult."""
    # Validate archetype
    archetype_str = data.get("archetype", "")
    try:
        archetype = Archetype(archetype_str)
    except ValueError:
        raise ValidationError(
            f"Unknown archetype '{archetype_str}'. "
            f"Expected one of: {[a.value for a in Archetype]}"
        )

    # Build characters
    characters = []
    for c in data.get("characters", []):
        characters.append(
            CharacterInfo(
                name=str(c.get("name", "Unknown")),
                line_count=int(c.get("line_count", 0)),
                suggested_color=str(c.get("suggested_color", "")),
            )
        )

    # Build source types
    source_types = []
    for s in data.get("source_types", []):
        source_types.append(
            SourceType(
                type=str(s.get("type", "")),
                label=s.get("label"),
                prefix=s.get("prefix"),
                count=int(s.get("count", 0)),
            )
        )

    # Build sections
    sections = []
    for s in data.get("sections", []):
        sections.append(
            Section(
                title=str(s.get("title", "")),
                start_line=int(s.get("start_line", 0)),
                end_line=int(s.get("end_line", 0)),
            )
        )

    # Build metadata blocks
    metadata_blocks = []
    metadata_warnings: list[str] = []
    for m in data.get("metadata_blocks", []):
        start = int(m.get("start_line", 0))
        end_raw = m.get("end_line")
        if end_raw is None:
            metadata_warnings.append(
                f"Metadata block at line {start} ({m.get('type', '?')}) "
                f"is missing end_line; treating as a single line."
            )
            end = start
        else:
            end = int(end_raw)
            if end < start:
                metadata_warnings.append(
                    f"Metadata block at line {start} ({m.get('type', '?')}) "
                    f"has end_line ({end}) before start_line; clamping to start_line."
                )
                end = start
        metadata_blocks.append(
            MetadataBlock(
                type=str(m.get("type", "")),
                start_line=start,
                end_line=end,
                text=m.get("text"),
            )
        )

    # Build pronunciation flags
    pronunciation_flags = []
    for p in data.get("pronunciation_flags", []):
        pronunciation_flags.append(
            PronunciationFlag(
                word=str(p.get("word", "")),
                line=int(p.get("line", 0)),
            )
        )

    return PreflightResult(
        archetype=archetype,
        characters=characters,
        has_narrator=bool(data.get("has_narrator", False)),
        source_types=source_types,
        sections=sections,
        detected_stage_directions=bool(data.get("detected_stage_directions", False)),
        detected_sound_cues=bool(data.get("detected_sound_cues", False)),
        metadata_blocks=metadata_blocks,
        pronunciation_flags=pronunciation_flags,
        suggested_toggles=data.get("suggested_toggles", {}),
        warnings=list(data.get("warnings", [])) + metadata_warnings,
    )


def run_preflight(
    script_text: str,
    filename: str,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-5-20250929",
) -> PreflightResult:
    """Run Claude API preflight analysis on a script.

    Args:
        script_text: The normalized script text.
        filename: Original filename for context.
        api_key: Anthropic API key (falls back to env var).
        model: Model to use for preflight.

    Returns:
        PreflightResult with all detected structure.

    Raises:
        PreflightError: On any API or parsing failure.
    """
    key = _get_api_key(api_key)
    line_count = script_text.count("\n") + 1

    # Truncate very long scripts to stay within API token limits.
    # Budget: ~50K tokens of script content leaves headroom for the system
    # prompt and response within the 200K context window.
    # For structural analysis, beginning (40%) + middle (20%) + end (20%) is
    # more than enough to detect archetype, characters, cues, etc.
    BUDGET_CHARS = 200_000  # ~50K tokens
    analysis_text = script_text
    truncated = False
    if len(script_text) > BUDGET_CHARS:
        truncated = True
        head = int(BUDGET_CHARS * 0.50)   # first 50% of budget
        mid  = int(BUDGET_CHARS * 0.25)   # middle 25%
        tail = int(BUDGET_CHARS * 0.25)   # last 25%
        middle_start = (len(script_text) - mid) // 2
        analysis_text = (
            f"{script_text[:head]}\n\n"
            f"[... {len(script_text) - head - mid - tail:,} characters omitted ...]\n\n"
            f"{script_text[middle_start:middle_start + mid]}\n\n"
            f"[... resuming near end of script ...]\n\n"
            f"{script_text[-tail:]}"
        )

    truncation_note = ""
    if truncated:
        truncation_note = (
            f" The script is very long ({len(script_text):,} characters) and has been "
            f"sampled (beginning, middle, end) for analysis. Line numbers are approximate."
        )

    user_message = (
        f'Analyze the following voice-over script. The script is from a file '
        f'named "{filename}" and is {line_count} lines long.{truncation_note}\n\n'
        f"<script>\n{analysis_text}\n</script>"
    )

    try:
        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            temperature=0,
            system=PREFLIGHT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIConnectionError as e:
        raise APIConnectionError(f"Could not connect to Claude API: {e}") from e
    except anthropic.APIStatusError as e:
        raise APIResponseError(f"Claude API error: {e.message}") from e

    # Extract text content
    response_text = ""
    for block in response.content:
        if block.type == "text":
            response_text += block.text

    if not response_text.strip():
        raise JSONParseError("API returned empty response")

    data = _extract_json(response_text)
    return _validate_and_build(data)


# ---------------------------------------------------------------------------
# Diagnostic review
# ---------------------------------------------------------------------------


def run_diagnostic(
    script_text: str,
    preflight_result: PreflightResult,
    formatted_blocks: list[FormattedBlock],
    api_key: str | None = None,
    model: str = "claude-sonnet-4-5-20250929",
) -> DiagnosticReport:
    """Run a diagnostic review comparing formatter output to the original script.

    This is a second API call (opt-in via --diagnose) that identifies
    misclassified lines and formatting issues.
    """
    # Build block classification list
    classifications = []
    for block in formatted_blocks:
        if block.source_line is not None:
            classifications.append(
                {
                    "line": block.source_line,
                    "type": block.block_type.value,
                    "text_preview": block.text[:80] if block.text else "",
                }
            )

    # Serialize preflight for context
    preflight_dict = {
        "archetype": preflight_result.archetype.value,
        "characters": [
            {"name": c.name, "line_count": c.line_count}
            for c in preflight_result.characters
        ],
        "has_narrator": preflight_result.has_narrator,
        "sections": [
            {"title": s.title, "start_line": s.start_line, "end_line": s.end_line}
            for s in preflight_result.sections
        ],
        "metadata_blocks": [
            {"type": m.type, "start_line": m.start_line, "end_line": m.end_line}
            for m in preflight_result.metadata_blocks
        ],
        "warnings": preflight_result.warnings,
    }

    user_message = (
        "Review the following formatter output for quality issues.\n\n"
        f"PREFLIGHT ANALYSIS:\n{json.dumps(preflight_dict, indent=2)}\n\n"
        f"FORMATTER CLASSIFICATIONS:\n{json.dumps(classifications, indent=2)}\n\n"
        f"ORIGINAL SCRIPT:\n<script>\n{script_text}\n</script>"
    )

    try:
        key = _get_api_key(api_key)
        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            temperature=0,
            system=DIAGNOSTIC_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
        return DiagnosticReport(
            misclassified_lines=[],
            missed_characters=[],
            missed_stage_directions=[],
            missed_sound_cues=[],
            unstripped_metadata=[],
            unhandled_patterns=[],
            summary=f"Diagnostic API call failed: {e}",
        )

    response_text = ""
    for block in response.content:
        if block.type == "text":
            response_text += block.text

    try:
        data = _extract_json(response_text)
    except JSONParseError:
        return DiagnosticReport(
            misclassified_lines=[],
            missed_characters=[],
            missed_stage_directions=[],
            missed_sound_cues=[],
            unstripped_metadata=[],
            unhandled_patterns=[],
            summary=f"Could not parse diagnostic response: {response_text[:300]}",
        )

    # Build report
    misclassified = []
    for entry in data.get("misclassified_lines", []):
        misclassified.append(
            DiagnosticEntry(
                line_number=int(entry.get("line_number", 0)),
                original_text=str(entry.get("original_text", "")),
                assigned_type=str(entry.get("assigned_type", "")),
                issue=str(entry.get("issue", "")),
                suggestion=str(entry.get("suggestion", "")),
            )
        )

    return DiagnosticReport(
        misclassified_lines=misclassified,
        missed_characters=data.get("missed_characters", []),
        missed_stage_directions=data.get("missed_stage_directions", []),
        missed_sound_cues=data.get("missed_sound_cues", []),
        unstripped_metadata=data.get("unstripped_metadata", []),
        unhandled_patterns=data.get("unhandled_patterns", []),
        summary=data.get("summary", ""),
    )


# ---------------------------------------------------------------------------
# Pronunciation guide generation
# ---------------------------------------------------------------------------


def run_pronunciation(
    words: list[str],
    script_context: str,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-5-20250929",
) -> dict[str, str]:
    """Generate phonetic spellings for a list of words via Claude API.

    Args:
        words: List of words to generate phonetics for.
        script_context: Brief context string (e.g. "Warcraft fantasy setting").
        api_key: Anthropic API key (falls back to env var).
        model: Model to use.

    Returns:
        Dict mapping each word to its phonetic spelling.
        Returns empty dict on failure (non-fatal).
    """
    if not words:
        return {}

    key = _get_api_key(api_key)

    # Deduplicate while preserving order
    seen = set()
    unique_words = []
    for w in words:
        if w not in seen:
            seen.add(w)
            unique_words.append(w)

    word_list = ", ".join(unique_words)
    user_message = (
        f"Generate phonetic pronunciations for these words from a {script_context}:\n\n"
        f"{word_list}"
    )

    try:
        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            temperature=0,
            system=PRONUNCIATION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
        log.warning("Pronunciation guide API call failed: %s", e)
        return {}

    response_text = ""
    for block in response.content:
        if block.type == "text":
            response_text += block.text

    try:
        data = _extract_json(response_text)
    except JSONParseError as e:
        log.warning("Could not parse pronunciation guide response: %s", e)
        return {}

    # Validate: should be a flat dict of str -> str
    result = {}
    for word, phonetic in data.items():
        if isinstance(phonetic, str):
            result[str(word)] = phonetic

    return result
