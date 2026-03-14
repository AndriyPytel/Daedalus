# FreeCadCopilot/ui/upload_widget.py
from PySide.QtGui import QWidget, QHBoxLayout, QPushButton, QFileDialog, QLabel
import FreeCAD

class UploadWidget(QWidget):
    """
    A widget to manage file uploads for RAG context or Vision (PDFs, Images).
    """
    def __init__(self):
        super().__init__()
        layout = QHBoxLayout()
        layout.setContentsMargins(0,0,0,0)
        self.setLayout(layout)

        self.upload_btn = QPushButton("📎 Attach File")
        self.upload_btn.clicked.connect(self.open_file_dialog)
        self.upload_btn.setToolTip("Attach images or PDFs to provide more context.")
        
        self.files_label = QLabel("0 files")
        
        layout.addWidget(self.upload_btn)
        layout.addWidget(self.files_label)
        layout.addStretch()
        
        self.attached_files = []

    def open_file_dialog(self):
        options = QFileDialog.Options()
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Attach Context Files",
            "",
            "Supported Files (*.pdf *.png *.jpg *.jpeg);;PDF Files (*.pdf);;Images (*.png *.jpg *.jpeg);;All Files (*)",
            options=options
        )
        if files:
            self.attached_files.extend(files)
            self.files_label.setText(f"{len(self.attached_files)} files")
            FreeCAD.Console.PrintMessage(f"[Copilot] Attached {len(files)} files for Context/Vision.\n")

    def get_and_clear_files(self):
        files = list(self.attached_files)
        self.attached_files.clear()
        self.files_label.setText("0 files")
        return files
