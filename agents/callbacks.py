# FreeCadCopilot/agents/callbacks.py
from langchain_core.callbacks.base import BaseCallbackHandler

class UIStreamCallbackHandler(BaseCallbackHandler):
    """
    A Langchain callback handler that receives novel tokens from the LLM
    and passes them to a provided custom callback function.
    """
    def __init__(self, callback_func):
        super().__init__()
        self.callback_func = callback_func

    def on_llm_new_token(self, token: str, **kwargs) -> None:
        if self.callback_func:
            self.callback_func(token)
