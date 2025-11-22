#!/usr/bin/env python3
"""
regex_apply_pyside6_fast_safequit_batched_progress.py

PySide6 GUI for applying regex replacements to a plaintext UTF-8 file.

This variant includes:
- per-pattern progress updates (progress bar shows processed/total for current pattern)
- cooperative shutdown support (safe quit)
- guarded intraline heuristics
- batched GUI updates for fast highlighting

Usage:
    python regex_apply_pyside6_fast_safequit_batched_progress.py [optional_plaintext] [optional_patterns]

Requires:
    pip install PySide6
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
from PySide6.QtWidgets import QApplication

# QAction sometimes lives in QtWidgets, sometimes in QtGui depending on PySide6 build.
try:
    from PySide6.QtWidgets import QAction, QDialog, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton, QSplitter, QTextEdit, QToolBar, QVBoxLayout, QWidget
except ImportError:
    # fallback
    from PySide6.QtGui import QAction  # type: ignore[attr-defined]
    from PySide6.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton, QSplitter, QTextEdit, QToolBar, QVBoxLayout, QWidget

# ---------------- Config (fast defaults) ----------------
DEFAULT_PATTERN_FILE = "patterns.txt"
WRITE_PER_PATTERN_DIFFS = False   # disabled for speed
WRITE_RUN_MANIFEST = False        # disabled to reduce writes
WRITE_RUN_DIFF = False            # single run diff file (set True if you want one)
DIFF_CONTEXT_LINES = 3

# intraline heuristics (guard expensive work)
MAX_INTRALINE_LINES = 300           # if more changed lines than this, skip intraline diffs
MAX_LINE_LEN_FOR_INTRALINE = 2000   # if any changed line longer than this, skip intraline for that line

# ---------------- Flag map ----------------
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

# ---------------- Utilities ----------------


def now_ts_for_filename() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def now_iso_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_flag_string(flag_str: str) -> int:
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
    # treat runs of 3+ spaces as a TAB (helps editors that expand tabs)
    return re.sub(r" {3,}", "\t", line)


def parse_patterns(path: str) -> List[Tuple[str, str, int]]:
    patterns: List[Tuple[str, str, int]] = []
    if not path or not os.path.exists(path):
        return patterns
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
    ts = now_ts_for_filename()
    dest = f"{path}.bak.{ts}"
    shutil.copy2(path, dest)
    return dest


def emit_change_record(fpath: str, record: Dict[str, Any]) -> None:
    # kept for compatibility; not heavily used in fast mode
    try:
        with open(fpath, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def write_unified_diff_file(original_lines: List[str], modified_lines: List[str], filename: str, fromfile: str = "original", tofile: str = "modified") -> None:
    # small wrapper, writes diff only if content non-empty
    from difflib import unified_diff

    diff_lines = list(unified_diff(original_lines, modified_lines, fromfile=fromfile, tofile=tofile, n=DIFF_CONTEXT_LINES))
    if diff_lines:
        with open(filename, "w", encoding="utf-8") as fh:
            fh.writelines(line if line.endswith("\n") else line + "\n" for line in diff_lines)


# ---------------- Worker & types ----------------


@dataclass
class ApplyResult:
    modified_text: str
    merged_text: str
    ins_spans: List[Tuple[int, int]]   # spans in modified_text to highlight (inserted/changed)
    del_spans: List[Tuple[int, int]]   # spans in merged_text to strike-through
    run_diff_path: Optional[str]
    pattern_diff_paths: List[str]
    manifest_path: Optional[str]


class ApplyWorker(QObject):
    # progress: pattern_index, processed_matches, total_matches, message
    progress = Signal(int, int, int, str)
    finished = Signal(object)         # ApplyResult
    error = Signal(str)

    def __init__(self, original_text: str, patterns: List[Tuple[str, str, int]], plaintext_path: Optional[str], patterns_path: Optional[str], out_dir: str, round_id: int = 1, show_console_progress: bool = True):
        super().__init__()
        self.original_text = original_text
        self.patterns = patterns
        self.plaintext_path = plaintext_path
        self.patterns_path = patterns_path
        self.out_dir = out_dir
        self.round_id = round_id
        self.show_console_progress = show_console_progress
        # cooperative stop flag
        self._should_stop = False

    def stop(self) -> None:
        """Signal the worker to stop as soon as possible (cooperative)."""
        self._should_stop = True

    @Slot()
    def run(self):
        try:
            if self._should_stop:
                # stopped before start
                self.finished.emit(ApplyResult(modified_text=self.original_text, merged_text=self.original_text, ins_spans=[], del_spans=[], run_diff_path=None, pattern_diff_paths=[], manifest_path=None))
                return

            modified_text = self._apply_patterns_fast(self.original_text, self.patterns)
            if self._should_stop:
                # return early using current modified_text
                merged_text = modified_text
                del_spans, ins_spans = [], []
                res = ApplyResult(modified_text=modified_text, merged_text=merged_text, ins_spans=ins_spans, del_spans=del_spans, run_diff_path=None, pattern_diff_paths=[], manifest_path=None)
                self.finished.emit(res)
                return

            # optionally write a single run diff (disabled by default)
            run_diff_path = None
            if WRITE_RUN_DIFF and self.out_dir:
                from difflib import unified_diff
                original_lines = self.original_text.splitlines(keepends=True)
                modified_lines = modified_text.splitlines(keepends=True)
                tsfn = now_ts_for_filename()
                run_diff_path = os.path.join(self.out_dir, f"run-{tsfn}.diff")
                write_unified_diff_file(original_lines, modified_lines, run_diff_path, fromfile=os.path.basename(self.plaintext_path or "original"), tofile=os.path.basename(self.plaintext_path or "modified"))

            # merged + tag ranges
            merged_text, tag_ranges = self._build_merged_from_original_and_modified(self.original_text, modified_text)
            # compute intraline spans with guarded heuristics
            del_spans, ins_spans = self._compute_intraline_spans(self.original_text, modified_text, tag_ranges)

            res = ApplyResult(modified_text=modified_text, merged_text=merged_text, ins_spans=ins_spans, del_spans=del_spans, run_diff_path=run_diff_path, pattern_diff_paths=[], manifest_path=None)
            self.finished.emit(res)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            msg = f"{e}\n{tb}"
            print(msg, file=sys.stderr)
            self.error.emit(msg)

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

    def _apply_patterns_fast(self, text: str, patterns: List[Tuple[str, str, int]]) -> str:
        """Apply patterns sequentially using re.finditer + slice concatenation (fast). Cooperative stop checks included."""
        current = text
        for pidx, (pat_text, repl_text, flags) in enumerate(patterns, start=1):
            if self._should_stop:
                return current
            prefix = f"Pattern {pidx}/{len(patterns)}"
            try:
                pattern = re.compile(pat_text, flags=flags)
            except re.error as e:
                msg = f"Regex compile error for pattern {pidx-1}: {pat_text!r} -> {e}"
                print(msg, file=sys.stderr)
                # emit with total=0 to indicate no measurable progress for this pattern
                self.progress.emit(pidx, 0, 0, "compile error (skipped)")
                continue

            # finditer may itself take time; we still check stop flag between patterns
            try:
                matches = list(pattern.finditer(current))
            except re.error as e:
                print(f"Regex finditer error for pattern {pidx-1}: {e}", file=sys.stderr)
                self.progress.emit(pidx, 0, 0, "finditer error (skipped)")
                continue

            num_matches = len(matches)
            if num_matches == 0:
                self.progress.emit(pidx, 0, 0, "0 matches")
                continue

            pieces: List[str] = []
            last_end = 0
            processed = 0
            # emit initial progress (0/num_matches)
            self.progress.emit(pidx, 0, num_matches, f"processed 0/{num_matches}")
            for m in matches:
                if self._should_stop:
                    # return partial work
                    pieces.append(current[last_end:])
                    # emit final partial progress
                    self.progress.emit(pidx, processed, num_matches, f"stopped {processed}/{num_matches}")
                    return "".join(pieces)
                a, b = m.start(), m.end()
                if last_end < a:
                    pieces.append(current[last_end:a])
                try:
                    replacement_text = m.expand(repl_text)
                except re.error:
                    replacement_text = repl_text
                pieces.append(replacement_text)
                last_end = b
                processed += 1
                # emit progress occasionally and at the end
                if processed % 200 == 0 or processed == num_matches:
                    self.progress.emit(pidx, processed, num_matches, f"processed {processed}/{num_matches}")
                    self._print_console_progress(prefix, processed, num_matches)
            if last_end < len(current):
                pieces.append(current[last_end:])
            current = "".join(pieces)
        return current

    def _build_merged_from_original_and_modified(self, original: str, modified: str) -> Tuple[str, List[Tuple[str, Tuple[int, int], Tuple[int, int]]]]:
        """Build merged text (deleted blocks present) and tag info produced from SequenceMatcher."""
        sm = SequenceMatcher(a=original, b=modified)
        pieces: List[str] = []
        tag_ranges: List[Tuple[str, Tuple[int, int], Tuple[int, int]]] = []
        cur_pos = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                s = original[i1:i2]
                pieces.append(s)
                cur_pos += len(s)
            elif tag == "delete":
                s = original[i1:i2]
                pieces.append(s)
                tag_ranges.append(("del", (cur_pos, cur_pos + len(s)), (-1, -1)))
                cur_pos += len(s)
            elif tag == "insert":
                s = modified[j1:j2]
                pieces.append(s)
                tag_ranges.append(("ins", (cur_pos, cur_pos + len(s)), (j1, j2)))
                cur_pos += len(s)
            elif tag == "replace":
                sdel = original[i1:i2]
                sins = modified[j1:j2]
                pieces.append(sdel)
                tag_ranges.append(("del", (cur_pos, cur_pos + len(sdel)), (-1, -1)))
                cur_pos += len(sdel)
                pieces.append(sins)
                tag_ranges.append(("ins", (cur_pos, cur_pos + len(sins)), (j1, j2)))
                cur_pos += len(sins)
        merged = "".join(pieces)
        return merged, tag_ranges

    def _compute_intraline_spans(self, original: str, modified: str, tag_ranges: List[Tuple[str, Tuple[int, int], Tuple[int, int]]]) -> Tuple[List[Tuple[int, int]], List[Tuple[int, int]]]:
        """
        Fast, guarded intraline computation.
        - If changed-line-count > MAX_INTRALINE_LINES, fall back to line-level highlighting.
        - If an individual changed line length > MAX_LINE_LEN_FOR_INTRALINE, skip intraline for that line.
        """
        orig_lines = original.splitlines(keepends=True)
        mod_lines = modified.splitlines(keepends=True)

        # build line offset arrays
        orig_offsets: List[int] = []
        acc = 0
        for l in orig_lines:
            orig_offsets.append(acc)
            acc += len(l)
        mod_offsets: List[int] = []
        acc = 0
        for l in mod_lines:
            mod_offsets.append(acc)
            acc += len(l)

        # collect changed-line pairs and coarse spans
        sm = SequenceMatcher(a=original, b=modified)
        total_changed_lines = 0
        changed_line_pairs: List[Tuple[int, int]] = []
        ins_spans: List[Tuple[int, int]] = []

        # helper to map char offset to line index
        def char_offset_to_line(offset: int, line_offsets: List[int], lines: List[str]) -> int:
            for i in range(len(line_offsets) - 1, -1, -1):
                if line_offsets[i] <= offset:
                    return i
            return 0

        for tag, a1, a2, b1, b2 in sm.get_opcodes():
            if tag == "equal":
                continue
            old_block = original[a1:a2]
            new_block = modified[b1:b2]
            old_lines = old_block.splitlines(keepends=True) or []
            new_lines = new_block.splitlines(keepends=True) or []
            total_changed_lines += max(len(old_lines), len(new_lines))
            if tag == "insert":
                ins_spans.append((b1, b2))
            elif tag == "replace":
                max_lines = max(len(old_lines), len(new_lines))
                old_cum = a1
                new_cum = b1
                for i in range(max_lines):
                    ol = old_lines[i] if i < len(old_lines) else ""
                    nl = new_lines[i] if i < len(new_lines) else ""
                    if ol or nl:
                        orig_idx = char_offset_to_line(old_cum, orig_offsets, orig_lines) if ol else None
                        mod_idx = char_offset_to_line(new_cum, mod_offsets, mod_lines) if nl else None
                        if orig_idx is not None and mod_idx is not None:
                            changed_line_pairs.append((orig_idx, mod_idx))
                        else:
                            if nl:
                                ins_spans.append((new_cum, new_cum + len(nl)))
                    old_cum += len(ol)
                    new_cum += len(nl)

        # fallback heuristics
        del_spans: List[Tuple[int, int]] = []
        if total_changed_lines > MAX_INTRALINE_LINES or self._should_stop:
            for t, (mstart, mend), _ in tag_ranges:
                if t == "del":
                    del_spans.append((mstart, mend))
            return merge_ranges(del_spans), merge_ranges(ins_spans)

        # otherwise compute intraline diffs per changed line pair
        for orig_idx, mod_idx in changed_line_pairs:
            # if user asked to stop during intraline work, abort early
            if self._should_stop:
                break
            ol = orig_lines[orig_idx] if orig_idx < len(orig_lines) else ""
            nl = mod_lines[mod_idx] if mod_idx < len(mod_lines) else ""
            if len(ol) > MAX_LINE_LEN_FOR_INTRALINE or len(nl) > MAX_LINE_LEN_FOR_INTRALINE:
                base_new = mod_offsets[mod_idx] if mod_idx < len(mod_offsets) else 0
                ins_spans.append((base_new, base_new + len(nl)))
                continue
            sm_line = SequenceMatcher(a=ol, b=nl)
            base_old = orig_offsets[orig_idx] if orig_idx < len(orig_offsets) else 0
            base_new = mod_offsets[mod_idx] if mod_idx < len(mod_offsets) else 0
            for ltag, la1, la2, lb1, lb2 in sm_line.get_opcodes():
                if ltag == "equal":
                    continue
                if ltag in ("replace", "delete"):
                    del_spans.append((base_old + la1, base_old + la2))
                if ltag in ("replace", "insert"):
                    ins_spans.append((base_new + lb1, base_new + lb2))

        # include deletions from merged tag_ranges
        for t, (mstart, mend), _ in tag_ranges:
            if t == "del":
                del_spans.append((mstart, mend))

        return merge_ranges(del_spans), merge_ranges(ins_spans)


# ---------------- Helpers ----------------


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


# ---------------- Pattern Editor ----------------


class PatternEditorDialog(QDialog):
    def __init__(self, parent: QWidget, patterns_path: str):
        super().__init__(parent)
        self.setWindowTitle("Edit patterns")
        self.resize(900, 700)
        self.patterns_path = patterns_path

        layout = QVBoxLayout(self)
        self.text_edit = QTextEdit()
        self.text_edit.setFont(QFont("Courier", 10))
        layout.addWidget(self.text_edit)

        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton("Save")
        self.btn_cancel = QPushButton("Cancel")
        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

        self.btn_save.clicked.connect(self.on_save)
        self.btn_cancel.clicked.connect(self.reject)

        try:
            if os.path.exists(self.patterns_path):
                with open(self.patterns_path, "r", encoding="utf-8") as fh:
                    content = fh.read()
            else:
                content = ""
        except Exception:
            content = ""
        self.text_edit.setPlainText(content)

    def on_save(self):
        text = self.text_edit.toPlainText()
        try:
            with open(self.patterns_path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except Exception as e:
            QMessageBox.critical(self, "Save error", f"Failed to write pattern file: {e}")
            return
        self.accept()


# ---------------- Main Window ----------------


class MainWindow(QMainWindow):
    def __init__(self, plaintext_path: Optional[str], patterns_path: Optional[str], changes_path: Optional[str], show_console_progress: bool = True):
        super().__init__()
        self.plaintext_path = plaintext_path
        self.patterns_path = patterns_path or DEFAULT_PATTERN_FILE
        self.changes_path = changes_path or "changes.jsonl"
        self.show_console_progress = show_console_progress
        self.out_dir = None
        self.original_text = ""
        self.modified_text = ""
        self.patterns: List[Tuple[str, str, int]] = []

        # ensure patterns file exists
        if not os.path.exists(self.patterns_path):
            with open(self.patterns_path, "w", encoding="utf-8") as fh:
                fh.write("# pattern<TAB>replacement<TAB>flags\n")
        self.patterns = parse_patterns(self.patterns_path)

        if self.plaintext_path and os.path.exists(self.plaintext_path):
            with open(self.plaintext_path, "r", encoding="utf-8") as fh:
                self.original_text = fh.read()
            self.out_dir = os.path.dirname(os.path.abspath(self.plaintext_path)) or os.getcwd()
        else:
            self.original_text = ""
            self.out_dir = os.getcwd()

        self.worker_thread: Optional[QThread] = None
        self.worker: Optional[ApplyWorker] = None

        self._build_ui()
        if self.original_text:
            self._start_worker()

    def _build_ui(self):
        self.setWindowTitle("Regex Apply (fast, safe quit, batched UI, per-pattern progress)")
        self.resize(1200, 800)

        menubar = self.menuBar()
        file_menu = menubar.addMenu("&File")
        open_plain_act = QAction("Open plaintext...", self)
        open_plain_act.triggered.connect(self.on_action_open_plain)
        file_menu.addAction(open_plain_act)
        open_patterns_act = QAction("Open patterns...", self)
        open_patterns_act.triggered.connect(self.on_action_open_patterns)
        file_menu.addAction(open_patterns_act)
        file_menu.addSeparator()
        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        toolbar = QToolBar()
        self.addToolBar(toolbar)
        btn_save = QPushButton("Save edits")
        btn_save.clicked.connect(self.on_save_edits)
        toolbar.addWidget(btn_save)
        btn_reapply = QPushButton("Re-apply patterns")
        btn_reapply.clicked.connect(self.on_reapply_patterns)
        toolbar.addWidget(btn_reapply)
        btn_edit_patterns = QPushButton("Edit patterns")
        btn_edit_patterns.clicked.connect(self.on_edit_patterns)
        toolbar.addWidget(btn_edit_patterns)
        btn_open_plain = QPushButton("Open plaintext")
        btn_open_plain.clicked.connect(self.on_action_open_plain)
        toolbar.addWidget(btn_open_plain)

        status_widget = QWidget()
        status_layout = QHBoxLayout(status_widget)
        status_layout.setContentsMargins(4, 4, 4, 4)
        self.status_label = QLabel("Ready.")
        status_layout.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        status_layout.addWidget(self.progress_bar)

        splitter = QSplitter(Qt.Horizontal)
        self.diff_edit = QTextEdit()
        self.diff_edit.setReadOnly(True)
        self.diff_edit.setFont(QFont("Courier", 10))
        splitter.addWidget(self.diff_edit)
        self.editor = QTextEdit()
        self.editor.setFont(QFont("Courier", 10))
        splitter.addWidget(self.editor)
        splitter.setSizes([600, 600])

        central = QWidget()
        vlay = QVBoxLayout(central)
        vlay.addWidget(status_widget)
        vlay.addWidget(splitter)
        self.setCentralWidget(central)

        self.ins_format = QTextCharFormat()
        self.ins_format.setBackground(QColor("#fff07a"))
        self.del_format = QTextCharFormat()
        self.del_format.setBackground(QColor("#ffd7d7"))
        self.del_format.setFontStrikeOut(True)

        if self.original_text:
            self.editor.setPlainText(self.original_text)
            self.diff_edit.setPlainText("Applying patterns...")
        else:
            self.editor.setPlainText("")
            self.diff_edit.setPlainText("Open a plaintext file (File -> Open plaintext...) to begin.")

    # worker control
    def _start_worker(self):
        if self.worker_thread and self.worker_thread.isRunning():
            return
        self.patterns = parse_patterns(self.patterns_path)
        self.worker_thread = QThread()
        self.worker = ApplyWorker(original_text=self.original_text, patterns=self.patterns, plaintext_path=self.plaintext_path, patterns_path=self.patterns_path, out_dir=self.out_dir, round_id=1, show_console_progress=self.show_console_progress)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_worker_progress)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.error.connect(self._on_worker_error)
        self.worker.finished.connect(lambda _: self.worker_thread.quit())
        self.worker.finished.connect(lambda _: self.worker_thread.wait())
        self.worker.error.connect(lambda _: self.worker_thread.quit())
        self.worker_thread.start()
        self.status_label.setText("Worker started...")

    def stop_worker(self, timeout_ms: int = 5000) -> bool:
        """
        Request the background worker to stop, wait up to timeout_ms milliseconds.
        Returns True if the worker/thread exited, False otherwise.
        """
        if not self.worker_thread:
            return True
        if not self.worker_thread.isRunning():
            return True
        # request cooperative stop
        try:
            if self.worker:
                self.worker.stop()
        except Exception:
            pass
        # ask thread event loop to quit (safe) and wait
        self.worker_thread.quit()
        finished = self.worker_thread.wait(timeout_ms)
        return finished

    def closeEvent(self, event):
        # Ask worker to stop and wait briefly
        ok = self.stop_worker(timeout_ms=5000)
        if not ok:
            res = QMessageBox.question(self, "Force quit?",
                                       "Background worker is still running. Force quit? This may leave work incomplete.",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if res == QMessageBox.Yes:
                event.accept()
            else:
                event.ignore()
                return
        event.accept()

    @Slot(int, int, int, str)
    def _on_worker_progress(self, pattern_index: int, processed: int, total: int, msg: str):
        """Update status label and show per-pattern progress in the progress bar."""
        # show message
        self.status_label.setText(f"Applying pattern {pattern_index}: {msg}")
        if total > 0:
            # per-pattern accuracy
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(processed)
        else:
            # fallback animated busy bar
            self.progress_bar.setMaximum(100)
            self.progress_bar.setValue((self.progress_bar.value() + 7) % 100)

    @Slot(object)
    def _on_worker_finished(self, result_obj: ApplyResult):
        res: ApplyResult = result_obj
        self.status_label.setText("Finished applying patterns.")
        self.progress_bar.setValue(100)
        # populate merged diff (deletions)
        self._populate_merged_diff(res.merged_text, res.del_spans)
        # populate editor with insertions
        self._populate_editor_with_insertions(res.modified_text, res.ins_spans)
        self.modified_text = res.modified_text
        QMessageBox.information(self, "Done", "Patterns applied and views updated.")

    @Slot(str)
    def _on_worker_error(self, text: str):
        QMessageBox.critical(self, "Worker error", f"Error during processing:\n\n{text}")

    # ---- BATCHED view population (fast) ----
    def _populate_merged_diff(self, merged_text: str, del_spans: List[Tuple[int, int]]):
        """
        Populate merged diff view (left). Batch updates to avoid long blocking.
        """
        MAX_TAG_OPS = 2000

        self.diff_edit.setReadOnly(False)
        self.diff_edit.setUpdatesEnabled(False)     # prevent repaints while we edit
        self.diff_edit.clear()
        self.diff_edit.setPlainText(merged_text)

        # Merge and possibly reduce the number of ranges
        merged = merge_ranges(del_spans)

        # If there are too many tag ops, fall back to coarse highlighting:
        if len(merged) > MAX_TAG_OPS:
            cursor = self.diff_edit.textCursor()
            cursor.beginEditBlock()
            try:
                preview_len = min(len(merged_text), 10000)
                cursor.setPosition(0)
                cursor.setPosition(preview_len, QTextCursor.KeepAnchor)  # type: ignore[attr-defined]
                cursor.setCharFormat(self.del_format)
            finally:
                cursor.endEditBlock()
            self.diff_edit.setUpdatesEnabled(True)
            QApplication.processEvents()
            self.diff_edit.setReadOnly(True)
            return

        # Normal path: apply each merged deletion span in a single edit block
        cursor = self.diff_edit.textCursor()
        cursor.beginEditBlock()
        try:
            for s, e in merged:
                cursor.setPosition(s)
                cursor.setPosition(e, QTextCursor.KeepAnchor)  # type: ignore[attr-defined]
                cursor.setCharFormat(self.del_format)
        finally:
            cursor.endEditBlock()

        self.diff_edit.setUpdatesEnabled(True)
        QApplication.processEvents()
        self.diff_edit.setReadOnly(True)

    def _populate_editor_with_insertions(self, modified_text: str, ins_spans: List[Tuple[int, int]]):
        """
        Populate the editable right pane with modified_text and apply insertion highlights in a batched way.
        """
        MAX_TAG_OPS = 2000

        self.editor.setUpdatesEnabled(False)
        self.editor.clear()
        self.editor.setPlainText(modified_text)

        merged_ins = merge_ranges(ins_spans)

        if len(merged_ins) > MAX_TAG_OPS:
            cursor = self.editor.textCursor()
            cursor.beginEditBlock()
            try:
                preview_len = min(len(modified_text), 10000)
                cursor.setPosition(0)
                cursor.setPosition(preview_len, QTextCursor.KeepAnchor)  # type: ignore[attr-defined]
                cursor.setCharFormat(self.ins_format)
            finally:
                cursor.endEditBlock()
            self.editor.setUpdatesEnabled(True)
            QApplication.processEvents()
            return

        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        try:
            for s, e in merged_ins:
                cursor.setPosition(s)
                cursor.setPosition(e, QTextCursor.KeepAnchor)  # type: ignore[attr-defined]
                cursor.setCharFormat(self.ins_format)
        finally:
            cursor.endEditBlock()

        self.editor.setUpdatesEnabled(True)
        QApplication.processEvents()

    # actions
    def on_save_edits(self):
        edited = self.editor.toPlainText()
        sm = SequenceMatcher(a=self.modified_text or self.original_text, b=edited)
        any_changes = False
        for tag, a1, a2, b1, b2 in sm.get_opcodes():
            if tag == "equal":
                continue
            any_changes = True
            ts = now_iso_z()
            if tag == "delete":
                rec = {
                    "timestamp": ts,
                    "round": None,
                    "pattern_idx": None,
                    "type": "delete",
                    "pos": a1,
                    "length": a2 - a1,
                    "text": (self.modified_text or self.original_text)[a1:a2],
                }
                try:
                    emit_change_record(self.changes_path, rec)
                except Exception:
                    pass
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
                try:
                    emit_change_record(self.changes_path, rec)
                except Exception:
                    pass
            elif tag == "replace":
                rec_del = {
                    "timestamp": ts,
                    "round": None,
                    "pattern_idx": None,
                    "type": "delete",
                    "pos": a1,
                    "length": a2 - a1,
                    "text": (self.modified_text or self.original_text)[a1:a2],
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
                try:
                    emit_change_record(self.changes_path, rec_del)
                    emit_change_record(self.changes_path, rec_ins)
                except Exception:
                    pass
        if not any_changes:
            QMessageBox.information(self, "Save edits", "No changes detected.")
            return
        try:
            backup = backup_file(self.plaintext_path or "plaintext.txt")
            with open(self.plaintext_path, "w", encoding="utf-8") as fh:
                fh.write(edited)
        except Exception as e:
            QMessageBox.critical(self, "Save error", f"Failed to write plaintext file: {e}")
            return
        self.modified_text = edited
        QMessageBox.information(self, "Saved", f"Saved edits. Backup created: {backup}")

    def on_reapply_patterns(self):
        if not self.plaintext_path:
            QMessageBox.information(self, "No plaintext", "Open a plaintext file first.")
            return
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.warning(self, "Busy", "Worker is already running.")
            return
        with open(self.plaintext_path, "r", encoding="utf-8") as fh:
            self.original_text = fh.read()
        self.patterns = parse_patterns(self.patterns_path)
        self.out_dir = os.path.dirname(os.path.abspath(self.plaintext_path)) or os.getcwd()
        self.editor.setPlainText(self.original_text)
        self.diff_edit.setPlainText("Re-applying patterns...")
        self._start_worker()

    def on_edit_patterns(self):
        dlg = PatternEditorDialog(self, self.patterns_path)
        if dlg.exec() == QDialog.Accepted:
            try:
                self.patterns = parse_patterns(self.patterns_path)
            except Exception as e:
                QMessageBox.critical(self, "Pattern load error", f"Failed to reload pattern file after save: {e}")
                return
            if self.plaintext_path:
                self.on_reapply_patterns()

    def on_action_open_plain(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open plaintext file", filter="Text files (*.txt);;All files (*)")
        if not path:
            return
        self.plaintext_path = path
        try:
            with open(path, "r", encoding="utf-8") as fh:
                self.original_text = fh.read()
        except Exception as e:
            QMessageBox.critical(self, "Open error", f"Failed to open plaintext file: {e}")
            return
        self.out_dir = os.path.dirname(os.path.abspath(path)) or os.getcwd()
        self.editor.setPlainText(self.original_text)
        self.diff_edit.setPlainText("Applying patterns to opened file...")
        self.patterns = parse_patterns(self.patterns_path)
        self._start_worker()

    def on_action_open_patterns(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open patterns file", filter="Text files (*.txt);;All files (*)")
        if not path:
            return
        self.patterns_path = path
        if not os.path.exists(self.patterns_path):
            with open(self.patterns_path, "w", encoding="utf-8") as fh:
                fh.write("# pattern<TAB>replacement<TAB>flags\n")
        self.patterns = parse_patterns(self.patterns_path)
        QMessageBox.information(self, "Patterns loaded", f"Loaded patterns from {self.patterns_path}")


# ---------------- Entrypoint ----------------


def main():
    parser = argparse.ArgumentParser(description="Regex-apply GUI (PySide6) — fast defaults with safe shutdown, batched UI updates, and per-pattern progress.")
    parser.add_argument("plaintext", nargs="?", help="Optional plaintext file")
    parser.add_argument("patterns", nargs="?", help="Optional patterns file")
    parser.add_argument("changes", nargs="?", help="Optional changes file")
    parser.add_argument("--no-progress", action="store_true", help="Disable console progress")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    plaintext = args.plaintext if args.plaintext else None
    patterns = args.patterns if args.patterns else DEFAULT_PATTERN_FILE
    changes = args.changes if args.changes else "changes.jsonl"
    win = MainWindow(plaintext, patterns, changes, show_console_progress=not args.no_progress)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()