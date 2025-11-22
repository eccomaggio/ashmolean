#!/usr/bin/env python3
"""
regex_apply_pyside6.py

Usage:
    python regex_apply_pyside6.py plaintext.txt patterns.txt [changes.jsonl]

Requirements:
    pip install PySide6

Features:
- Reads plaintext UTF-8, pattern file, optional changes JSONL (default 'changes.jsonl')
- Pattern file tolerant to tabs replaced by spaces (runs of 3+ spaces -> tab)
- Patterns: pattern<TAB>replacement<TAB>flags  OR  pattern => replacement
- Flags accepted (case-insensitive): IGNORECASE/I, MULTILINE/M, DOTALL/S, VERBOSE/X, ASCII/A, LOCALE/L, DEBUG
- Faster application using re.finditer + slice concatenation
- Background worker thread so GUI appears immediately
- Progress reporting in GUI and console
- Merged diff view (deleted text: light red + strike-through; inserted text: yellow)
- Editor (modified text) with inserted-text highlights (yellow)
- Save edits: writes plaintext backup, writes changed file entries (JSON Lines), updates UI
- All timestamps timezone-aware (UTC) via datetime.now(timezone.utc).isoformat()
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

# ------------- Flags map -------------
FLAG_MAP = {
    "IGNORECASE": re.IGNORECASE,
    "I": re.IGNORECASE,
    "MULTILINE": re.MULTILINE,
    "M": re.MULTILINE,
    "DOTALL": re.DOTALL,
    "S": re.DOTALL,
    "VERBOSE": re.VERBOSE,
    "X": re.VERBOSE,
    "ASCII": re.ASCII,
    "A": re.ASCII,
    "LOCALE": re.LOCALE,
    "L": re.LOCALE,
    "DEBUG": re.DEBUG,
}

# ------------- Utilities & parsing -------------


def parse_flag_string(flag_str: str) -> int:
    """Convert a string like 'ignorecase,multiline' into combined re flags (case-insensitive)."""
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
            print(f"Warning: unknown flag '{part}' in pattern file (ignored).", file=sys.stderr)
    return flags


def _normalize_separators(line: str) -> str:
    """
    Treat runs of 3+ spaces as a TAB separator (for editors that expand tabs).
    """
    return re.sub(r" {3,}", "\t", line)


def parse_patterns(path: str) -> List[Tuple[str, str, int]]:
    """
    Parse the pattern file and return list of (pattern, replacement, flags_int)
    Accepts:
      - pattern<TAB>replacement
      - pattern<TAB>replacement<TAB>flags
      - pattern => replacement (no flags)
    """
    patterns: List[Tuple[str, str, int]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.rstrip("\n")
            if not line or line.strip().startswith("#"):
                continue
            norm = _normalize_separators(line)
            if "\t" in norm:
                parts = norm.split("\t")
                pat = parts[0]
                repl = parts[1] if len(parts) >= 2 else ""
                flags_part = parts[2] if len(parts) >= 3 else ""
            elif " => " in norm:
                pat, repl = norm.split(" => ", 1)
                flags_part = ""
            else:
                # fallback: first whitespace
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
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    dest = f"{path}.bak.{ts}"
    shutil.copy2(path, dest)
    return dest


def emit_change_record(fpath: str, record: Dict[str, Any]) -> None:
    with open(fpath, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ------------- Worker & data structures -------------


@dataclass
class ApplyResult:
    modified_text: str
    merged_text: str
    ins_ranges: List[Tuple[int, int]]  # ranges in modified_text that were inserted/replaced
    del_ranges: List[Tuple[int, int]]  # ranges in merged_text that are deletions (for merged view)
    records: List[Dict[str, Any]]


class ApplyWorker(QObject):
    """
    Runs in a QThread. Emits progress and final result.
    """
    progress = Signal(int, int, str)  # pattern_index, num_matches_processed, message
    finished = Signal(object)  # ApplyResult
    error = Signal(str)

    def __init__(self, original_text: str, patterns: List[Tuple[str, str, int]], change_file: str, round_id: int = 1, show_console_progress: bool = True):
        super().__init__()
        self.original_text = original_text
        self.patterns = patterns
        self.change_file = change_file
        self.round_id = round_id
        self.show_console_progress = show_console_progress

    @Slot()
    def run(self):
        try:
            modified, ins_ranges, records = self._apply_patterns_fast(self.original_text, self.patterns, self.change_file)
            # build merged view and deletion ranges using SequenceMatcher
            merged, merged_tag_ranges = self._build_merged_and_tag_ranges(self.original_text, modified)
            # extract del ranges (in merged text) and keep ins_ranges (in modified)
            del_ranges = [r for t, r in merged_tag_ranges if t == "del"]
            # emit finished result
            res = ApplyResult(modified_text=modified, merged_text=merged, ins_ranges=ins_ranges, del_ranges=del_ranges, records=records)
            self.finished.emit(res)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self.error.emit(f"{e}\n{tb}")

    def _print_console_progress(self, prefix: str, cur: int, total: int, width: int = 40):
        if not self.show_console_progress:
            return
        if total <= 0:
            bar = " " * width
            pct = 0.0
        else:
            frac = cur / total
            pct = frac * 100.0
            fill = int(round(frac * width))
            bar = "#" * fill + "-" * (width - fill)
        sys.stdout.write(f"\r{prefix} [{bar}] {cur}/{total} ({pct:5.1f}%)")
        sys.stdout.flush()
        if cur >= total:
            sys.stdout.write("\n")

    def _apply_patterns_fast(self, text: str, patterns: List[Tuple[str, str, int]], change_file: str) -> Tuple[str, List[Tuple[int, int]], List[Dict[str, Any]]]:
        """
        Apply patterns sequentially using finditer and slice concatenation.
        Returns: (final_text, insertion_ranges_in_final_text, list_of_records_emitted)
        Also writes JSONL records to change_file as we go.
        """
        current = text
        all_records: List[Dict[str, Any]] = []
        # We'll collect insertion ranges relative to the progressively-built new_text.
        for pidx, (pat_text, repl_text, flags) in enumerate(self.patterns, start=1):
            prefix = f"Pattern {pidx}/{len(self.patterns)}"
            try:
                pattern = re.compile(pat_text, flags=flags)
            except re.error as e:
                # compile error: skip pattern but warn
                self.progress.emit(pidx, 0, f"Regex compile error: {e}")
                print(f"Regex compile error for pattern {pidx-1}: {pat_text!r} -> {e}", file=sys.stderr)
                continue

            matches = list(pattern.finditer(current))
            num_matches = len(matches)
            if num_matches == 0:
                self.progress.emit(pidx, 0, "0 matches")
                if self.show_console_progress:
                    self._print_console_progress(prefix, 0, 0)
                continue

            pieces: List[str] = []
            ins_ranges_for_pattern: List[Tuple[int, int]] = []
            last_end = 0
            new_pos = 0  # position in the new text being constructed
            processed = 0
            for m in matches:
                a, b = m.start(), m.end()
                # unchanged chunk before match
                if last_end < a:
                    chunk = current[last_end:a]
                    pieces.append(chunk)
                    new_pos += len(chunk)
                matched_text = current[a:b]
                try:
                    replacement_text = m.expand(repl_text)
                except re.error:
                    replacement_text = repl_text

                ts = datetime.now(timezone.utc).isoformat()
                # delete record (if any)
                if len(matched_text) > 0:
                    rec_del = {
                        "timestamp": ts,
                        "round": self.round_id,
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

                # insert record (if any)
                if len(replacement_text) > 0:
                    rec_ins = {
                        "timestamp": ts,
                        "round": self.round_id,
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
                    # record insertion range in the new text (current new_pos .. new_pos+len)
                    ins_ranges_for_pattern.append((new_pos, new_pos + len(replacement_text)))

                pieces.append(replacement_text)
                new_pos += len(replacement_text)
                last_end = b

                processed += 1
                # update progress signals
                if processed % 100 == 0 or processed == num_matches:
                    self.progress.emit(pidx, processed, f"processed {processed}/{num_matches}")
                    self._print_console_progress(prefix, processed, num_matches)

            # remainder
            if last_end < len(current):
                tail = current[last_end:]
                pieces.append(tail)
                new_pos += len(tail)
            new_text = "".join(pieces)
            # update current and continue
            current = new_text
            # accumulate ins_ranges into a global list but adjusting offsets caused by prior patterns:
            # NOTE: ins_ranges are collected per pattern but are already expressed in the updated text for that pattern.
            # We'll collect them all across patterns by translating them into the final text only after all patterns are done.
            # For simplicity and correctness we store the insertions from each pattern relative to the *current* text at that time
            # and will recompute/merge ranges using SequenceMatcher after the full run.
            # So here we append them to all_records via JSONL and also track nothing else.
            # However, for editor highlighting we need ranges in the final modified text. We'll compute those after.
            # For speed, we skip precomputing final insertion offsets here.
        # at this point 'current' is final modified text
        # We'll compute insertion ranges in final modified text by diffing original vs modified:
        ins_ranges_final: List[Tuple[int, int]] = []
        del_ins_records = [r for r in all_records if r.get("type") in ("insert", "delete")]
        # Build ins ranges in final text by running SequenceMatcher and collecting 'insert' and 'replace' ranges
        sm = SequenceMatcher(a=self.original_text, b=current)
        for tag, a1, a2, b1, b2 in sm.get_opcodes():
            if tag == "insert" or tag == "replace":
                ins_ranges_final.append((b1, b2))
        # records already emitted to file; return final modified text and ins ranges and records
        return current, ins_ranges_final, all_records

    def _build_merged_and_tag_ranges(self, original: str, modified: str) -> Tuple[str, List[Tuple[str, Tuple[int, int]]]]:
        """
        Build merged string (deleted chunks included) and return merged and a list of tag ranges like:
          [("del", (start,end)), ("ins", (start,end)), ...]
        The merged string is original+inserts arranged so that deletions remain visible (so we can strike them).
        """
        sm = SequenceMatcher(a=original, b=modified)
        pieces: List[str] = []
        tag_ranges: List[Tuple[str, Tuple[int, int]]] = []
        cur_pos = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                s = original[i1:i2]
                pieces.append(s)
                cur_pos += len(s)
            elif tag == "delete":
                s = original[i1:i2]
                pieces.append(s)
                tag_ranges.append(("del", (cur_pos, cur_pos + len(s))))
                cur_pos += len(s)
            elif tag == "insert":
                s = modified[j1:j2]
                pieces.append(s)
                tag_ranges.append(("ins", (cur_pos, cur_pos + len(s))))
                cur_pos += len(s)
            elif tag == "replace":
                sdel = original[i1:i2]
                sins = modified[j1:j2]
                pieces.append(sdel)
                tag_ranges.append(("del", (cur_pos, cur_pos + len(sdel))))
                cur_pos += len(sdel)
                pieces.append(sins)
                tag_ranges.append(("ins", (cur_pos, cur_pos + len(sins))))
                cur_pos += len(sins)
        merged = "".join(pieces)
        return merged, tag_ranges


# ------------- Helpers for merging ranges -------------


def merge_ranges(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    if not ranges:
        return []
    sorted_ranges = sorted(ranges, key=lambda r: r[0])
    merged: List[Tuple[int, int]] = []
    cur_s, cur_e = sorted_ranges[0]
    for s, e in sorted_ranges[1:]:
        if s <= cur_e:
            cur_e = max(cur_e, e)
        else:
            merged.append((cur_s, cur_e))
            cur_s, cur_e = s, e
    merged.append((cur_s, cur_e))
    return merged


# ------------- Main Window (PySide6) -------------


class MainWindow(QMainWindow):
    def __init__(self, plaintext_path: str, patterns_path: str, changes_path: str, patterns: List[Tuple[str, str, int]], show_console_progress: bool = True):
        super().__init__()
        self.plaintext_path = plaintext_path
        self.patterns_path = patterns_path
        self.changes_path = changes_path
        self.patterns = patterns
        self.show_console_progress = show_console_progress

        # load plaintext
        with open(self.plaintext_path, "r", encoding="utf-8") as fh:
            self.original_text = fh.read()

        # ensure change file exists
        if not os.path.exists(self.changes_path):
            open(self.changes_path, "w", encoding="utf-8").close()

        # create backup
        backup = backup_file(self.plaintext_path)
        print(f"Backup saved to: {backup}")

        self.worker_thread: Optional[QThread] = None
        self.worker: Optional[ApplyWorker] = None

        self._build_ui()
        self._start_worker()

    def _build_ui(self):
        self.setWindowTitle(f"Regex Apply — {os.path.basename(self.plaintext_path)}")
        self.resize(1200, 800)

        # toolbar
        toolbar = QToolBar()
        self.addToolBar(toolbar)

        btn_save = QPushButton("Save edits")
        btn_save.clicked.connect(self.on_save_edits)
        toolbar.addWidget(btn_save)

        btn_reapply = QPushButton("Re-apply patterns")
        btn_reapply.clicked.connect(self.on_reapply_patterns)
        toolbar.addWidget(btn_reapply)

        btn_open = QPushButton("Open plaintext...")
        btn_open.clicked.connect(self.on_open_plain)
        toolbar.addWidget(btn_open)

        btn_quit = QPushButton("Quit")
        btn_quit.clicked.connect(self.close)
        toolbar.addWidget(btn_quit)

        # status area: label + progress bar
        status_widget = QWidget()
        status_layout = QHBoxLayout(status_widget)
        status_layout.setContentsMargins(4, 4, 4, 4)
        self.status_label = QLabel("Starting...")
        status_layout.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        status_layout.addWidget(self.progress_bar)

        # editor and diff panes in a splitter
        splitter = QSplitter(Qt.Horizontal)
        # merged diff view (left)
        self.diff_edit = QTextEdit()
        self.diff_edit.setReadOnly(True)
        self.diff_edit.setFont(QFont("Courier", 10))
        splitter.addWidget(self.diff_edit)
        # modified editor (right)
        self.editor = QTextEdit()
        self.editor.setFont(QFont("Courier", 10))
        splitter.addWidget(self.editor)
        splitter.setSizes([600, 600])

        # layout
        central = QWidget()
        vlay = QVBoxLayout(central)
        vlay.addWidget(status_widget)
        vlay.addWidget(splitter)
        self.setCentralWidget(central)

        # prepare formats (for fast reuse)
        self.ins_format = QTextCharFormat()
        self.ins_format.setBackground(QColor("#fff07a"))  # light yellow
        self.del_format = QTextCharFormat()
        self.del_format.setBackground(QColor("#ffd7d7"))  # light red
        self.del_format.setFontStrikeOut(True)

        # initially show original text in editor (until worker finishes)
        self.editor.setPlainText(self.original_text)
        self.diff_edit.setPlainText("Working... applying patterns (please wait).")

    def _start_worker(self):
        # create worker and thread
        self.worker_thread = QThread()
        self.worker = ApplyWorker(original_text=self.original_text, patterns=self.patterns, change_file=self.changes_path, round_id=1, show_console_progress=self.show_console_progress)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_worker_progress)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.error.connect(self._on_worker_error)
        self.worker.finished.connect(lambda _: self.worker_thread.quit())
        self.worker.finished.connect(lambda _: self.worker_thread.wait())
        self.worker.error.connect(lambda _: self.worker_thread.quit())
        self.worker_thread.start()

    @Slot(int, int, str)
    def _on_worker_progress(self, pattern_index: int, processed: int, msg: str):
        # pattern_index is 1-based; processed is matches processed for that pattern
        self.status_label.setText(f"Applying pattern {pattern_index}: {msg}")
        # show a simple animation of processed fraction if we had numbers — we do not know total matches here
        # incremental visualization: pulse progress bar
        self.progress_bar.setValue((self.progress_bar.value() + 5) % 100)

    @Slot(object)
    def _on_worker_finished(self, result_obj: ApplyResult):
        # populate UI with result
        res: ApplyResult = result_obj
        self.status_label.setText("Finished applying patterns.")
        self.progress_bar.setValue(100)
        # Fill merged diff view with formatting for deletions (red+strike) and insertions (yellow)
        self._populate_merged_diff(res.merged_text, res.del_ranges, [])  # ins ranges in merged are marked by 'ins' from builder, but we only have del_ranges; merges done inside
        # Fill editor with modified text and highlight insertions
        self._populate_editor_with_insertions(res.modified_text, res.ins_ranges)
        # store latest state
        self.original_text = res.modified_text
        self.modified_text = res.modified_text
        self.auto_change_records = res.records
        # small message
        QMessageBox.information(self, "Done", "Patterns applied and views updated.")

    @Slot(str)
    def _on_worker_error(self, text: str):
        QMessageBox.critical(self, "Worker error", f"Error during processing:\n{text}")

    def _populate_merged_diff(self, merged_text: str, del_ranges: List[Tuple[int, int]], ins_ranges: List[Tuple[int, int]]):
        """
        Insert merged_text once into diff_edit and apply formatting.
        del_ranges are ranges in merged_text that should be shown red+strikethrough.
        ins_ranges (optional) are ranges to highlight as inserted (yellow).
        """
        self.diff_edit.setReadOnly(False)
        self.diff_edit.clear()
        self.diff_edit.setPlainText(merged_text)
        cursor = self.diff_edit.textCursor()
        # apply deletions (merge ranges first)
        merged_del = merge_ranges(del_ranges)
        for s, e in merged_del:
            cursor.setPosition(s)
            cursor.setPosition(e, QTextCursor.KeepAnchor)
            cursor.setCharFormat(self.del_format)
        # apply insertions if any (likely empty here)
        merged_ins = merge_ranges(ins_ranges)
        for s, e in merged_ins:
            cursor.setPosition(s)
            cursor.setPosition(e, QTextCursor.KeepAnchor)
            cursor.setCharFormat(self.ins_format)
        self.diff_edit.setReadOnly(True)

    def _populate_editor_with_insertions(self, modified_text: str, ins_ranges: List[Tuple[int, int]]):
        """
        Fill editor with modified_text and highlight insertions (merged ranges).
        ins_ranges are character offsets in modified_text.
        """
        self.editor.setPlainText(modified_text)
        cursor = self.editor.textCursor()
        merged_ins = merge_ranges(ins_ranges)
        for s, e in merged_ins:
            cursor.setPosition(s)
            cursor.setPosition(e, QTextCursor.KeepAnchor)
            cursor.setCharFormat(self.ins_format)

    # ---------- Actions ----------
    def on_save_edits(self):
        edited = self.editor.toPlainText()
        sm = SequenceMatcher(a=self.modified_text, b=edited)
        records: List[Dict[str, Any]] = []
        any_changes = False
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
                emit_change_record(self.changes_path, rec)
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
                emit_change_record(self.changes_path, rec)
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
                emit_change_record(self.changes_path, rec_del)
                emit_change_record(self.changes_path, rec_ins)
        if not any_changes:
            QMessageBox.information(self, "Save edits", "No changes detected.")
            return
        try:
            backup = backup_file(self.plaintext_path)
            with open(self.plaintext_path, "w", encoding="utf-8") as fh:
                fh.write(edited)
        except Exception as e:
            QMessageBox.critical(self, "Save error", f"Failed to write plaintext file: {e}")
            return
        self.original_text = edited
        self.modified_text = edited
        QMessageBox.information(self, "Saved", f"Saved edits. Backup created: {backup}")

    def on_reapply_patterns(self):
        # Re-run worker with current plaintext
        with open(self.plaintext_path, "r", encoding="utf-8") as fh:
            self.original_text = fh.read()
        # stop existing worker thread if running (safeguard)
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.warning(self, "Busy", "Worker is already running.")
            return
        # reset UI and start new worker
        self.editor.setPlainText(self.original_text)
        self.diff_edit.setPlainText("Re-applying patterns...")
        self._start_worker()

    def on_open_plain(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open plaintext file", filter="Text files (*.txt);;All files (*)")
        if not path:
            return
        self.plaintext_path = path
        with open(self.plaintext_path, "r", encoding="utf-8") as fh:
            self.original_text = fh.read()
        # start worker
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.warning(self, "Busy", "Worker is already running.")
            return
        self.editor.setPlainText(self.original_text)
        self.diff_edit.setPlainText("Applying patterns to opened file...")
        self._start_worker()


# ------------- Main entrypoint -------------


def main():
    parser = argparse.ArgumentParser(description="Apply regex patterns to a plaintext file and show diffs + editor (PySide6)")
    parser.add_argument("plaintext", help="Path to plaintext UTF-8 file")
    parser.add_argument("patterns", help="Path to pattern file (pattern<TAB>replacement<TAB>flags) or 'pattern => replacement'")
    parser.add_argument("changes", nargs="?", default="changes.jsonl", help="Path to change file (JSONL). Optional; default 'changes.jsonl'")
    parser.add_argument("--no-progress", action="store_true", help="Disable console progress output")
    args = parser.parse_args()

    if not os.path.exists(args.plaintext):
        print(f"Plaintext file not found: {args.plaintext}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(args.patterns):
        print(f"Pattern file not found: {args.patterns}", file=sys.stderr)
        sys.exit(1)

    # parse patterns
    patterns = parse_patterns(args.patterns)

    app = QApplication(sys.argv)
    win = MainWindow(args.plaintext, args.patterns, args.changes, patterns, show_console_progress=not args.no_progress)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
