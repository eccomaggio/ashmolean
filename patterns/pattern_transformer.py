#!/usr/bin/env python3
"""
pattern_transformer_whitespace_fixed.py

Fixes:
1) Ensures the td { font-size: 10pt; } CSS is injected only into the generated diff.html.
2) Whitespace toggle now affects both the patterns editor and the original text view.
3) Editor monospace font size increased to 12pt so text is readable.

Requirements:
- PySide6
- PySide6-QtWebEngine
"""

import os
import sys
import re
import difflib
from pathlib import Path

# Optionally silence Chromium GPU logs; keep commented unless needed.
# os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = '--disable-gpu --disable-software-rasterizer --disable-gpu-compositing'

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QVBoxLayout, QHBoxLayout,
    QPlainTextEdit, QFileDialog, QMessageBox, QLabel, QPushButton, QListWidget, QListWidgetItem, QCheckBox
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView

from PySide6.QtGui import (
    QSyntaxHighlighter, QTextCharFormat, QColor, QFont, QTextCursor, QFontDatabase
)


# -------------------------
# Flag parsing helpers
# -------------------------
FLAG_ALIASES = {
    "IGNORECASE": re.IGNORECASE,
    "MULTILINE": re.MULTILINE,
    "DOTALL": re.DOTALL,
    "VERBOSE": re.VERBOSE,
    "ASCII": re.ASCII,
    "I": re.IGNORECASE,
    "M": re.MULTILINE,
    "S": re.DOTALL,
    "X": re.VERBOSE,
    "A": re.ASCII,
    "RE.IGNORECASE": re.IGNORECASE,
    "RE.I": re.IGNORECASE,
    "RE.MULTILINE": re.MULTILINE,
    "RE.M": re.MULTILINE,
    "RE.DOTALL": re.DOTALL,
    "RE.S": re.DOTALL,
    "RE.VERBOSE": re.VERBOSE,
    "RE.X": re.VERBOSE,
    "RE.ASCII": re.ASCII,
    "RE.A": re.ASCII,
}

FLAG_DISPLAY = [
    ("IGNORECASE", re.IGNORECASE),
    ("MULTILINE", re.MULTILINE),
    ("DOTALL", re.DOTALL),
    ("VERBOSE", re.VERBOSE),
    ("ASCII", re.ASCII),
]


def parse_flag_tokens(tok_text: str) -> int:
    if not tok_text:
        return 0
    combined = 0
    toks = re.split(r"[,\|;\s]+", tok_text.strip())
    for t in toks:
        if not t:
            continue
        key = t.strip().upper()
        if key in FLAG_ALIASES:
            combined |= FLAG_ALIASES[key]
        else:
            if len(key) == 1 and key in FLAG_ALIASES:
                combined |= FLAG_ALIASES[key]
    return combined


def flags_to_tokens(flags_int: int) -> list:
    tokens = []
    for name, val in FLAG_DISPLAY:
        if val != re.MULTILINE and (flags_int & val):
            tokens.append(name)
    return tokens


def parse_pattern_line(line: str):
    parts = line.split("##", 1)
    rule_part = parts[0].strip()
    flags_part = parts[1].strip() if len(parts) > 1 else ""

    flags_text = ""
    if flags_part:
        m = re.search(r"flags\s*:\s*(.*)$", flags_part, flags=re.IGNORECASE)
        if m:
            flags_text = m.group(1).strip()
        else:
            flags_text = flags_part

    if "->" in rule_part:
        left, right = rule_part.split("->", 1)
        pattern = left.strip()
        replacement = right.strip()
    else:
        pattern = rule_part
        replacement = ""

    flags = parse_flag_tokens(flags_text)
    flags |= re.MULTILINE
    return pattern, replacement, flags


# -------------------------
# Whitespace highlighter
# -------------------------
class WhitespaceHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.space_format = QTextCharFormat()
        self.space_format.setBackground(QColor(230, 230, 230))

        self.tab_format = QTextCharFormat()
        self.tab_format.setBackground(QColor(210, 230, 255))

        self.trailing_format = QTextCharFormat()
        self.trailing_format.setBackground(QColor(255, 220, 220))

    def highlightBlock(self, text: str):
        # tabs
        for m in re.finditer(r"\t", text):
            start = m.start()
            self.setFormat(start, 1, self.tab_format)
        # spaces
        for m in re.finditer(r" ", text):
            start = m.start()
            self.setFormat(start, 1, self.space_format)
        # trailing spaces and tabs
        m = re.search(r"[ \t]+$", text)
        if m:
            start = m.start()
            length = len(text) - start
            self.setFormat(start, length, self.trailing_format)


# -------------------------
# Main Window
# -------------------------
class PatternTransformerMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Apply regex patterns")
        self.resize(1200, 780)

        # Paths
        self.patterns_path: Path | None = None
        self.text_path: Path | None = None
        self.diff_path: Path | None = None
        self.transform_path: Path | None = None

        self._last_saved_patterns_text = ""

        # Two highlighter instances (one per editor) so toggle can attach to both documents
        self.whitespace_highlighter_patterns = WhitespaceHighlighter()
        self.whitespace_highlighter_text = WhitespaceHighlighter()
        self._whitespace_enabled = False

        central = QWidget()
        self.setCentralWidget(central)
        main_vlayout = QVBoxLayout()
        main_vlayout.setContentsMargins(6, 6, 6, 6)
        main_vlayout.setSpacing(6)
        central.setLayout(main_vlayout)

        # Top button bar
        button_bar = QWidget()
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        button_bar.setLayout(button_layout)

        self.btn_select_text = QPushButton("Select text file")
        self.btn_select_patterns = QPushButton("Select pattern file")
        self.btn_run = QPushButton("Run patterns")
        self.btn_save_patterns = QPushButton("Save patterns")
        self.btn_help = QPushButton("Help")
        self.chk_show_ws = QCheckBox("Show whitespace")

        button_layout.addWidget(self.btn_select_text)
        button_layout.addWidget(self.btn_select_patterns)
        button_layout.addWidget(self.btn_run)
        button_layout.addWidget(self.btn_save_patterns)
        button_layout.addWidget(self.btn_help)
        button_layout.addWidget(self.chk_show_ws)
        button_layout.addStretch(1)

        main_vlayout.addWidget(button_bar)

        # Connect buttons
        self.btn_select_text.clicked.connect(self.select_text_file)
        self.btn_select_patterns.clicked.connect(self.select_pattern_file)
        self.btn_run.clicked.connect(self.run_patterns)
        self.btn_save_patterns.clicked.connect(self.save_patterns)
        self.btn_help.clicked.connect(self.show_help_dialog)
        self.chk_show_ws.toggled.connect(self.on_toggle_whitespace)

        # Grid layout
        grid = QGridLayout()
        grid.setSpacing(8)

        # Monospace font for editors, set to 12pt for readability
        monospace = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        monospace.setPointSize(12)

        # Top-left: patterns editor
        self.patterns_label = QLabel("Patterns: (no file loaded)")
        self.patterns_edit = QPlainTextEdit()
        self.patterns_edit.setPlaceholderText(
            "Load a patterns file with 'Select pattern file'...\n\n"
            "Each non-empty line is a rule. Format:\n"
            "pattern -> replacement ## flags: IGNORECASE, DOTALL\n"
            "Lines starting with # are comments."
        )
        self.patterns_edit.setFont(monospace)

        left_top_layout = QVBoxLayout()
        left_top_layout.addWidget(self.patterns_label)
        left_top_layout.addWidget(self.patterns_edit)
        left_top_widget = QWidget()
        left_top_widget.setLayout(left_top_layout)
        grid.addWidget(left_top_widget, 0, 0)

        # Top-right: parsed rules list
        self.rules_label = QLabel("Parsed rules (click to jump to rule in editor):")
        self.rules_list = QListWidget()
        right_top_layout = QVBoxLayout()
        right_top_layout.addWidget(self.rules_label)
        right_top_layout.addWidget(self.rules_list)
        right_top_widget = QWidget()
        right_top_widget.setLayout(right_top_layout)
        grid.addWidget(right_top_widget, 0, 1)

        # Bottom-left: original text view
        self.text_label = QLabel("Text file: (no file loaded)")
        self.text_view = QPlainTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setPlaceholderText("Select a text file with 'Select text file'...")
        self.text_view.setFont(monospace)

        bottom_left_layout = QVBoxLayout()
        bottom_left_layout.addWidget(self.text_label)
        bottom_left_layout.addWidget(self.text_view)
        bottom_left_widget = QWidget()
        bottom_left_widget.setLayout(bottom_left_layout)
        grid.addWidget(bottom_left_widget, 1, 0)

        # Bottom-right: diff web view
        self.diff_label = QLabel("Diff file: (not generated yet)")
        self.web_view = QWebEngineView()
        initial_html = "<html><body><i>Run patterns to generate diff.html and load it here (full web engine).</i></body></html>"
        self.web_view.setHtml(initial_html)
        bottom_right_layout = QVBoxLayout()
        bottom_right_layout.addWidget(self.diff_label)
        bottom_right_layout.addWidget(self.web_view)
        bottom_right_widget = QWidget()
        bottom_right_widget.setLayout(bottom_right_layout)
        grid.addWidget(bottom_right_widget, 1, 1)

        # layout stretch
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)

        main_vlayout.addLayout(grid)

        self.statusBar().showMessage("Ready")

        self._rules_line_map = []
        self.patterns_edit.textChanged.connect(self.update_rules_list)
        self.rules_list.itemClicked.connect(self.on_rule_clicked)
        self.update_rules_list()

    # -------------------------
    # helpers
    # -------------------------
    def _patterns_modified(self) -> bool:
        return self.patterns_edit.toPlainText() != self._last_saved_patterns_text

    def update_rules_list(self):
        self.rules_list.clear()
        self._rules_line_map = []
        text = self.patterns_edit.toPlainText()
        lines = text.splitlines()
        for idx, ln in enumerate(lines):
            stripped = ln.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                pat, repl, flags = parse_pattern_line(ln)
                flag_tokens = flags_to_tokens(flags)
                flags_display = (", ".join(flag_tokens)) if flag_tokens else ""
                display = f"{pat}  ->  {repl}"
                if flags_display:
                    display += f"   [{flags_display}]"
                item = QListWidgetItem(display)
                item.setData(Qt.UserRole, idx)
                self.rules_list.addItem(item)
                self._rules_line_map.append(idx)
            except Exception:
                item = QListWidgetItem(f"(parse error) {ln}")
                item.setData(Qt.UserRole, idx)
                self.rules_list.addItem(item)
                self._rules_line_map.append(idx)

    def on_rule_clicked(self, item: QListWidgetItem):
        line_no = item.data(Qt.UserRole)
        if line_no is None:
            return
        lines = self.patterns_edit.toPlainText().splitlines(True)
        pos = 0
        for i in range(min(line_no, len(lines))):
            pos += len(lines[i])
        cursor = self.patterns_edit.textCursor()
        cursor.setPosition(pos)
        if line_no < len(lines):
            end_pos = pos + len(lines[line_no])
            cursor.setPosition(end_pos, QTextCursor.KeepAnchor)
        else:
            cursor.setPosition(len(self.patterns_edit.toPlainText()))
        self.patterns_edit.setTextCursor(cursor)
        self.patterns_edit.setFocus()

    # -------------------------
    # Button actions
    # -------------------------
    def select_pattern_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select patterns file", ".", "Text files (*.txt);;All files (*)")
        if not path:
            return
        self.patterns_path = Path(path)
        try:
            text = self.patterns_path.read_text(encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to read patterns file:\n{e}")
            return
        self.patterns_edit.setPlainText(text)
        self._last_saved_patterns_text = text
        self.statusBar().showMessage(f"Loaded patterns: {self.patterns_path}")
        self.update_rules_list()

    def select_text_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select text file", ".", "Text files (*.txt);;All files (*)")
        if not path:
            return
        self.text_path = Path(path)
        try:
            text = self.text_path.read_text(encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to read text file:\n{e}")
            return
        self.text_view.setPlainText(text)
        self.statusBar().showMessage(f"Loaded text file: {self.text_path}")

    def save_patterns(self):
        content = self.patterns_edit.toPlainText()
        if not self.patterns_path:
            path, _ = QFileDialog.getSaveFileName(self, "Save patterns as", ".", "Text files (*.txt);;All files (*)")
            if not path:
                return
            self.patterns_path = Path(path)
        try:
            self.patterns_path.write_text(content, encoding="utf-8")
            self._last_saved_patterns_text = content
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save patterns file:\n{e}")
            return
        self.statusBar().showMessage(f"Saved patterns to: {self.patterns_path}")
        QMessageBox.information(self, "Saved", f"Patterns saved to:\n{self.patterns_path}")

    def run_patterns(self):
        if not self.text_path:
            QMessageBox.warning(self, "No text file", "Please select a text file first (button: Select text file).")
            return
        if not self.patterns_edit.toPlainText().strip():
            QMessageBox.warning(self, "No patterns", "No patterns are loaded or the patterns file is empty.")
            return

        original_text = self.text_view.toPlainText() or ""
        patterns_raw = self.patterns_edit.toPlainText().splitlines()
        rules = []
        for ln in patterns_raw:
            ln_stripped = ln.strip()
            if not ln_stripped or ln_stripped.startswith("#"):
                continue
            try:
                pat, repl, flags = parse_pattern_line(ln)
                rules.append((pat, repl, flags))
            except Exception as e:
                QMessageBox.warning(self, "Pattern parse error", f"Failed to parse line:\n{ln}\n\n{e}")
                continue

        transformed = original_text
        try:
            for pat, repl, flags in rules:
                transformed = re.sub(pat, repl, transformed, flags=flags)
        except re.error as e:
            QMessageBox.critical(self, "Regex error", f"Error while applying regex patterns:\n{e}")
            return
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Unexpected error while transforming text:\n{e}")
            return

        base_dir = self.text_path.parent if self.text_path and self.text_path.parent else Path.cwd()
        self.transform_path = base_dir / "transform.txt"
        self.diff_path = base_dir / "diff.html"

        try:
            self.transform_path.write_text(transformed, encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to write transform.txt:\n{e}")
            return

        try:
            orig_lines = original_text.splitlines()
            new_lines = transformed.splitlines()
            hd = difflib.HtmlDiff(tabsize=4, wrapcolumn=80)
            html = hd.make_file(orig_lines, new_lines,
                                fromdesc=str(self.text_path) if self.text_path else "original",
                                todesc=str(self.transform_path))

            # inject CSS before </style> (HtmlDiff always provides a <style>)
            css_injection = """
    td { font-size: 10pt; }
    .diff_header {padding-right: 1rem;}
    /* hide header and left-most columns (the "original" side) */
    .diff tr > th,
    .diff th:nth-child(1),
    .diff th:nth-child(2),
    .diff th:nth-child(3),
    .diff td:nth-child(1),
    .diff td:nth-child(2),
    .diff td:nth-child(3) {
        display: none !important;
    }
    table.diff { width: 100% !important; }
"""
            html = html.replace("</style>", css_injection + "</style>", 1)
            self.diff_path.write_text(html, encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to generate diff.html:\n{e}")
            return

        try:
            url = QUrl.fromLocalFile(str(self.diff_path.resolve()))
            self.web_view.load(url)
        except Exception as e:
            QMessageBox.warning(self, "Warning", f"Diff generated but failed to load into web view:\n{e}")

        self.statusBar().showMessage(f"Patterns applied. transform.txt and diff.html saved to: {self.diff_path.parent}")
        QMessageBox.information(self, "Done", f"Transformed text saved to:\n{self.transform_path}\nDiff saved to:\n{self.diff_path}")
        self.update_rules_list()

    # -------------------------
    # Help dialog
    # -------------------------
    def show_help_dialog(self):
        help_text = (
            "<b>Patterns file syntax</b><br><br>"
            "Each non-empty, non-comment line is a rule. Lines starting with <code>#</code> are ignored.<br><br>"
            "<b>Format (recommended):</b><br>"
            "<code>pattern -> replacement ## flags: FLAG1,FLAG2</code><br><br>"
            "<b>Examples</b>:<br>"
            "<code>foo(\\d+) -> bar\\1</code> &mdash; replace foo123 → bar123<br>"
            "<code>TODO:.* ->  ## flags: IGNORECASE</code> &mdash; remove TODO lines, case-insensitive<br>"
            "<code>^start -> BEGIN ## flags: i,s</code> &mdash; short flags allowed (i = IGNORECASE, s = DOTALL)<br><br>"
            "<b>Notes on separators & whitespace</b>:<br>"
            "- Use <code>-></code> (space optional) to separate the search pattern and the replacement. If omitted, replacement is empty.<br>"
            "- Use <code>##</code> to start the flags portion (optional). After <code>##</code> you can write <code>flags: ...</code> or just list flags.<br>"
            "- Flags can be separated by commas, spaces, pipes, or semicolons (e.g. <code>i,m</code> or <code>IGNORECASE MULTILINE</code>).<br>"
            "- Flag names accept: short (i, m, s, x, a), long (IGNORECASE, MULTILINE, DOTALL, VERBOSE, ASCII), or Python-style (re.I).<br><br>"
            "<b>Behavior</b>: Rules are applied in order to the entire text using <code>re.sub()</code>. MULTILINE is enabled by default."
        )
        QMessageBox.information(self, "Patterns file help", help_text)

    # -------------------------
    # Whitespace toggle (APPLIES TO BOTH EDITORS)
    # -------------------------
    def on_toggle_whitespace(self, checked: bool):
        self._whitespace_enabled = bool(checked)
        if self._whitespace_enabled:
            # attach to both documents
            self.whitespace_highlighter_patterns.setDocument(self.patterns_edit.document())
            self.whitespace_highlighter_text.setDocument(self.text_view.document())
        else:
            # detach both
            self.whitespace_highlighter_patterns.setDocument(None)
            self.whitespace_highlighter_text.setDocument(None)

    # -------------------------
    # Autosave on close
    # -------------------------
    def closeEvent(self, event):
        try:
            if self._patterns_modified():
                if not self.patterns_path:
                    autosave_path = Path.cwd() / "patterns.txt"
                    self.patterns_path = autosave_path
                content = self.patterns_edit.toPlainText()
                self.patterns_path.write_text(content, encoding="utf-8")
                self._last_saved_patterns_text = content
                self.statusBar().showMessage(f"Autosaved patterns to: {self.patterns_path}")
        except Exception as e:
            QMessageBox.warning(self, "Autosave failed", f"Failed to autosave patterns:\n{e}")
        event.accept()


# -------------------------
# Run application
# -------------------------
def main():
    app = QApplication(sys.argv)
    w = PatternTransformerMainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
