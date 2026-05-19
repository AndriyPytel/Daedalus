# FreeCadCopilot/ui/upload_widget.py
"""
UploadWidget + FileChipBar

Provides:
- A "+" trigger button (transparent, minimal)
- A FileChipBar that shows attached files as removable chips above the input
- handle_clipboard_paste() to be called from the input QTextEdit keyPressEvent
"""
import os
import tempfile

try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui
from ui.ui_helpers import UIHelpers


# ── Individual file chip ──────────────────────────────────────────────────────

class _FileChip(QtWidgets.QWidget):
    """[📄 filename ×] or [🖼 filename ×] removable chip."""

    remove_requested = QtCore.Signal(str)  # emits the file path

    _ICON_MAP = {
        ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
        ".pdf": "file_text",
    }

    def __init__(self, filepath: str):
        super().__init__()
        self.filepath = filepath
        ext  = os.path.splitext(filepath)[1].lower()
        icon_name = self._ICON_MAP.get(ext, "attachment")
        name = os.path.basename(filepath)
        if len(name) > 22:
            name = name[:19] + "…"

        self.layout = QtWidgets.QHBoxLayout(self)
        self.layout.setContentsMargins(8, 4, 8, 4)
        self.layout.setSpacing(6)

        icon_lbl = QtWidgets.QLabel()
        icon_lbl.setPixmap(UIHelpers.get_icon(icon_name, size=14).pixmap(14, 14))
        self.layout.addWidget(icon_lbl)

        self.lbl = QtWidgets.QLabel(name)
        self.lbl.setStyleSheet("font-size: 11px; color: palette(text);")
        self.layout.addWidget(self.lbl)

        self.btn = QtWidgets.QPushButton()
        self.btn.setIcon(UIHelpers.get_icon("close", color="#E05252", size=12))
        self.btn.setFixedSize(16, 16)
        self.btn.setStyleSheet("""
            QPushButton {
                border: none;
                background: transparent;
                font-size: 14px;
                font-weight: bold;
                padding: 0px;
                margin-top: -2px;
                color: palette(mid);
            }
            QPushButton:hover { color: #E05252; }
        """)
        self.btn.clicked.connect(lambda: self.remove_requested.emit(self.filepath))
        self.btn.setVisible(False)  # Hidden until hover
        self.layout.addWidget(self.btn)

        highlight = self.palette().color(QtGui.QPalette.Highlight)
        bg_rgba = f"rgba({highlight.red()}, {highlight.green()}, {highlight.blue()}, 51)" # 20% opacity
        self.setStyleSheet(f"""
            _FileChip {{
                border: 1px solid palette(mid);
                border-radius: 12px;
                background: {bg_rgba};
            }}
            _FileChip:hover {{
                border: 1px solid palette(highlight);
            }}
        """)
        self.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)

    def enterEvent(self, event):
        self.btn.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.btn.setVisible(False)
        super().leaveEvent(event)


# ── Chip container bar ────────────────────────────────────────────────────────

class FileChipBar(QtWidgets.QWidget):
    """
    Horizontal strip of FileChip widgets shown above the input field.
    Hides itself when no files are attached.
    """

    def __init__(self):
        super().__init__()
        self._layout = QtWidgets.QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.addStretch()
        self.setVisible(False)
        self._files: list[str] = []

    def add_file(self, filepath: str):
        if filepath in self._files:
            return
        chip = _FileChip(filepath)
        chip.remove_requested.connect(self.remove_file)
        # Insert before the trailing stretch
        insert_pos = self._layout.count() - 1
        self._layout.insertWidget(insert_pos, chip)
        self._files.append(filepath)
        self.setVisible(True)

    def remove_file(self, filepath: str):
        if filepath not in self._files:
            return
        self._files.remove(filepath)
        # Find and delete the chip widget
        for i in range(self._layout.count() - 1):
            item = self._layout.itemAt(i)
            if item and item.widget() and getattr(item.widget(), "filepath", None) == filepath:
                w = self._layout.takeAt(i).widget()
                w.deleteLater()
                break
        if not self._files:
            self.setVisible(False)

    def get_and_clear(self) -> list[str]:
        files = list(self._files)
        for filepath in list(self._files):
            self.remove_file(filepath)
        return files


# ── Upload trigger button ─────────────────────────────────────────────────────

class UploadWidget(QtWidgets.QWidget):
    """
    A minimal "+" icon button that opens a file dialog.
    Chips are rendered by the FileChipBar (owned by copilot_panel).
    """

    file_attached = QtCore.Signal(str)   # emits path of newly attached file

    def __init__(self):
        super().__init__()
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.upload_btn = QtWidgets.QPushButton()
        self.upload_btn.setIcon(UIHelpers.get_icon("plus", size=20))
        self.upload_btn.setFixedSize(28, 28)
        self.upload_btn.setToolTip("Attach file (image / PDF)")
        self.upload_btn.setStyleSheet("""
            QPushButton {
                border: none;
                background: transparent;
                font-size: 18px;
                font-weight: bold;
                color: palette(text);
                padding: 0px;
            }
            QPushButton:hover { color: palette(text); }
        """)
        self.upload_btn.clicked.connect(self.open_file_dialog)
        layout.addWidget(self.upload_btn)

        # Legacy: keep a simple list so get_and_clear_files() still works
        # if called from old copilot_panel code during transition
        self._files: list[str] = []

    def open_file_dialog(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Attach Context Files", "",
            "Supported (*.pdf *.png *.jpg *.jpeg);;All (*)"
        )
        for f in files:
            self._files.append(f)
            self.file_attached.emit(f)
            from utils.logger import logger
            logger.info(f"[Copilot] Attached file: {f}")

    def handle_clipboard_paste(self) -> bool:
        """
        Call this from the QTextEdit's keyPressEvent (Ctrl+V).
        Returns True if an image was found and handled (suppress default paste).
        """
        clipboard = QtWidgets.QApplication.clipboard()
        mime = clipboard.mimeData()

        if mime.hasImage():
            pix = QtGui.QPixmap(mime.imageData())
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            pix.save(tmp.name, "PNG")
            tmp.close()
            self._files.append(tmp.name)
            self.file_attached.emit(tmp.name)
            from utils.logger import logger
            logger.info(f"[Copilot] Clipboard image saved to: {tmp.name}")
            return True
        return False

    # Legacy compat
    def get_and_clear_files(self) -> list[str]:
        files = list(self._files)
        self._files.clear()
        return files
