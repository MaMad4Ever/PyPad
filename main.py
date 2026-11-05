#!/usr/bin/env python3
"""PyPad - a small Python-focused text editor built with PyQt6.

Usage:  pypad              open PyPad
        pypad here.txt     open (or create on save) here.txt
"""
from __future__ import annotations

import builtins
import argparse
import json
import keyword
import os
import re
import sys
import traceback
from pathlib import Path

from PyQt6.QtCore import QObject, QRect, QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QAction, QColor, QDesktopServices, QFontDatabase, QKeySequence, QPainter, QSyntaxHighlighter,
    QTextCharFormat, QTextCursor, QTextDocument, QTextFormat,
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QTabWidget, QTextEdit, QWidget,
)

APP_NAME = "PyPad"
APP_VERSION = "1.0.0"
TWITTER_URL = "https://twitter.com/MaMad4Ever"
FILE_FILTER = "All Files (*);;Python Files (*.py);;Text Files (*.txt)"
PYTHON_EXTS = (".py", ".pyw")

LICENSE_TEXT = """LICENSE_TEXT = "PyPad is released under the MIT License.\nCopyright (c) 2026 MaMad4Ever"""


def _config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "pypad"


SETTINGS_FILE = _config_dir() / "settings.json"  # user-editable, opened from Edit > Settings
SESSION_FILE = _config_dir() / "session.json"    # open tabs, managed by the app
DEFAULT_SETTINGS = {"font_size": 12, "tab_width": 4, "dark_mode": False, "recent_files": []}


def _write_json(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # best effort


def _is_python_path(path: str | None) -> bool:
    """Untitled buffers count as Python; other files only by extension."""
    return not path or os.path.splitext(path)[1].lower() in PYTHON_EXTS


DARK_QSS = """
    QWidget { background-color: #1e1e1e; color: #d4d4d4; }
    QLineEdit, QMenuBar, QMenu { background-color: #252526; }
    QMenuBar::item:selected, QMenu::item:selected { background-color: #3f3f40; }
    QStatusBar { background-color: #007acc; color: #ffffff; }
    QStatusBar QLabel { background: transparent; }
"""


# =========================================================================
# Editor widgets
# =========================================================================

class LineNumberArea(QWidget):
    """Gutter that paints line numbers for a PyEdit."""

    def __init__(self, editor: PyEdit):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        ed, clip = self.editor, event.rect()
        painter = QPainter(self)
        painter.fillRect(clip, QColor("#2d2d30"))
        painter.setPen(QColor("#808080"))
        height = ed.fontMetrics().height()

        block = ed.firstVisibleBlock()
        number = block.blockNumber()
        top = round(ed.blockBoundingGeometry(block).translated(ed.contentOffset()).top())
        while block.isValid() and top <= clip.bottom():
            bottom = top + round(ed.blockBoundingRect(block).height())
            if block.isVisible() and bottom >= clip.top():
                painter.drawText(0, top, self.width() - 4, height,
                                 Qt.AlignmentFlag.AlignRight, str(number + 1))
            block, top, number = block.next(), bottom, number + 1
        painter.end()


class PyEdit(QPlainTextEdit):
    """Code editor: line numbers, current-line highlight, auto-indent, highlighter."""
    modification_state_changed = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.file_path: str | None = None
        self.untitled_name = "Untitled"
        self.tab_width = 4
        self._highlighting = True
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.document().modificationChanged.connect(self.modification_state_changed)
        self.highlighter = PythonHighlighter(self.document())  # keep a reference

        self.gutter = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self.setFont(self.font())  # computes tab stop + gutter width
        self._highlight_current_line()

    # --- font / gutter -----------------------------------------------------
    def setFont(self, font):
        """Keeps the tab stop and the gutter in sync with the font."""
        super().setFont(font)
        self.set_tab_width(self.tab_width)
        self._update_gutter_width()

    def set_tab_width(self, width: int):
        self.tab_width = width
        self.setTabStopDistance(width * self.fontMetrics().horizontalAdvance(" "))

    def set_highlighting(self, on: bool):
        """Python syntax colours only for Python files (plain text otherwise)."""
        if on != self._highlighting:
            self._highlighting = on
            self.highlighter.setDocument(self.document() if on else None)

    def gutter_width(self) -> int:
        digits = max(3, len(str(self.blockCount())))
        return 10 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, _count: int = 0):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)
        self._layout_gutter()

    def _layout_gutter(self):
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_gutter()

    # --- behaviour ---------------------------------------------------------
    def _highlight_current_line(self):
        if self.textCursor().hasSelection():
            self.setExtraSelections([])
            return
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor(128, 128, 128, 40))  # works on light + dark
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])

    def keyPressEvent(self, e):
        key, cursor, width = e.key(), self.textCursor(), self.tab_width
        if key == Qt.Key.Key_Tab and not cursor.hasSelection():
            self.insertPlainText(" " * (width - cursor.positionInBlock() % width))  # Tab -> spaces
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):  # auto-indent
            before = cursor.block().text()[:cursor.positionInBlock()]
            indent = before[:len(before) - len(before.lstrip())]
            if self._highlighting and before.rstrip().endswith(":"):  # Python only
                indent += " " * width
            super().keyPressEvent(e)
            self.insertPlainText(indent)
        else:
            super().keyPressEvent(e)


class FindReplaceDialog(QDialog):
    """Non-modal find/replace panel; stays open between searches."""
    action_requested = pyqtSignal(str)  # "find" | "replace" | "replace_all"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Find and Replace")
        self.find_input, self.replace_input = QLineEdit(), QLineEdit()
        self.case_box, self.word_box = QCheckBox("Case sensitive"), QCheckBox("Whole word")

        form = QFormLayout(self)
        form.addRow("Find:", self.find_input)
        form.addRow("Replace with:", self.replace_input)
        options = QHBoxLayout()
        options.addWidget(self.case_box)
        options.addWidget(self.word_box)
        form.addRow(options)
        buttons = QHBoxLayout()
        for label, action in (("Find Next", "find"), ("Replace", "replace"),
                              ("Replace All", "replace_all")):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, a=action: self.action_requested.emit(a))
            buttons.addWidget(button)
        form.addRow(buttons)
        self.find_input.returnPressed.connect(lambda: self.action_requested.emit("find"))

    def find_flags(self) -> QTextDocument.FindFlag:
        flags = QTextDocument.FindFlag(0)
        if self.case_box.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if self.word_box.isChecked():
            flags |= QTextDocument.FindFlag.FindWholeWords
        return flags


# =========================================================================
# Syntax highlighting
# =========================================================================

def _fmt(color: str, italic: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(color))
    fmt.setFontItalic(italic)
    return fmt


class PythonHighlighter(QSyntaxHighlighter):
    """Single-pass tokenizer: one regex scan per line instead of ~120.

    Because tokens are consumed left to right, a '#' inside a string or a quote
    inside a comment is no longer mis-coloured. Triple-quoted strings span lines.
    """
    _BUILTINS = [n for n in dir(builtins) if n.islower() and not n.startswith("_")]
    _TRIPLES = {1: "'''", 2: '"""'}
    _TOKEN = re.compile("|".join([
        r"(?P<comment>#.*)",
        r"(?P<triple>'''|\"\"\")",
        r"(?P<string>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')",
        r"(?P<func>(?<=def )\w+)",
        r"(?P<cls>(?<=class )\w+)",
        r"(?P<self>\bself\b)",
        r"(?P<kw>\b(?:" + "|".join(keyword.kwlist) + r")\b)",
        r"(?P<builtin>\b(?:" + "|".join(_BUILTINS) + r")\b)",
        r"(?P<number>\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)",
    ]))

    def __init__(self, parent: QTextDocument):
        super().__init__(parent)
        self._formats = {
            "comment": _fmt("#757575"), "string": _fmt("#6a9955"), "number": _fmt("#b5cea8"),
            "kw": _fmt("#569cd6"), "builtin": _fmt("#ffc66d"), "self": _fmt("#569cd6", True),
            "func": _fmt("#dcdcaa"), "cls": _fmt("#4ec9b0"),
        }

    def highlightBlock(self, text: str) -> None:
        string_fmt = self._formats["string"]
        pos, state = 0, self.previousBlockState()  # 1 = inside ''', 2 = inside """
        self.setCurrentBlockState(0)
        if state in self._TRIPLES:
            end = text.find(self._TRIPLES[state])
            if end == -1:
                self.setFormat(0, len(text), string_fmt)
                self.setCurrentBlockState(state)
                return
            pos = end + 3
            self.setFormat(0, pos, string_fmt)

        while (m := self._TOKEN.search(text, pos)):
            kind, start = m.lastgroup, m.start()
            if kind == "triple":
                delim = m.group()
                end = text.find(delim, m.end())
                if end == -1:
                    self.setFormat(start, len(text) - start, string_fmt)
                    self.setCurrentBlockState(1 if delim == "'''" else 2)
                    return
                pos = end + 3
                self.setFormat(start, pos - start, string_fmt)
            else:
                pos = m.end()
                self.setFormat(start, pos - start, self._formats[kind])


# =========================================================================
# Main window (UI only)
# =========================================================================

class PyPadWindow(QMainWindow):
    recent_file_requested = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(800, 600)
        self.setMinimumSize(QSize(400, 300))
        self._font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self._font.setPointSize(12)
        self._tab_width = 4

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.setCentralWidget(self.tabs)

        self._create_menus()
        self.cursor_label, self.type_label = QLabel("Ln 1, Col 1"), QLabel("Plain Text")
        for label in (self.cursor_label, self.type_label):
            self.statusBar().addPermanentWidget(label)

    def _create_menus(self) -> None:
        def add(menu, text, shortcut=None) -> QAction:
            action = QAction(text, self)
            if shortcut is not None:
                action.setShortcut(QKeySequence(shortcut))
            menu.addAction(action)
            return action

        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        self.new_action = add(file_menu, "&New", "Ctrl+N")
        self.open_action = add(file_menu, "&Open...", "Ctrl+O")
        self.recent_menu = file_menu.addMenu("Recent Files")
        file_menu.addSeparator()
        self.save_action = add(file_menu, "&Save", "Ctrl+S")
        self.save_as_action = add(file_menu, "Save &As...", "Ctrl+Shift+S")
        file_menu.addSeparator()
        self.duplicate_action = add(file_menu, "&Duplicate Tab", "Ctrl+Shift+D")
        self.close_all_action = add(file_menu, "Close All", "Ctrl+Shift+W")
        file_menu.addSeparator()
        self.quit_action = add(file_menu, "&Quit", "Ctrl+Q")

        edit_menu = bar.addMenu("&Edit")
        Std = QKeySequence.StandardKey
        self.undo_action = add(edit_menu, "&Undo", Std.Undo)
        self.redo_action = add(edit_menu, "&Redo", Std.Redo)
        edit_menu.addSeparator()
        self.cut_action = add(edit_menu, "Cu&t", Std.Cut)
        self.copy_action = add(edit_menu, "&Copy", Std.Copy)
        self.paste_action = add(edit_menu, "&Paste", Std.Paste)
        edit_menu.addSeparator()
        self.font_up_action = add(edit_menu, "Increase Font")
        self.font_up_action.setShortcuts([QKeySequence("Ctrl++"), QKeySequence("Ctrl+=")])
        self.font_down_action = add(edit_menu, "Decrease Font", "Ctrl+-")
        edit_menu.addSeparator()
        self.theme_action = add(edit_menu, "Toggle Theme", "Ctrl+T")
        edit_menu.addSeparator()
        self.goto_action = add(edit_menu, "Go to Line...", "Ctrl+G")
        self.find_action = add(edit_menu, "Find and Replace...", "Ctrl+F")
        edit_menu.addSeparator()
        self.settings_action = add(edit_menu, "&Settings (JSON)", "Ctrl+,")

        tools_menu = bar.addMenu("&Tools")
        self.dedupe_action = add(tools_menu, "&Deduplicate Lines")
        self.word_count_action = add(tools_menu, "&Word Count")

        help_menu = bar.addMenu("&Help")
        self.twitter_action = add(help_menu, "&Twitter")
        self.about_action = add(help_menu, "&About")
        self.license_action = add(help_menu, "&License")

    def get_current_editor(self) -> PyEdit | None:
        return self.tabs.currentWidget()

    def new_tab(self, title: str) -> PyEdit:
        editor = PyEdit()
        editor.setFont(self._font)
        editor.set_tab_width(self._tab_width)
        self.tabs.setCurrentIndex(self.tabs.addTab(editor, title))
        return editor

    def set_font_size(self, size: int) -> None:
        self._font.setPointSize(size)
        for i in range(self.tabs.count()):
            self.tabs.widget(i).setFont(self._font)

    def set_tab_width(self, width: int) -> None:
        self._tab_width = width
        for i in range(self.tabs.count()):
            self.tabs.widget(i).set_tab_width(width)

    def update_recent_files_menu(self, files: list[str]) -> None:
        self.recent_menu.clear()
        for path in files:
            action = self.recent_menu.addAction(os.path.basename(path))
            action.triggered.connect(lambda _=False, p=path: self.recent_file_requested.emit(p))


# =========================================================================
# Controller
# =========================================================================

class PyPadController(QObject):
    def __init__(self, ui: PyPadWindow):
        super().__init__()
        self.ui = ui
        self.find_dialog = FindReplaceDialog(ui)
        self.untitled_counter = 1
        self.settings_path = os.path.abspath(SETTINGS_FILE)
        self.settings_invalid = False
        self.font_size, self.tab_width, self.dark_mode, self.recent_files = 12, 4, False, []
        self._load_settings()
        self._connect_signals()
        self._restore_session()

    # --- wiring ------------------------------------------------------------
    def _connect_signals(self) -> None:
        ui = self.ui
        ui.tabs.tabCloseRequested.connect(self._on_close_requested)
        ui.tabs.currentChanged.connect(lambda _: self._update_status())
        ui.recent_file_requested.connect(self.open_path)
        self.find_dialog.action_requested.connect(self._on_find_action)

        bindings = [
            (ui.new_action, self._new_file), (ui.open_action, self._open_dialog),
            (ui.save_action, self._save), (ui.save_as_action, self._save_as),
            (ui.duplicate_action, self._duplicate), (ui.close_all_action, self._close_all),
            (ui.quit_action, QApplication.quit),
            (ui.font_up_action, lambda: self._change_font(1)),
            (ui.font_down_action, lambda: self._change_font(-1)),
            (ui.theme_action, self._toggle_theme), (ui.goto_action, self._go_to_line),
            (ui.find_action, self._show_find), (ui.settings_action, self._open_settings),
            (ui.undo_action, lambda: self._edit("undo")), (ui.redo_action, lambda: self._edit("redo")),
            (ui.cut_action, lambda: self._edit("cut")), (ui.copy_action, lambda: self._edit("copy")),
            (ui.paste_action, lambda: self._edit("paste")),
            (ui.dedupe_action, self._deduplicate), (ui.word_count_action, self._word_count),
            (ui.twitter_action, lambda: QDesktopServices.openUrl(QUrl(TWITTER_URL))),
            (ui.about_action, self._show_about), (ui.license_action, self._show_license),
        ]
        for action, slot in bindings:  # slots take no args; drop the `checked` bool
            action.triggered.connect(lambda _=False, s=slot: s())

    # --- settings / session ------------------------------------------------
    def _load_settings(self) -> None:
        """Reads settings.json; on a broken file keeps the current values and warns."""
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8")) if SETTINGS_FILE.exists() else {}
            cfg = {**DEFAULT_SETTINGS, **data}
            values = (max(6, min(48, int(cfg["font_size"]))), max(1, min(16, int(cfg["tab_width"]))),
                      bool(cfg["dark_mode"]),
                      [p for p in map(str, cfg["recent_files"]) if os.path.exists(p)])
        except (OSError, ValueError, TypeError) as e:
            self.settings_invalid = True
            QMessageBox.warning(self.ui, "Settings",
                                f"Could not read {SETTINGS_FILE}:\n{e}\n\nKeeping the previous settings.")
            return
        self.settings_invalid = False
        self.font_size, self.tab_width, self.dark_mode, self.recent_files = values

    def _apply_settings(self) -> None:
        self.ui.set_font_size(self.font_size)
        self.ui.set_tab_width(self.tab_width)
        self.ui.update_recent_files_menu(self.recent_files)
        self._apply_theme()

    def _write_settings_file(self) -> None:
        if not self.settings_invalid:  # never overwrite a file the user still has to fix
            _write_json(SETTINGS_FILE, {
                "font_size": self.font_size, "tab_width": self.tab_width, "dark_mode": self.dark_mode,
                "recent_files": [p for p in self.recent_files if os.path.exists(p)]})

    def _open_settings(self) -> None:
        already_open = any(self.ui.tabs.widget(i).file_path == self.settings_path
                           for i in range(self.ui.tabs.count()))
        if not already_open:
            self._write_settings_file()  # make sure the file exists and is current
        self.open_path(self.settings_path)

    def _restore_session(self) -> None:
        self._apply_settings()
        try:
            tabs = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            tabs = []
        missing = []
        for tab in tabs:
            path, dirty, content = tab.get("path"), tab.get("is_dirty", False), tab.get("content", "")
            if path and not os.path.exists(path):
                missing.append(path)
                path = None
            elif path and not dirty:  # clean tabs always reflect what is on disk now
                try:
                    content = Path(path).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    missing.append(path)
                    continue
            if path or content:
                self._new_file(content, path, dirty or path is None)
        if missing:
            QMessageBox.warning(self.ui, "Files Not Found",
                                "Could not restore:\n" + "\n".join(missing))
        self._ensure_tab()

    def save_settings(self) -> None:
        """Called on quit: writes settings.json and the open-tabs session."""
        tabs = []
        for i in range(self.ui.tabs.count()):
            editor = self.ui.tabs.widget(i)
            dirty = editor.document().isModified()
            if editor.file_path or editor.toPlainText():
                keep = dirty or not editor.file_path  # content only when it can't be re-read from disk
                tabs.append({"path": editor.file_path, "is_dirty": dirty,
                             "content": editor.toPlainText() if keep else ""})
        self._write_settings_file()
        _write_json(SESSION_FILE, tabs)

    # --- tabs / status -----------------------------------------------------
    def _new_file(self, text: str = "", path: str | None = None, dirty: bool = False) -> PyEdit:
        name = None
        if path is None:
            name = f"Untitled {self.untitled_counter}"
            self.untitled_counter += 1
        editor = self.ui.new_tab(name or os.path.basename(path))
        editor.file_path, editor.untitled_name = path, name
        editor.set_highlighting(_is_python_path(path))
        editor.setPlainText(text)
        editor.document().setModified(dirty)
        editor.modification_state_changed.connect(lambda _=False, e=editor: self._refresh(e))
        editor.cursorPositionChanged.connect(self._update_status)
        self._refresh(editor)
        return editor

    def _ensure_tab(self) -> None:
        if not self.ui.tabs.count():
            self._new_file()

    def _refresh(self, editor: PyEdit) -> None:
        """Updates the tab title ('name*' when modified) and the status bar."""
        editor.set_highlighting(_is_python_path(editor.file_path))
        index = self.ui.tabs.indexOf(editor)
        if index >= 0:
            name = os.path.basename(editor.file_path) if editor.file_path else editor.untitled_name
            self.ui.tabs.setTabText(index, name + ("*" if editor.document().isModified() else ""))
            self.ui.tabs.setTabToolTip(index, editor.file_path or "")
        self._update_status()

    def _update_status(self) -> None:
        ui, editor = self.ui, self.ui.get_current_editor()
        if not editor:
            ui.statusBar().showMessage("No file open")
            ui.cursor_label.setText("")
            ui.type_label.setText("")
            ui.setWindowTitle(APP_NAME)
            return
        cursor = editor.textCursor()
        ui.cursor_label.setText(f"Ln {cursor.blockNumber() + 1}, Col {cursor.columnNumber() + 1}")
        ext = os.path.splitext(editor.file_path or "")[1].lower()
        kinds = {".py": "Python", ".pyw": "Python", ".txt": "Text"}
        ui.type_label.setText(kinds.get(ext, ext or ("Plain Text" if editor.file_path else "Python")))
        state = "Unsaved changes" if editor.document().isModified() else "Ready"
        ui.statusBar().showMessage(f"{editor.file_path or 'New file'} - {state}")
        ui.setWindowTitle(f"{APP_NAME} - {ui.tabs.tabText(ui.tabs.currentIndex())}")

    def _confirm_close(self, editor: PyEdit) -> bool:
        if not editor.document().isModified():
            return True
        name = self.ui.tabs.tabText(self.ui.tabs.indexOf(editor)).rstrip("*")
        Btn = QMessageBox.StandardButton
        choice = QMessageBox.question(self.ui, "Unsaved Changes", f"Save changes to {name}?",
                                      Btn.Save | Btn.Discard | Btn.Cancel, Btn.Save)
        return self._save(editor) if choice == Btn.Save else choice == Btn.Discard

    def _close_tab(self, index: int) -> bool:
        editor = self.ui.tabs.widget(index)
        if not self._confirm_close(editor):
            return False
        self.ui.tabs.removeTab(index)
        editor.deleteLater()
        return True

    def _on_close_requested(self, index: int) -> None:
        self._close_tab(index)
        self._ensure_tab()

    def _close_all(self) -> None:
        while self.ui.tabs.count() and self._close_tab(0):
            pass
        self._ensure_tab()

    def _duplicate(self) -> None:
        editor = self.ui.get_current_editor()
        if editor:
            self._new_file(editor.toPlainText(), dirty=True)  # an independent, unsaved copy

    # --- files -------------------------------------------------------------
    def _open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self.ui, "Open File", "", FILE_FILTER)
        if path:
            self.open_path(path)

    def open_path(self, path: str, create: bool = False) -> None:
        """Opens a file in a tab. With create=True a missing file opens empty (made on save)."""
        path = os.path.abspath(path)
        for i in range(self.ui.tabs.count()):
            if self.ui.tabs.widget(i).file_path == path:
                self.ui.tabs.setCurrentIndex(i)
                return
        text = ""
        if not (create and not os.path.exists(path)):
            try:
                text = Path(path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                QMessageBox.critical(self.ui, "Error", f"Could not open file: {e}")
                return
        blank = self.ui.get_current_editor()  # replace a pristine "Untitled" tab
        if blank and (blank.file_path or blank.document().isModified() or not blank.document().isEmpty()):
            blank = None
        self._new_file(text, path)
        if blank:
            self.ui.tabs.removeTab(self.ui.tabs.indexOf(blank))
            blank.deleteLater()
        self._add_recent(path)

    def _write(self, editor: PyEdit, path: str) -> bool:
        try:
            Path(path).write_text(editor.toPlainText(), encoding="utf-8")
        except OSError as e:
            QMessageBox.critical(self.ui, "Error", f"Could not save file: {e}")
            return False
        editor.file_path = path
        editor.document().setModified(False)
        self._add_recent(path)
        if path == self.settings_path:  # edited settings.json -> apply immediately
            self._load_settings()
            self._apply_settings()
        self._refresh(editor)
        return True

    def _save(self, editor: PyEdit | None = None) -> bool:
        editor = editor or self.ui.get_current_editor()
        if not editor:
            return False
        return self._write(editor, editor.file_path) if editor.file_path else self._save_as(editor)

    def _save_as(self, editor: PyEdit | None = None) -> bool:
        editor = editor or self.ui.get_current_editor()
        if not editor:
            return False
        path, _ = QFileDialog.getSaveFileName(self.ui, "Save File As", editor.file_path or "", FILE_FILTER)
        return bool(path) and self._write(editor, os.path.abspath(path))

    def _add_recent(self, path: str) -> None:
        if path == self.settings_path:
            return
        self.recent_files = [path] + [p for p in self.recent_files if p != path][:9]
        self.ui.update_recent_files_menu(self.recent_files)

    # --- edit / tools / help -----------------------------------------------
    def _edit(self, operation: str) -> None:
        editor = self.ui.get_current_editor()
        if editor:
            getattr(editor, operation)()  # undo / redo / cut / copy / paste

    def _selection_or_document(self, editor: PyEdit) -> tuple[QTextCursor, bool]:
        cursor = editor.textCursor()
        has_selection = cursor.hasSelection()
        if not has_selection:
            cursor.select(QTextCursor.SelectionType.Document)
        return cursor, has_selection

    def _deduplicate(self) -> None:
        """Removes repeated lines (keeps the first one; blank lines are left alone)."""
        editor = self.ui.get_current_editor()
        if not editor:
            return
        cursor, _ = self._selection_or_document(editor)
        lines = cursor.selectedText().split("\u2029")
        seen, unique = set(), []
        for line in lines:
            if line.strip() and line in seen:
                continue
            seen.add(line)
            unique.append(line)
        removed = len(lines) - len(unique)
        if removed:
            cursor.insertText("\n".join(unique))  # a single undo step
        self.ui.statusBar().showMessage(f"Removed {removed} duplicate line(s)", 5000)

    def _word_count(self) -> None:
        editor = self.ui.get_current_editor()
        if not editor:
            return
        cursor, partial = self._selection_or_document(editor)
        text = cursor.selectedText().replace("\u2029", "\n")
        QMessageBox.information(
            self.ui, "Word Count",
            f"{'Selection' if partial else 'Document'}\n\n"
            f"Words: {len(text.split())}\n"
            f"Characters: {len(text)}\n"
            f"Characters (no spaces): {len(''.join(text.split()))}\n"
            f"Lines: {text.count(chr(10)) + 1 if text else 0}")

    def _show_about(self) -> None:
        QMessageBox.about(self.ui, f"About {APP_NAME}",
                          f"<b>{APP_NAME}</b> {APP_VERSION}<br>A small Python-focused text editor "
                          "built with PyQt6.<br>It also works great for plain .txt files.")

    def _show_license(self) -> None:
        QMessageBox.information(self.ui, "License", LICENSE_TEXT)

    # --- view --------------------------------------------------------------
    def _change_font(self, delta: int) -> None:
        self.font_size = max(6, min(48, self.font_size + delta))
        self.ui.set_font_size(self.font_size)

    def _apply_theme(self) -> None:
        self.ui.setStyleSheet(DARK_QSS if self.dark_mode else "")

    def _toggle_theme(self) -> None:
        self.dark_mode = not self.dark_mode
        self._apply_theme()

    def _go_to_line(self) -> None:
        editor = self.ui.get_current_editor()
        if not editor:
            return
        count = editor.blockCount()
        line, ok = QInputDialog.getInt(self.ui, "Go to Line", f"Line number (1-{count}):",
                                       editor.textCursor().blockNumber() + 1, 1, count)
        if ok:
            editor.setTextCursor(QTextCursor(editor.document().findBlockByNumber(line - 1)))
            editor.centerCursor()

    # --- find / replace ----------------------------------------------------
    def _show_find(self) -> None:
        editor, dialog = self.ui.get_current_editor(), self.find_dialog
        if editor and editor.textCursor().hasSelection():
            selected = editor.textCursor().selectedText()
            if "\u2029" not in selected:  # single-line selections only
                dialog.find_input.setText(selected)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        dialog.find_input.setFocus()
        dialog.find_input.selectAll()

    def _find_next(self, editor: PyEdit, text: str, flags) -> bool:
        if editor.find(text, flags):
            return True
        previous = editor.textCursor()  # wrap around, but restore the cursor if nothing matches
        editor.moveCursor(QTextCursor.MoveOperation.Start)
        if editor.find(text, flags):
            return True
        editor.setTextCursor(previous)
        return False

    def _replace_all(self, editor: PyEdit, text: str, replacement: str, flags) -> int:
        doc, count = editor.document(), 0
        edit, search = QTextCursor(doc), QTextCursor(doc)
        edit.beginEditBlock()  # one undo step for the whole operation
        while True:
            search = doc.find(text, search, flags)
            if search.isNull():
                break
            search.insertText(replacement)
            count += 1
        edit.endEditBlock()
        return count

    def _on_find_action(self, action: str) -> None:
        editor, dialog = self.ui.get_current_editor(), self.find_dialog
        text, replacement, flags = dialog.find_input.text(), dialog.replace_input.text(), dialog.find_flags()
        if not editor or not text:
            return
        if action == "replace_all":
            count = self._replace_all(editor, text, replacement, flags)
            self.ui.statusBar().showMessage(f"Replaced {count} occurrence(s)", 5000)
            return
        if action == "replace":
            cursor = editor.textCursor()
            selected = cursor.selectedText()
            if selected == text if dialog.case_box.isChecked() else selected.lower() == text.lower():
                cursor.insertText(replacement)
                editor.setTextCursor(cursor)
        if not self._find_next(editor, text, flags):
            self.ui.statusBar().showMessage(f"'{text}' not found", 5000)

# =========================================================================
# Entry point
# =========================================================================

def _excepthook(exc_type, exc, tb) -> None:
    """PyQt6 aborts on uncaught slot exceptions; show them instead."""
    traceback.print_exception(exc_type, exc, tb)
    QMessageBox.critical(None, "Unexpected Error", f"{exc_type.__name__}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="pypad", description="A small Python-focused text editor.")
    parser.add_argument("files", nargs="*", help="files to open (created on save if missing)")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    args, qt_args = parser.parse_known_args()  # leave Qt's own options alone

    sys.excepthook = _excepthook
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("pypad")

    ui = PyPadWindow()
    controller = PyPadController(ui)
    for path in args.files:  # `pypad here.txt`
        controller.open_path(path, create=True)
    app.aboutToQuit.connect(controller.save_settings)

    ui.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
