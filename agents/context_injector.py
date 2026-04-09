# FreeCadCopilot/agents/context_injector.py

class ContextInjector:
    """
    Service responsible for injecting specific contextual data 
    into the state for each specialized agent, replacing a generic RAG.
    """
    
    @staticmethod
    def get_designer_context(state: dict) -> str:
        """
        Returns Initial Requirements (user prompt, uploaded PDFs/Images) 
        and the Global History of the current session.
        """
        history = state.get("history", [])
        # History is expected to be a list of langchain messages, but we can do a naive string conversion for now
        history_str = "\n".join([f"{msg.type}: {msg.content}" for msg in history]) if history else "No history."
        
        attachments = state.get("attachments", [])
        attach_str = f"Attachments: {len(attachments)} files provided." if attachments else "No attachments."
        
        return f"History:\n{history_str}\n\nContext:\n{attach_str}"
        
    @staticmethod
    def get_developer_context(state: dict) -> str:
        """
        Returns FreeCAD API Documentation snippets retrieved from the RAG store.
        """
        try:
            from core.rag_service import get_rag_service
            rag_docs = get_rag_service().get_full_context()
            if not rag_docs:
                rag_docs = "FreeCAD API: Prefer PartDesign and Sketcher. Always use doc=App.ActiveDocument."
        except Exception as e:
            rag_docs = f"FreeCAD API Base Rules (RAG Failed: {e})"
            
        ctx = f"RAG API Documentation Snippets:\n{rag_docs}"
        
        if state.get("feedback"):
            ctx += f"\n\nCRITICAL FIX REQUIRED:\nYour previous code generated the following error:\n{state['feedback']}\nPlease fix this error in your response."
            
        return ctx
        
    @staticmethod
    def get_validator_context(state: dict) -> str:
        """
        Returns Python standards and FreeCAD-specific error patterns.
        """
        standards = "Ensure code imports FreeCAD. Do not use generic OS subprocess functions unless necessary."
        return f"Validation Standards:\n{standards}"
