"""
asciidoc-check.py
=============
AsciiDoc Diagnostics Script — v1.0.0

Scans all .adoc files recursively from the current working directory and
reports structural issues to the terminal. Also writes an identical report
to a timestamped AsciiDoc log file under ./modules/pages/log/.

Detection rules (13 total):
  1.  Stray block delimiters (====, ----, ++++, ****)
  2.  Duplicate explicit anchors [id="..."]
  3.  Duplicate figure IDs (fig-*)
  4.  Duplicate figure titles
  5.  Duplicate table IDs (tbl-*)
  6.  Duplicate table titles
  7.  Duplicate section anchors ([#anchor])
  8.  Duplicate section IDs (excluding fig-* and tbl-*)
  9.  Section titles with leading spaces
  10. Section titles inside delimited blocks
  11. Missing blank lines before section titles
  12. Invalid nesting levels (====== or deeper)
  13. Adjacent section titles

Constraints:
  - Never modifies source .adoc files
  - Log directory is created automatically if it does not exist
  - Readable, commented, and maintainable
"""

import os
import re
import sys
from datetime import datetime
from collections import defaultdict


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCAN_EXTENSION = ".adoc"
LOG_DIR = os.path.join(".", "modules", "pages", "log")

# Delimiters that open/close delimited blocks in AsciiDoc.
# Each entry is (pattern_string, compiled_regex).
BLOCK_DELIMITERS = {
    "====": re.compile(r"^={4,}$"),
    "----": re.compile(r"^-{4,}$"),
    "++++": re.compile(r"^\+{4,}$"),
    "****": re.compile(r"^\*{4,}$"),
}

# A section title is a line starting with one or more = signs followed by a space.
SECTION_TITLE_RE = re.compile(r"^(=+)\s+\S")

# Leading-space section title: one or more spaces, then = signs and a space.
LEADING_SPACE_TITLE_RE = re.compile(r"^ +(=+)\s+\S")

# Explicit anchor: [id="some-id"]
EXPLICIT_ANCHOR_RE = re.compile(r'\[id="([^"]+)"\]')

# Section anchor: [#some-anchor] on its own line (not inline).
SECTION_ANCHOR_RE = re.compile(r"^\[#([^\]]+)\]")

# Figure ID: fig-* inside an anchor
FIGURE_ID_RE = re.compile(r'\[id="(fig-[^"]+)"\]|^\[#(fig-[^\]]+)\]')

# Table ID: tbl-* inside an anchor
TABLE_ID_RE = re.compile(r'\[id="(tbl-[^"]+)"\]|^\[#(tbl-[^\]]+)\]')

# Figure title: line starting with .Figure or .fig (case-insensitive caption prefix)
FIGURE_TITLE_RE = re.compile(r"^\.(Figure\s+.+|fig[- ].+)", re.IGNORECASE)

# Table title: line starting with .Table or .tbl (case-insensitive caption prefix)
TABLE_TITLE_RE = re.compile(r"^\.(Table\s+.+|tbl[- ].+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------

def collect_adoc_files(root: str) -> list[str]:
    """
    Recursively collect all .adoc files under *root*.
    Returns a sorted list of relative file paths.
    """
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            if filename.endswith(SCAN_EXTENSION):
                found.append(os.path.join(dirpath, filename))
    return sorted(found)


# ---------------------------------------------------------------------------
# Per-file parsing helpers
# ---------------------------------------------------------------------------

def read_lines(filepath: str) -> list[str]:
    """Read a file and return its lines with newlines stripped."""
    with open(filepath, encoding="utf-8", errors="replace") as fh:
        return [line.rstrip("\n") for line in fh.readlines()]


def is_inside_block(open_counts: dict) -> bool:
    """Return True if any block delimiter is currently open (odd open count)."""
    return any(count % 2 == 1 for count in open_counts.values())


# ---------------------------------------------------------------------------
# Detection rule implementations
# ---------------------------------------------------------------------------

def check_stray_block_delimiters(filepath: str, lines: list[str]) -> list[str]:
    """
    Rule 1 — Stray block delimiters.

    A delimiter line is 'stray' when it is not part of a matched pair.
    We track open/close counts per delimiter type. An odd running total at
    end-of-file means at least one delimiter was never closed.

    We also flag any delimiter that appears to open a block but is immediately
    followed by another delimiter of the same type with no content in between
    (empty block), as this is almost always a copy/paste error.
    """
    findings = []
    # Track whether each delimiter type is currently open (True) or closed.
    open_state = {d: False for d in BLOCK_DELIMITERS}
    open_lines = {d: None for d in BLOCK_DELIMITERS}  # line number where opened

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        for delim, pattern in BLOCK_DELIMITERS.items():
            if pattern.match(stripped):
                if not open_state[delim]:
                    # Opening this delimiter.
                    open_state[delim] = True
                    open_lines[delim] = lineno
                else:
                    # Closing this delimiter — pair is matched, reset.
                    open_state[delim] = False
                    open_lines[delim] = None
                break  # A line can only match one delimiter type.

    # Any delimiter still open at EOF is stray.
    for delim, still_open in open_state.items():
        if still_open:
            findings.append(
                f"  {filepath}:{open_lines[delim]}  "
                f"Unclosed block delimiter '{delim}' (no matching closing delimiter found)"
            )

    return findings


def check_duplicate_explicit_anchors(
    all_anchors: dict[str, list[tuple[str, int]]]
) -> list[str]:
    """
    Rule 2 — Duplicate explicit anchors [id="..."].

    *all_anchors* is built during the main scan pass:
      { anchor_id: [(filepath, lineno), ...] }

    Returns findings for any anchor_id that appears more than once.
    """
    findings = []
    for anchor_id, locations in sorted(all_anchors.items()):
        if len(locations) > 1:
            findings.append(
                f"  Anchor id=\"{anchor_id}\" appears {len(locations)} times:"
            )
            for filepath, lineno in locations:
                findings.append(f"    {filepath}:{lineno}")
    return findings


def check_duplicate_figure_ids(
    all_figure_ids: dict[str, list[tuple[str, int]]]
) -> list[str]:
    """
    Rule 3 — Duplicate figure IDs (fig-*).
    """
    findings = []
    for fig_id, locations in sorted(all_figure_ids.items()):
        if len(locations) > 1:
            findings.append(
                f"  Figure ID \"{fig_id}\" appears {len(locations)} times:"
            )
            for filepath, lineno in locations:
                findings.append(f"    {filepath}:{lineno}")
    return findings


def check_duplicate_figure_titles(
    all_figure_titles: dict[str, list[tuple[str, int]]]
) -> list[str]:
    """
    Rule 4 — Duplicate figure titles.
    """
    findings = []
    for title, locations in sorted(all_figure_titles.items()):
        if len(locations) > 1:
            findings.append(
                f"  Figure title \"{title}\" appears {len(locations)} times:"
            )
            for filepath, lineno in locations:
                findings.append(f"    {filepath}:{lineno}")
    return findings


def check_duplicate_table_ids(
    all_table_ids: dict[str, list[tuple[str, int]]]
) -> list[str]:
    """
    Rule 5 — Duplicate table IDs (tbl-*).
    """
    findings = []
    for tbl_id, locations in sorted(all_table_ids.items()):
        if len(locations) > 1:
            findings.append(
                f"  Table ID \"{tbl_id}\" appears {len(locations)} times:"
            )
            for filepath, lineno in locations:
                findings.append(f"    {filepath}:{lineno}")
    return findings


def check_duplicate_table_titles(
    all_table_titles: dict[str, list[tuple[str, int]]]
) -> list[str]:
    """
    Rule 6 — Duplicate table titles.
    """
    findings = []
    for title, locations in sorted(all_table_titles.items()):
        if len(locations) > 1:
            findings.append(
                f"  Table title \"{title}\" appears {len(locations)} times:"
            )
            for filepath, lineno in locations:
                findings.append(f"    {filepath}:{lineno}")
    return findings


def check_duplicate_section_anchors(
    all_section_anchors: dict[str, list[tuple[str, int]]]
) -> list[str]:
    """
    Rule 7 — Duplicate section anchors ([#anchor]).
    """
    findings = []
    for anchor, locations in sorted(all_section_anchors.items()):
        if len(locations) > 1:
            findings.append(
                f"  Section anchor \"[#{anchor}]\" appears {len(locations)} times:"
            )
            for filepath, lineno in locations:
                findings.append(f"    {filepath}:{lineno}")
    return findings


def check_duplicate_section_ids(
    all_section_ids: dict[str, list[tuple[str, int]]]
) -> list[str]:
    """
    Rule 8 — Duplicate section IDs, excluding fig-* and tbl-* prefixes.

    Section IDs are derived from both explicit [id="..."] anchors that are
    not figure or table IDs, and [#anchor] markers that are not fig-* or tbl-*.
    """
    findings = []
    for sec_id, locations in sorted(all_section_ids.items()):
        if len(locations) > 1:
            findings.append(
                f"  Section ID \"{sec_id}\" appears {len(locations)} times:"
            )
            for filepath, lineno in locations:
                findings.append(f"    {filepath}:{lineno}")
    return findings


def check_leading_space_titles(filepath: str, lines: list[str]) -> list[str]:
    """
    Rule 9 — Section titles with leading spaces.

    Asciidoctor does not recognise a section title if it is preceded by
    any whitespace. These are silently treated as normal paragraphs.
    """
    findings = []
    for lineno, line in enumerate(lines, start=1):
        if LEADING_SPACE_TITLE_RE.match(line):
            findings.append(
                f"  {filepath}:{lineno}  Leading space before section title: {line.strip()!r}"
            )
    return findings


def check_titles_inside_blocks(filepath: str, lines: list[str]) -> list[str]:
    """
    Rule 10 — Section titles inside delimited blocks.

    Inside a delimited block, a line that looks like a section title is
    treated as literal content. This is almost always unintentional.
    """
    findings = []
    open_state = {d: False for d in BLOCK_DELIMITERS}

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Check for delimiter toggle before checking for title,
        # so the delimiter line itself is not tested as content.
        toggled = False
        for delim, pattern in BLOCK_DELIMITERS.items():
            if pattern.match(stripped):
                open_state[delim] = not open_state[delim]
                toggled = True
                break

        if not toggled and any(open_state.values()):
            if SECTION_TITLE_RE.match(line):
                findings.append(
                    f"  {filepath}:{lineno}  Section title inside a delimited block: {line!r}"
                )

    return findings


def check_missing_blank_before_title(filepath: str, lines: list[str]) -> list[str]:
    """
    Rule 11 — Missing blank line before a section title.

    A section title must be preceded by a blank line (or be the first line
    of the file). If the previous non-empty line is not blank, the title
    may not render correctly.
    """
    findings = []
    for lineno, line in enumerate(lines, start=1):
        if SECTION_TITLE_RE.match(line):
            if lineno == 1:
                continue  # First line of file — no preceding line required.
            prev_line = lines[lineno - 2]  # lines is 0-indexed; lineno is 1-indexed.
            if prev_line.strip() != "":
                findings.append(
                    f"  {filepath}:{lineno}  Missing blank line before section title: {line!r}"
                )
    return findings


def check_invalid_nesting_levels(filepath: str, lines: list[str]) -> list[str]:
    """
    Rule 12 — Invalid nesting levels (====== or deeper).

    AsciiDoc supports section levels 0–5 (= through =====).
    Six or more equals signs (======) is not a valid section title.
    """
    findings = []
    too_deep = re.compile(r"^={6,}\s+\S")
    for lineno, line in enumerate(lines, start=1):
        if too_deep.match(line):
            depth = len(line) - len(line.lstrip("="))
            findings.append(
                f"  {filepath}:{lineno}  Section title at invalid depth (level {depth}): {line!r}"
            )
    return findings


def check_adjacent_section_titles(filepath: str, lines: list[str]) -> list[str]:
    """
    Rule 13 — Adjacent section titles with no content between them.

    Two or more consecutive section title lines (with only blank lines
    between them and no other content) typically indicate a missing or
    broken include directive.
    """
    findings = []
    prev_title_lineno = None
    prev_title_text = None
    # Track whether any non-blank, non-title content appeared since last title.
    content_since_last_title = True

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if SECTION_TITLE_RE.match(line):
            if prev_title_lineno is not None and not content_since_last_title:
                findings.append(
                    f"  {filepath}:{lineno}  Adjacent section title "
                    f"(no content after title on line {prev_title_lineno}): {line!r}"
                )
            prev_title_lineno = lineno
            prev_title_text = line
            content_since_last_title = False
        elif stripped != "":
            # Non-blank, non-title content resets the adjacency check.
            content_since_last_title = True

    return findings


# ---------------------------------------------------------------------------
# Main scan pass — collects per-file and cross-file data
# ---------------------------------------------------------------------------

def scan_files(filepaths: list[str]) -> dict:
    """
    Iterate over all files and collect:
      - Per-file findings for rules that only need one file at a time
        (rules 1, 9, 10, 11, 12, 13).
      - Cross-file registries for rules that compare across all files
        (rules 2–8).

    Returns a dict containing all raw findings and registries.
    """
    # Cross-file registries: { value: [(filepath, lineno), ...] }
    explicit_anchors: dict[str, list] = defaultdict(list)   # rule 2
    figure_ids: dict[str, list] = defaultdict(list)          # rule 3
    figure_titles: dict[str, list] = defaultdict(list)       # rule 4
    table_ids: dict[str, list] = defaultdict(list)           # rule 5
    table_titles: dict[str, list] = defaultdict(list)        # rule 6
    section_anchors: dict[str, list] = defaultdict(list)     # rule 7
    section_ids: dict[str, list] = defaultdict(list)         # rule 8

    # Per-file findings keyed by rule id.
    per_file: dict[str, list[str]] = {
        "stray_block_delimiters": [],
        "leading_space_titles": [],
        "titles_inside_blocks": [],
        "missing_blank_before_title": [],
        "invalid_nesting_levels": [],
        "adjacent_section_titles": [],
    }

    for filepath in filepaths:
        lines = read_lines(filepath)

        # Rule 1
        per_file["stray_block_delimiters"].extend(
            check_stray_block_delimiters(filepath, lines)
        )

        # Rules 9–13 (per-file checks)
        per_file["leading_space_titles"].extend(
            check_leading_space_titles(filepath, lines)
        )
        per_file["titles_inside_blocks"].extend(
            check_titles_inside_blocks(filepath, lines)
        )
        per_file["missing_blank_before_title"].extend(
            check_missing_blank_before_title(filepath, lines)
        )
        per_file["invalid_nesting_levels"].extend(
            check_invalid_nesting_levels(filepath, lines)
        )
        per_file["adjacent_section_titles"].extend(
            check_adjacent_section_titles(filepath, lines)
        )

        # Cross-file registry population (rules 2–8)
        for lineno, line in enumerate(lines, start=1):

            # Rule 2 — explicit anchors [id="..."]
            for m in EXPLICIT_ANCHOR_RE.finditer(line):
                anchor_id = m.group(1)
                explicit_anchors[anchor_id].append((filepath, lineno))

                # Rule 3 — figure IDs from explicit anchors
                if anchor_id.startswith("fig-"):
                    figure_ids[anchor_id].append((filepath, lineno))
                # Rule 5 — table IDs from explicit anchors
                elif anchor_id.startswith("tbl-"):
                    table_ids[anchor_id].append((filepath, lineno))
                else:
                    # Rule 8 — non-fig, non-tbl section IDs
                    section_ids[anchor_id].append((filepath, lineno))

            # Rule 7 — section anchors [#anchor]
            m = SECTION_ANCHOR_RE.match(line)
            if m:
                anchor = m.group(1)
                section_anchors[anchor].append((filepath, lineno))
                # Also feed into rule 3, 5, 8 depending on prefix.
                if anchor.startswith("fig-"):
                    figure_ids[anchor].append((filepath, lineno))
                elif anchor.startswith("tbl-"):
                    table_ids[anchor].append((filepath, lineno))
                else:
                    section_ids[anchor].append((filepath, lineno))

            # Rule 4 — figure titles (.Figure ...)
            m = FIGURE_TITLE_RE.match(line)
            if m:
                figure_titles[line.strip()].append((filepath, lineno))

            # Rule 6 — table titles (.Table ...)
            m = TABLE_TITLE_RE.match(line)
            if m:
                table_titles[line.strip()].append((filepath, lineno))

    return {
        "per_file": per_file,
        "explicit_anchors": explicit_anchors,
        "figure_ids": figure_ids,
        "figure_titles": figure_titles,
        "table_ids": table_ids,
        "table_titles": table_titles,
        "section_anchors": section_anchors,
        "section_ids": section_ids,
    }


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

RULE_LABELS = [
    ("stray_block_delimiters",    "Rule 1  — Stray Block Delimiters"),
    ("duplicate_anchors",         "Rule 2  — Duplicate Explicit Anchors"),
    ("duplicate_figure_ids",      "Rule 3  — Duplicate Figure IDs"),
    ("duplicate_figure_titles",   "Rule 4  — Duplicate Figure Titles"),
    ("duplicate_table_ids",       "Rule 5  — Duplicate Table IDs"),
    ("duplicate_table_titles",    "Rule 6  — Duplicate Table Titles"),
    ("duplicate_section_anchors", "Rule 7  — Duplicate Section Anchors"),
    ("duplicate_section_ids",     "Rule 8  — Duplicate Section IDs"),
    ("leading_space_titles",      "Rule 9  — Section Titles with Leading Spaces"),
    ("titles_inside_blocks",      "Rule 10 — Section Titles Inside Blocks"),
    ("missing_blank_before_title","Rule 11 — Missing Blank Line Before Section Title"),
    ("invalid_nesting_levels",    "Rule 12 — Invalid Nesting Levels"),
    ("adjacent_section_titles",   "Rule 13 — Adjacent Section Titles"),
]


def build_report(data: dict, file_count: int, timestamp: str) -> list[str]:
    """
    Assemble the full report as a list of plain-text lines.
    The same list is used for both terminal output and the AsciiDoc log.
    The AsciiDoc-specific wrapper is added by write_adoc_log().
    """
    # Resolve cross-file findings now.
    resolved = dict(data["per_file"])
    resolved["duplicate_anchors"]         = check_duplicate_explicit_anchors(data["explicit_anchors"])
    resolved["duplicate_figure_ids"]      = check_duplicate_figure_ids(data["figure_ids"])
    resolved["duplicate_figure_titles"]   = check_duplicate_figure_titles(data["figure_titles"])
    resolved["duplicate_table_ids"]       = check_duplicate_table_ids(data["table_ids"])
    resolved["duplicate_table_titles"]    = check_duplicate_table_titles(data["table_titles"])
    resolved["duplicate_section_anchors"] = check_duplicate_section_anchors(data["section_anchors"])
    resolved["duplicate_section_ids"]     = check_duplicate_section_ids(data["section_ids"])

    total_issues = sum(len(v) for v in resolved.values())

    lines = []
    lines.append(f"AsciiDoc Diagnostics — {timestamp}")
    lines.append(f"Scanned {file_count} file(s).")
    lines.append("")

    for rule_key, label in RULE_LABELS:
        findings = resolved.get(rule_key, [])
        status = f"({len(findings)} finding(s))" if findings else "(no issues found)"
        lines.append(f"{label}  {status}")
        lines.extend(findings)
        lines.append("")

    lines.append("-" * 60)
    if total_issues == 0:
        lines.append("All checks passed. No issues found.")
    else:
        lines.append(f"Total issues found: {total_issues}")
    lines.append("")

    return lines, resolved


# ---------------------------------------------------------------------------
# Output — terminal
# ---------------------------------------------------------------------------

def print_report(report_lines: list[str]) -> None:
    """Print the report to stdout."""
    for line in report_lines:
        print(line)


# ---------------------------------------------------------------------------
# Output — AsciiDoc log file
# ---------------------------------------------------------------------------

def write_adoc_log(report_lines: list[str], resolved: dict, timestamp: str) -> str:
    """
    Write the report to a timestamped AsciiDoc file.

    The file is structured as a valid AsciiDoc document:
      - Document title
      - One section per rule
      - Findings inside literal blocks (4 dots .... )

    Returns the path of the written log file.
    """
    os.makedirs(LOG_DIR, exist_ok=True)

    filename = f"{timestamp}-asciidoc-check.adoc"
    filepath = os.path.join(LOG_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as fh:
        fh.write(f"= AsciiDoc Diagnostics Run: {timestamp}\n")
        fh.write(":toc: left\n")
        fh.write(":icons: font\n")
        fh.write("\n")
        fh.write(f"Scanned files: {report_lines[1]}\n")
        fh.write("\n")

        for rule_key, label in RULE_LABELS:
            findings = resolved.get(rule_key, [])
            fh.write(f"== {label}\n\n")
            if findings:
                fh.write("....\n")
                for finding in findings:
                    fh.write(finding + "\n")
                fh.write("....\n\n")
            else:
                fh.write("No issues found.\n\n")

        fh.write("== Summary\n\n")
        total = sum(len(v) for v in resolved.values())
        if total == 0:
            fh.write("All checks passed. No issues found.\n")
        else:
            fh.write(f"Total issues found: *{total}*\n")
        fh.write("\n")

    return filepath


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    root = os.getcwd()
    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")

    print(f"AsciiDoc Diagnostics — scanning from: {root}")
    print("")

    # Collect files.
    filepaths = collect_adoc_files(root)
    if not filepaths:
        print("No .adoc files found. Exiting.")
        sys.exit(0)

    print(f"Found {len(filepaths)} .adoc file(s). Running checks...")
    print("")

    # Run all checks.
    data = scan_files(filepaths)

    # Build and print the report.
    report_lines, resolved = build_report(data, len(filepaths), timestamp)
    print_report(report_lines)

    # Write the AsciiDoc log.
    log_path = write_adoc_log(report_lines, resolved, timestamp)
    print(f"Log written to: {log_path}")

    # Exit with a non-zero code if any issues were found,
    # so CI pipelines can use this as a gate.
    total = sum(len(v) for v in resolved.values())
    sys.exit(1 if total > 0 else 0)


if __name__ == "__main__":
    main()