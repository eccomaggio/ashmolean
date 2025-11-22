#!/usr/bin/env python3
"""
regex_apply_gui.py (tolerant pattern-file parsing)

- Treats runs of 3+ spaces as a tab separator when parsing the pattern file,
  so editors that replace tabs with multiple spaces (e.g. VS Code) won't break flags.
- Optional change-file argument (default: changes.jsonl)
- Pattern file may include flags column (pattern<TAB>replacement<TAB>flags)
- Faster pattern application using re.finditer + slice concatenation
- Simple command-line progress display during processing
- GUI: diff view + editor preserved
- All timestamps are timezone-aware (UTC) using datetime.now(timezone.utc)
"""
from __future__ import annotations

import argparse
import os
import re
import json
import shutil
import sys
from datetime import datetime, timezone
from typing import List, Tuple, Optional, Dict, Any
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

# ------------ Utilities & parsing -------------


FLAG_MAP = {
    "IGNORECASE": re.IGNORECASE,
    "I": re.IGNORECASE,
    "MULTILINE": re.MULTILINE,
    "M": re.MULTILINE,
    "DOTALL": re.DOTALL,
    "S": re.DOTALL,
    "VERBOSE": re.VERBOSE,
    "X": re.VERBOSE,
}


def parse_flag_string(flag_str: str) -> int:
    """Convert a string like 'IGNORECASE,MULTILINE' into combined re flags."""
    if not flag_str:
        return 0
    flags = 0
    for part in re.split(r"[,\s]+", flag_str.strip()):
        if not part:
            continue
        key = part.strip().upper()
        if key in FLAG_MAP:
            flags |= FLAG_MAP[key]
        else:
            # warn but ignore unknown flags
            print(f"Warning: unknown flag '{part}' in pattern file (ignored).", file=sys.stderr)
    return flags


def _normalize_separators(line: str) -> str:
    """
    Normalize separators in a pattern file line.

    - If the user used real TABs, keep them.
    - If the user's editor replaced tabs with multiple spaces (e.g. 4 spaces),
      treat any run of 3 or more spaces as a TAB separator by converting
      runs of 3+ spaces into '\t'.

    This makes the parser forgiving for VS Code and similar editors.
    """
    # Replace runs of 3 or more spaces with a tab
    return re.sub(r" {3,}", "\t", line)


def parse_patterns(path: str) -> List[Tuple[str, str, int]]:
    """
    Parse the pattern file.

    Accepts lines of forms:
      pattern<TAB>replacement
      pattern<TAB>replacement<TAB>flags
    Also accepts 'pattern => replacement' (without flags).

    This function is tolerant: runs of 3+ spaces are treated as TABs so that
    editors which expand tabs to spaces won't break the flags column.
    Returns list of (pattern_str, replacement_str, flags_int).
    """
    patterns = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.rstrip("\n")
            if not line or line.strip().startswith("#"):
                continue

            # Normalize separators to accept runs of 3+ spaces as tabs
            norm = _normalize_separators(line)

            # Prefer tab-separated (supports optional flags column)
            if "\t" in norm:
                parts = norm.split("\t")
                if len(parts) >= 2:
                    pat = parts[0]
                    repl = parts[1]
                    flags_part = parts[2] if len(parts) >= 3 else ""
                else:
                    pat, repl, flags_part = norm, "", ""
            elif " => " in norm:
                # '=>' form: no flags supported here (unless user used actual tabs)
                pat, repl = norm.split(" => ", 1)
                flags_part = ""
            else:
                # fallback: treat first whitespace as separator
                parts = norm.split(None, 1)
                if len(parts) == 2:
                    pat, repl = parts
                    flags_part = ""
                else:
                    pat, repl, flags_part = norm, "", ""

            flags = parse_flag_string(flags_part)
            patterns.append((pat, repl, flags))
    return patterns


def backup_file(path: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    dest = f"{path}.bak.{timestamp}"
    shutil.copy2(path, dest)
    return dest


def emit_change_record(fpath: str, record: Dict[str, Any]):
    """Append a JSON line to the change file (ensure UTF-8 and non-ascii preserved)."""
    with open(fpath, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ------------ Fast pattern application -------------


def _print_progress(prefix: str, current: int, total: int, width: int = 40):
    """Simple command-line progress bar (overwrites the line)."""
    if total <= 0:
        bar = (" " * width)
        pct = 0.0
    else:
        frac = current / total
        pct = frac * 100.0
        fill = int(round(frac * width))
        bar = "#" * fill + "-" * (width - fill)
    sys.stdout.write(f"\r{prefix} [{bar}] {current}/{total} ({pct:5.1f}%)")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")


def apply_patterns_and_record_fast(
    text: str,
    patterns: List[Tuple[str, str, int]],
    change_file: str,
    round_id: int = 1,
    show_progress: bool = True,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Apply patterns sequentially using re.finditer + slice concatenation.

    For each match, we:
      - compute replacement via match.expand(repl)
      - append deletion record (if matched text not empty)
      - append insertion record (if replacement text not empty)

    Returns (final_text, list_of_records_emitted_during_run)
    """
    current = text
    all_records: List[Dict[str, Any]] = []
    total_patterns = len(patterns)

    for pidx, (pat_text, repl_text, flags) in enumerate(patterns, start=1):
        prefix = f"Pattern {pidx}/{total_patterns}"
        # compile pattern with flags; allow inline flags in the pattern as well
        try:
            pattern = re.compile(pat_text, flags=flags)
        except re.error as e:
            print(f"\nRegex compile error for pattern index {pidx-1}: {pat_text!r} -> {e}", file=sys.stderr)
            continue

        # find matches first (we'll iterate over match objects)
        # Using finditer will scan the string once; we'll process matches left-to-right
        matches = list(pattern.finditer(current))
        num_matches = len(matches)

        if num_matches == 0:
            if show_progress:
                _print_progress(prefix, 0, 0)
            continue

        # Build new string by concatenating slices and replacements
        pieces: List[str] = []
        last_end = 0
        processed = 0
        for mi, m in enumerate(matches):
            a, b = m.start(), m.end()  # span in 'current' string
            # append unchanged chunk before match
            if last_end < a:
                pieces.append(current[last_end:a])
            matched_text = current[a:b]
            # compute replacement (supports backrefs etc.)
            try:
                replacement_text = m.expand(repl_text)
            except re.error:
                # fallback: use simple replacement string if expand fails
                replacement_text = repl_text

            # emit delete record if matched_text not empty
            ts = datetime.now(timezone.utc).isoformat()
            if len(matched_text) > 0:
                rec_del = {
                    "timestamp": ts,
                    "round": round_id,
                    "pattern_idx": pidx - 1,
                    "pattern": pat_text,
                    "replacement": repl_text,
                    "type": "delete",
                    "pos": a,
                    "length": len(matched_text),
                    "text": matched_text,
                }
                emit_change_record(change_file, rec_del)
                all_records.append(rec_del)

            # emit insert record if replacement_text not empty
            if len(replacement_text) > 0:
                rec_ins = {
                    "timestamp": ts,
                    "round": round_id,
                    "pattern_idx": pidx - 1,
                    "pattern": pat_text,
                    "replacement": repl_text,
                    "type": "insert",
                    "pos": a,
                    "length": len(replacement_text),
                    "text": replacement_text,
                }
                emit_change_record(change_file, rec_ins)
                all_records.append(rec_ins)

            # append replacement
            pieces.append(replacement_text)
            last_end = b

            # update progress occasionally
            processed += 1
            if show_progress and (processed % 100 == 0 or processed == num_matches):
                _print_progress(prefix, processed, num_matches)

        # append remainder
        if last_end < len(current):
            pieces.append(current[last_end:])
        new_text = "".join(pieces)

        # update current text to new_text for next pattern
        current = new_text

    return current, all_records


# ------------ Diff view helpers (kept similar to before) -------------


from difflib import SequenceMatcher


def build_merged_diff_view(original: str, modified: str) -> Tuple[str, List[Tuple[str, int, int]]]:
    """
    Build merged view string and tag ranges for 'del' and 'ins'.
    Uses SequenceMatcher once (between original and final modified) — cheap relative to the earlier iteration-per-pattern approach.
    """
    sm = SequenceMatcher(a=original, b=modified)
    pieces = []
    tag_ranges: List[Tuple[str, int, int]] = []
    cur_pos = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            s = original[i1:i2]
            pieces.append(s)
            cur_pos += len(s)
        elif tag == "delete":
            s = original[i1:i2]
            pieces.append(s)
            tag_ranges.append(("del", cur_pos, cur_pos + len(s)))
            cur_pos += len(s)
        elif tag == "insert":
            s = modified[j1:j2]
            pieces.append(s)
            tag_ranges.append(("ins", cur_pos, cur_pos + len(s)))
            cur_pos += len(s)
        elif tag == "replace":
            sdel = original[i1:i2]
            sins = modified[j1:j2]
            pieces.append(sdel)
            tag_ranges.append(("del", cur_pos, cur_pos + len(sdel)))
            cur_pos += len(sdel)
            pieces.append(sins)
            tag_ranges.append(("ins", cur_pos, cur_pos + len(sins)))
            cur_pos += len(sins)
    merged = "".join(pieces)
    return merged, tag_ranges


def build_insertion_tag_ranges(original: str, modified: str) -> List[Tuple[int, int]]:
    sm = SequenceMatcher(a=original, b=modified)
    ranges: List[Tuple[int, int]] = []
    for tag, a1, a2, b1, b2 in sm.get_opcodes():
        if tag == "insert" or tag == "replace":
            ranges.append((b1, b2))
    return ranges


# ------------ GUI Application -------------


class RegexApplyGUI:
    def __init__(self, plaintext_path: str, patterns_path: str, change_path: str, show_progress: bool = True):
        self.plaintext_path = plaintext_path
        self.patterns_path = patterns_path
        self.change_path = change_path
        self.show_progress = show_progress

        with open(self.plaintext_path, "r", encoding="utf-8") as f:
            self.original_text = f.read()

        self.patterns = parse_patterns(self.patterns_path)

        if not os.path.exists(self.change_path):
            open(self.change_path, "w", encoding="utf-8").close()

        backup = backup_file(self.plaintext_path)
        print(f"Backup of original saved to: {backup}")

        self.round_id = 1
        # Use fast apply
        self.modified_text, self.auto_change_records = apply_patterns_and_record_fast(
            self.original_text, self.patterns, self.change_path, round_id=self.round_id, show_progress=self.show_progress
        )

        # Build GUI
        self.root = tk.Tk()
        self.root.title(f"Regex Apply — {os.path.basename(plaintext_path)}")
        self._build_ui()

    def _build_ui(self):
        pad = 6
        self.root.geometry("1000x700")

        mainframe = ttk.Frame(self.root, padding=pad)
        mainframe.pack(fill="both", expand=True)

        # Toolbar
        toolbar = ttk.Frame(mainframe)
        toolbar.pack(fill="x", padx=pad, pady=(0, pad))

        btn_save = ttk.Button(toolbar, text="Save edits", command=self.on_save_edits)
        btn_save.pack(side="left")

        btn_reapply = ttk.Button(toolbar, text="Re-apply patterns", command=self.on_reapply_patterns)
        btn_reapply.pack(side="left", padx=(6, 0))

        btn_open_plain = ttk.Button(toolbar, text="Open plaintext file...", command=self.on_open_plain)
        btn_open_plain.pack(side="left", padx=(6, 0))

        btn_quit = ttk.Button(toolbar, text="Quit", command=self.root.quit)
        btn_quit.pack(side="right")

        # Notebook tabs
        notebook = ttk.Notebook(mainframe)
        notebook.pack(fill="both", expand=True)

        # Diff view
        diff_frame = ttk.Frame(notebook)
        notebook.add(diff_frame, text="Diff view (merged)")

        diff_text = ScrolledText(diff_frame, wrap="word")
        diff_text.pack(fill="both", expand=True)
        diff_text.configure(state="normal")
        merged, tag_ranges = build_merged_diff_view(self.original_text, self.modified_text)
        diff_text.insert("1.0", merged)
        diff_text.tag_configure("ins", background="#fff07a")
        diff_text.tag_configure("del", background="#ffd7d7", overstrike=True)
        for tag, start_idx, end_idx in tag_ranges:
            start_index = f"1.0 + {start_idx} chars"
            end_index = f"1.0 + {end_idx} chars"
            diff_text.tag_add(tag, start_index, end_index)
        diff_text.configure(state="disabled")

        # Editor tab
        edit_frame = ttk.Frame(notebook)
        notebook.add(edit_frame, text="Editor (modified text)")

        self.editor = ScrolledText(edit_frame, wrap="word")
        self.editor.pack(fill="both", expand=True)
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", self.modified_text)

        # highlight insertions in editor
        self.editor.tag_configure("ins", background="#fff07a")
        ins_ranges = build_insertion_tag_ranges(self.original_text, self.modified_text)
        for s, e in ins_ranges:
            start_index = f"1.0 + {s} chars"
            end_index = f"1.0 + {e} chars"
            self.editor.tag_add("ins", start_index, end_index)

        status = ttk.Label(mainframe, text=f"Patterns loaded: {len(self.patterns)} — changes recorded: {len(self.auto_change_records)}")
        status.pack(fill="x", padx=pad, pady=(pad, 0))
        self.status_label = status

    def on_save_edits(self):
        edited = self.editor.get("1.0", "end-1c")
        sm = SequenceMatcher(a=self.modified_text, b=edited)
        any_changes = False
        records = []
        for tag, a1, a2, b1, b2 in sm.get_opcodes():
            if tag == "equal":
                continue
            any_changes = True
            ts = datetime.now(timezone.utc).isoformat()
            if tag == "delete":
                rec = {
                    "timestamp": ts,
                    "round": None,
                    "pattern_idx": None,
                    "type": "delete",
                    "pos": a1,
                    "length": a2 - a1,
                    "text": self.modified_text[a1:a2],
                }
                records.append(rec)
                emit_change_record(self.change_path, rec)
            elif tag == "insert":
                rec = {
                    "timestamp": ts,
                    "round": None,
                    "pattern_idx": None,
                    "type": "insert",
                    "pos": a1,
                    "length": b2 - b1,
                    "text": edited[b1:b2],
                }
                records.append(rec)
                emit_change_record(self.change_path, rec)
            elif tag == "replace":
                rec_del = {
                    "timestamp": ts,
                    "round": None,
                    "pattern_idx": None,
                    "type": "delete",
                    "pos": a1,
                    "length": a2 - a1,
                    "text": self.modified_text[a1:a2],
                }
                rec_ins = {
                    "timestamp": ts,
                    "round": None,
                    "pattern_idx": None,
                    "type": "insert",
                    "pos": a1,
                    "length": b2 - b1,
                    "text": edited[b1:b2],
                }
                records.extend([rec_del, rec_ins])
                emit_change_record(self.change_path, rec_del)
                emit_change_record(self.change_path, rec_ins)
        if not any_changes:
            messagebox.showinfo("Save edits", "No changes detected.")
            return

        backup = backup_file(self.plaintext_path)
        try:
            with open(self.plaintext_path, "w", encoding="utf-8") as fh:
                fh.write(edited)
        except Exception as e:
            messagebox.showerror("Save error", f"Failed to write plaintext file: {e}")
            return

        self.original_text = edited
        self.modified_text = edited
        self.status_label.configure(text=f"Patterns loaded: {len(self.patterns)} — changes recorded: {len(self.auto_change_records) + len(records)}")
        messagebox.showinfo("Save edits", f"Saved edits to {self.plaintext_path}. Backup: {backup}")

    def on_reapply_patterns(self):
        with open(self.plaintext_path, "r", encoding="utf-8") as fh:
            current_plain = fh.read()
        self.round_id += 1
        new_text, new_records = apply_patterns_and_record_fast(current_plain, self.patterns, self.change_path, round_id=self.round_id, show_progress=self.show_progress)
        backup = backup_file(self.plaintext_path)
        with open(self.plaintext_path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        self.original_text = current_plain
        self.modified_text = new_text
        self.auto_change_records.extend(new_records)
        for w in self.root.winfo_children():
            w.destroy()
        self._build_ui()
        messagebox.showinfo("Re-apply patterns", f"Patterns re-applied. Backup of prior plaintext: {backup}")

    def on_open_plain(self):
        path = filedialog.askopenfilename(title="Open plaintext file", defaultextension=".txt")
        if not path:
            return
        self.plaintext_path = path
        with open(self.plaintext_path, "r", encoding="utf-8") as fh:
            self.original_text = fh.read()
        self.round_id += 1
        new_text, new_records = apply_patterns_and_record_fast(self.original_text, self.patterns, self.change_path, round_id=self.round_id, show_progress=self.show_progress)
        self.modified_text = new_text
        self.auto_change_records.extend(new_records)
        for w in self.root.winfo_children():
            w.destroy()
        self._build_ui()

    def run(self):
        self.root.mainloop()


# ------------ Main -------------


def main():
    parser = argparse.ArgumentParser(description="Apply regex patterns to a plaintext file and show diffs + editor")
    parser.add_argument("plaintext", help="Path to plaintext file (UTF-8)")
    parser.add_argument("patterns", help="Path to pattern file (each line: pattern<TAB>replacement or pattern<TAB>replacement<TAB>flags)")
    parser.add_argument("changes", nargs="?", default="changes.jsonl", help="Path to change file (JSON Lines). Optional; default 'changes.jsonl'")
    parser.add_argument("--no-progress", action="store_true", help="Disable command-line progress output")
    args = parser.parse_args()

    if not os.path.exists(args.plaintext):
        print(f"Plaintext file not found: {args.plaintext}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(args.patterns):
        print(f"Pattern file not found: {args.patterns}", file=sys.stderr)
        sys.exit(1)

    app = RegexApplyGUI(args.plaintext, args.patterns, args.changes, show_progress=not args.no_progress)
    app.run()


if __name__ == "__main__":
    main()