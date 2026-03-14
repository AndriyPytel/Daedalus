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
        Returns FreeCAD API Documentation snippets (Sketcher, PartDesign, App/Gui) 
        and Current Codebase State.
        """
        # Mocking specific doc retrieval
        api_docs = "FreeCAD API: Use App.ActiveDocument to get the document. Use Part and PartDesign for geometries."
        return f"API Documentation:\n{api_docs}"
        
    @staticmethod
    def get_validator_context(state: dict) -> str:
        """
        Returns Python standards and FreeCAD-specific error patterns.
        """
        standards = "Ensure code imports FreeCAD. Do not use generic OS subprocess functions unless necessary."
        return f"Validation Standards:\n{standards}"
