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
from ui.design_tokens import CONTROL_DENSE, ICON, RADIUS_CONTROL, SPACE_XS, WASH
from ui.ui_helpers import UIHelpers


NAME_KEPT = 22


def _cut_to_fit(name: str) -> str:
    """
    *name* with its middle elided, where it is longer than `NAME_KEPT`.

    The middle rather than the tail: a name this addon writes ends in a run id
    and a timestamp, and the extension is the one part of it that says what
    clicking the chip will open.
    """
    if len(name) <= NAME_KEPT:
        return name
    head = (NAME_KEPT - 1) // 2
    return name[:head] + "…" + name[len(name) - (NAME_KEPT - 1 - head):]


class FileChip(QtWidgets.QWidget):
    """
    [📄 filename ×] or [🖼 filename ×] chip, removable while it is still an
    attachment and fixed once it is a record of one.

    The chip is a translucent grey rather than a colour, so it works over any
    theme (ADR-0011).

    The transcript shows the same chip for a file it could not draw -- a PDF
    has no thumbnail, and the name of the drawing a run was given is worth more
    than the paperclip character that stood there. Nothing in a transcript is
    taken back, so that one carries no remove button at all. It is the one file
    shape the design has, so the document a run writes wears it too.

    Clicking one opens the file where the desktop opens it. The addon has no
    viewer of its own and a drawing is looked at again in the middle of a run;
    `core.sheet_reader` renders a page, but it is the evidence pipeline rather
    than a thumbnailer and would run its whole analysis on the GUI thread.
    """

    remove_requested = QtCore.Signal(str)

    _ICON_MAP = {
        ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
        ".pdf": "file_text",
    }

    def __init__(self, filepath: str, removable: bool = True):
        super().__init__()
        self._removable = removable
        self.filepath = filepath
        ext  = os.path.splitext(filepath)[1].lower()
        icon_name = self._ICON_MAP.get(ext, "attachment")
        name = _cut_to_fit(os.path.basename(filepath) or filepath)

        self.layout = QtWidgets.QHBoxLayout(self)
        self.layout.setContentsMargins(8, 4, 8, 4)
        self.layout.setSpacing(6)

        icon_lbl = QtWidgets.QLabel()
        icon_lbl.setPixmap(UIHelpers.get_icon(icon_name, size=14, on=icon_lbl).pixmap(14, 14))
        self.layout.addWidget(icon_lbl)

        self.lbl = QtWidgets.QLabel(name)
        self.lbl.setStyleSheet("font-size: 11px;")
        self.layout.addWidget(self.lbl)

        self.btn = QtWidgets.QPushButton()
        self.btn.setIcon(UIHelpers.get_icon("close", size=12, on=self.btn))
        self.btn.setFixedSize(16, 16)
        self.btn.setStyleSheet("""
            QPushButton {
                border: none;
                background: transparent;
                font-size: 14px;
                font-weight: bold;
                padding: 0px;
                min-width: 0px;
                margin-top: -2px;
            }
        """)
        self.btn.clicked.connect(lambda: self.remove_requested.emit(self.filepath))
        self.btn.setVisible(False)
        self.btn.setEnabled(removable)
        if removable:
            self.layout.addWidget(self.btn)
        else:
            self.btn.setParent(None)

        self.setStyleSheet("""
            FileChip {
                border-radius: 12px;
                background: rgba(127, 127, 127, 40);
            }
        """)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(filepath)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.filepath))

    def enterEvent(self, event):
        self.btn.setVisible(self._removable)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.btn.setVisible(False)
        super().leaveEvent(event)


class FileChipBar(QtWidgets.QWidget):
    """
    Horizontal strip of FileChip widgets shown above the input field.
    Hides itself when no files are attached.
    """

    file_removed = QtCore.Signal(str)

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
        chip = FileChip(filepath)
        chip.remove_requested.connect(self.remove_file)
        insert_pos = self._layout.count() - 1
        self._layout.insertWidget(insert_pos, chip)
        self._files.append(filepath)
        self.setVisible(True)

    def remove_file(self, filepath: str):
        """The user's own removal — the file is not wanted, so its owner may drop it."""
        if self._take(filepath):
            self.file_removed.emit(filepath)

    def get_and_clear(self) -> list[str]:
        """Handing the files to a run is not removing them; the run still needs them."""
        files = list(self._files)
        for filepath in files:
            self._take(filepath)
        return files

    def _take(self, filepath: str) -> bool:
        if filepath not in self._files:
            return False
        self._files.remove(filepath)
        for i in range(self._layout.count() - 1):
            item = self._layout.itemAt(i)
            if item and item.widget() and getattr(item.widget(), "filepath", None) == filepath:
                w = self._layout.takeAt(i).widget()
                w.deleteLater()
                break
        if not self._files:
            self.setVisible(False)
        return True

    def files(self) -> list[str]:
        """What is on the bar, without taking it off."""
        return list(self._files)


class UploadWidget(QtWidgets.QWidget):
    """
    A minimal "+" icon button that opens a file dialog.
    Chips are rendered by the FileChipBar (owned by copilot_panel).

    It is the dense control height, not the full one the header uses: it sits
    in the composer's tool strip beside the model pill, and a row of controls
    that are two different heights reads as a misalignment rather than as a
    hierarchy.

    Its width is the icon plus one 4, not the square the height would make it.
    A square hit target puts 4 px of empty button either side of a 16 px glyph
    that has nothing beside it to balance, and the strip reads as a gap before
    the pickers rather than as a control. The height is the token, because that
    is what the row aligns on; the width is not, because nothing aligns on it.
    """

    file_attached = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.upload_btn = QtWidgets.QPushButton()
        self.upload_btn.setIcon(UIHelpers.get_icon("plus", size=ICON, on=self.upload_btn))
        self.upload_btn.setFixedSize(ICON + SPACE_XS, CONTROL_DENSE)
        self.upload_btn.setToolTip("Attach a drawing — an image or a PDF")
        self.upload_btn.setStyleSheet(
            "QPushButton { border: none; background: transparent; padding: 0px;"
            " min-width: 0px; border-radius: %dpx; }"
            "QPushButton:hover { background-color: %s; }" % (RADIUS_CONTROL, WASH))
        self.upload_btn.clicked.connect(self.open_file_dialog)
        layout.addWidget(self.upload_btn)

        self.temporary: list[str] = []

    def showEvent(self, event):
        super().showEvent(event)
        self.upload_btn.setIcon(UIHelpers.get_icon("plus", size=ICON, on=self.upload_btn))

    def open_file_dialog(self):
        """
        Offers PDFs as well as images because they are accepted: the chip icon
        map dresses a PDF as attachable.
        """
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Attach a drawing", "",
            "Drawings (*.png *.jpg *.jpeg *.gif *.webp *.pdf);;All (*)"
        )
        for f in files:
            self.file_attached.emit(f)
            from bridge.logger import logger
            logger.info(f"[Copilot] Attached file: {f}")

    def handle_clipboard_paste(self) -> bool:
        """
        Call this from the QTextEdit's keyPressEvent (Ctrl+V).
        Returns True if an image was found and handled (suppress default paste).

        The pixmap is built with fromImage rather than QPixmap(image): Qt5 has
        no QImage constructor, PySide2 takes the call anyway and hands back a
        null pixmap, and save() then writes that as a blank.
        """
        clipboard = QtWidgets.QApplication.clipboard()
        mime = clipboard.mimeData()

        if mime.hasImage():
            pix = QtGui.QPixmap.fromImage(clipboard.image())
            if pix.isNull():
                from bridge.logger import logger
                logger.warning("[Copilot] The clipboard offered an image that did not convert")
                return False
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            pix.save(tmp.name, "PNG")
            tmp.close()
            self.temporary.append(tmp.name)
            self.file_attached.emit(tmp.name)
            from bridge.logger import logger
            logger.info(f"[Copilot] Clipboard image saved to: {tmp.name}")
            return True
        return False

    def discard(self, *paths: str):
        """Delete those of these we wrote. A file the user attached is theirs, not ours."""
        for path in paths:
            if path not in self.temporary:
                continue
            self.temporary.remove(path)
            try:
                os.remove(path)
            except OSError:
                pass  # except-pass-allowed: already removed from tracking, the file itself may be gone

    def discard_temporary(self):
        """Every clipboard image still unaccounted for — the panel is closing."""
        self.discard(*self.temporary)
