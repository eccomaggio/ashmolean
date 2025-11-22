#!/usr/bin/env python3
"""
pattern_transformer_rules_list.py

Pattern Transformer with:
- Buttons at the top: Select text file, Select pattern file, Run patterns, Save patterns, Help
- Clickable parsed-rules list (shows pattern -> replacement [flags]) that jumps to the rule in the editor
- Visible file path labels
- Autosave patterns on close
- Regex flags support via extended syntax
- Full web engine diff viewer (QtWebEngineView)

Requirements:
- PySide6
- PySide6-QtWebEngine
    pip install PySide6 PySide6-QtWebEngine
"""

import sys
import re
import difflib
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QVBoxLayout, QHBoxLayout,
    QPlainTextEdit, QFileDialog, QMessageBox, QLabel, QPushButton, QListWidget, QListWidgetItem
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtGui import QTextCursor

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

# Reverse mapping for flag display (prefer long names)
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
            # single-letter fallback
            if len(key) == 1 and key in FLAG_ALIASES:
                combined |= FLAG_ALIASES[key]
    return combined


def flags_to_tokens(flags_int: int) -> list:
    """Return a list of human-friendly flag names present in flags_int (excluding MULTILINE as it's default)."""
    tokens = []
    for name, val in FLAG_DISPLAY:
        if val != re.MULTILINE and (flags_int & val):
            tokens.append(name)
    # include MULTILINE only if user explicitly included it (we add it by default later)
    return tokens


def parse_pattern_line(line: str):
    """
    Parse a single patterns-file line.
    Syntax:
      pattern -> replacement ## flags: FLAG1,FLAG2
    Returns (pattern, replacement, flags_int)
    """
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
    # Always include MULTILINE by default for compatibility
    flags |= re.MULTILINE
    return pattern, replacement, flags


# -------------------------
# Main Window
# -------------------------
class PatternTransformerMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pattern Transformer — Rules List")
        self.resize(1200, 780)

        # Paths
        self.patterns_path: Path | None = None
        self.text_path: Path | None = None
        self.diff_path: Path | None = None
        self.transform_path: Path | None = None

        # Track last saved content for autosave detection
        self._last_saved_patterns_text = ""

        # Central widget and main vertical layout
        central = QWidget()
        self.setCentralWidget(central)
        main_vlayout = QVBoxLayout()
        main_vlayout.setContentsMargins(6, 6, 6, 6)
        main_vlayout.setSpacing(6)
        central.setLayout(main_vlayout)

        # Top button bar (buttons inside window)
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

        button_layout.addWidget(self.btn_select_text)
        button_layout.addWidget(self.btn_select_patterns)
        button_layout.addWidget(self.btn_run)
        button_layout.addWidget(self.btn_save_patterns)
        button_layout.addWidget(self.btn_help)
        button_layout.addStretch(1)  # push buttons to left

        main_vlayout.addWidget(button_bar)

        # Connect buttons
        self.btn_select_text.clicked.connect(self.select_text_file)
        self.btn_select_patterns.clicked.connect(self.select_pattern_file)
        self.btn_run.clicked.connect(self.run_patterns)
        self.btn_save_patterns.clicked.connect(self.save_patterns)
        self.btn_help.clicked.connect(self.show_help_dialog)

        # Grid layout (2x2). Left column will contain patterns editor and rules list.
        grid = QGridLayout()
        grid.setSpacing(8)

        # Top-left: Patterns area (label + editable box)
        self.patterns_label = QLabel("Patterns: (no file loaded)")
        self.patterns_label.setToolTip("Path to the patterns file")
        self.patterns_edit = QPlainTextEdit()
        self.patterns_edit.setPlaceholderText("Load a patterns file with 'Select pattern file'...\n\n"
                                              "Each non-empty line is a rule. Format:\n"
                                              "pattern -> replacement ## flags: IGNORECASE, DOTALL\n"
                                              "Lines starting with # are comments.")
        left_top_layout = QVBoxLayout()
        left_top_layout.addWidget(self.patterns_label)
        left_top_layout.addWidget(self.patterns_edit)
        left_top_widget = QWidget()
        left_top_widget.setLayout(left_top_layout)
        grid.addWidget(left_top_widget, 0, 0)

        # Top-right: parsed rules list (clickable)
        self.rules_label = QLabel("Parsed rules (click to jump to rule in editor):")
        self.rules_list = QListWidget()
        self.rules_list.setToolTip("Shows how each line is parsed. Click an item to jump to that line in the patterns editor.")
        right_top_layout = QVBoxLayout()
        right_top_layout.addWidget(self.rules_label)
        right_top_layout.addWidget(self.rules_list)
        right_top_widget = QWidget()
        right_top_widget.setLayout(right_top_layout)
        grid.addWidget(right_top_widget, 0, 1)

        # Bottom-left: Text view (readonly) with path label
        self.text_label = QLabel("Text file: (no file loaded)")
        self.text_label.setToolTip("Path to the selected text file (original)")
        self.text_view = QPlainTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setPlaceholderText("Select a text file with 'Select text file'...")
        bottom_left_layout = QVBoxLayout()
        bottom_left_layout.addWidget(self.text_label)
        bottom_left_layout.addWidget(self.text_view)
        bottom_left_widget = QWidget()
        bottom_left_widget.setLayout(bottom_left_layout)
        grid.addWidget(bottom_left_widget, 1, 0)

        # Bottom-right: HTML diff viewer using QWebEngineView
        self.diff_label = QLabel("Diff file: (not generated yet)")
        self.diff_label.setToolTip("Path to the generated diff.html file (opens here)")
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

        # Add the grid to the main vertical layout
        main_vlayout.addLayout(grid)

        # Status bar
        self.statusBar().showMessage("Ready")

        # Internal mapping of rule-list index -> editor line number
        self._rules_line_map = []  # list of ints

        # Connect signals for live update of the parsed rules list
        self.patterns_edit.textChanged.connect(self.update_rules_list)
        self.rules_list.itemClicked.connect(self.on_rule_clicked)

        # Populate initial rules list (empty)
        self.update_rules_list()

    # -------------------------
    # UI helpers
    # -------------------------
    def _update_labels(self):
        self.patterns_label.setText(
            f"Patterns: {str(self.patterns_path) if self.patterns_path else '(no file loaded)'}"
        )
        self.text_label.setText(
            f"Text file: {str(self.text_path) if self.text_path else '(no file loaded)'}"
        )
        self.diff_label.setText(
            f"Diff file: {str(self.diff_path) if self.diff_path else '(not generated yet)'}"
        )
        summary = []
        summary.append(f"Patterns file: {self.patterns_path if self.patterns_path else '(none)'}")
        summary.append(f"Text file: {self.text_path if self.text_path else '(none)'}")
        summary.append(f"Transform file: {self.transform_path if self.transform_path else '(none)'}")
        summary.append(f"Diff file: {self.diff_path if self.diff_path else '(none)'}")
        unsaved = "(modified)" if self._patterns_modified() else "(saved)"
        summary.append(f"Patterns editor status: {unsaved}")
        self.rules_label.setText("Parsed rules (click to jump to rule in editor):")
        self.top_right_label_text = "\n".join(summary)  # not shown elsewhere; kept for parity

    def _patterns_modified(self) -> bool:
        current = self.patterns_edit.toPlainText()
        return current != self._last_saved_patterns_text

    # -------------------------
    # Rules list management
    # -------------------------
    def update_rules_list(self):
        """
        Parse the patterns editor and populate the rules_list.
        Also record each rule's original editor line index in _rules_line_map so clicking can jump.
        """
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
                # build a friendly display string
                flag_tokens = flags_to_tokens(flags)
                flags_display = (", ".join(flag_tokens)) if flag_tokens else ""
                display = f"{pat}  ->  {repl}"
                if flags_display:
                    display += f"   [{flags_display}]"
                item = QListWidgetItem(display)
                # store editor line number in item data
                item.setData(Qt.UserRole, idx)
                self.rules_list.addItem(item)
                self._rules_line_map.append(idx)
            except Exception as e:
                # on parse error, show raw line with an error marker
                item = QListWidgetItem(f"(parse error) {ln}")
                item.setData(Qt.UserRole, idx)
                self.rules_list.addItem(item)
                self._rules_line_map.append(idx)

    def on_rule_clicked(self, item):
        """
        Jump to the corresponding line in the patterns editor and select it.
        This implementation uses explicit character offsets (setPosition)
        rather than movePosition() enums to avoid PySide6 enum-name incompatibilities.
        """
        line_no = item.data(Qt.UserRole)
        if line_no is None:
            return

        # Get the full text lines including newlines so lengths are exact
        lines = self.patterns_edit.toPlainText().splitlines(True)  # keepends=True

        # Compute start position: sum lengths of all lines before the target line
        pos = 0
        for i in range(min(line_no, len(lines))):
            pos += len(lines[i])

        cursor = self.patterns_edit.textCursor()
        # Move cursor to start of the line
        cursor.setPosition(pos)

        # If the target line exists, select to the end of that line
        if line_no < len(lines):
            end_pos = pos + len(lines[line_no])
            # KeepAnchor selects the text between current position and end_pos
            cursor.setPosition(end_pos, QTextCursor.KeepAnchor)
        else:
            # If for some reason the line doesn't exist (shouldn't happen), move to document end
            cursor.setPosition(len(self.patterns_edit.toPlainText()))

        self.patterns_edit.setTextCursor(cursor)
        self.patterns_edit.setFocus()


    # def on_rule_clicked(self, item: QListWidgetItem):
    #     """
    #     Jump to the corresponding line in the patterns editor and select it.
    #     """
    #     line_no = item.data(Qt.UserRole)
    #     if line_no is None:
    #         return
    #     # Move cursor to start of line_no
    #     cursor = self.patterns_edit.textCursor()
    #     # compute position by summing lengths of previous lines + newline
    #     lines = self.patterns_edit.toPlainText().splitlines(True)  # keepends True so lengths include newlines
    #     pos = 0
    #     for i in range(min(line_no, len(lines))):
    #         pos += len(lines[i])
    #     cursor.setPosition(pos)
    #     # select the whole line (if exists)
    #     if line_no < len(lines):
    #         cursor.movePosition(cursor.EndOfLine, cursor.KeepAnchor)
    #     self.patterns_edit.setTextCursor(cursor)
    #     self.patterns_edit.setFocus()

    # -------------------------
    # Button action implementations
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
        self._update_labels()

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
        self._update_labels()

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
        self._update_labels()

    def run_patterns(self):
        if not self.text_path:
            QMessageBox.warning(self, "No text file", "Please select a text file first (button: Select text file).")
            return
        if not self.patterns_edit.toPlainText().strip():
            QMessageBox.warning(self, "No patterns", "No patterns are loaded or the patterns file is empty.")
            return

        original_text = self.text_view.toPlainText() or ""

        # Parse patterns, keeping order
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

            # inject CSS before </style> (HtmlDiff always provides a <style>...)
            css_injection = """
/* hide header and left-most columns (the "original" side) */
td {font-size: 10pt;}
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
        self._update_labels()

    # -------------------------
    # Help dialog / tooltip
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
