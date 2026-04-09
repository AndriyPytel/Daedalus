# FreeCadCopilot/core/rag_service.py
import os
from utils.settings_manager import SettingsManager

class RAGService:
    def __init__(self):
        self.doc_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "api")
        self.full_context = None
        
    def get_full_context(self) -> str:
        if self.full_context is not None:
            return self.full_context
            
        context_parts = []
        if os.path.exists(self.doc_dir):
            for filename in os.listdir(self.doc_dir):
                if filename.endswith(".md"):
                    file_path = os.path.join(self.doc_dir, filename)
                    with open(file_path, "r", encoding="utf-8") as f:
                        context_parts.append(f.read())
                        
        self.full_context = "\n\n".join(context_parts)
        return self.full_context

# Singleton wrapper
_instance = None
def get_rag_service():
    global _instance
    if _instance is None:
        _instance = RAGService()
    return _instance
