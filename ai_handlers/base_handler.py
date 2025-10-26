"""
Base abstract class for AI handlers
"""
from abc import ABC, abstractmethod


class BaseHandler(ABC):
    """Abstract base class for AI model handlers."""
    
    def __init__(self, api_key=None, api_endpoint=None, system_prompt=None):
        """Initialize the handler.
        
        Args:
            api_key: API key for the service
            api_endpoint: API endpoint URL
            system_prompt: System prompt to use
        """
        self.api_key = api_key
        self.api_endpoint = api_endpoint
        self.system_prompt = system_prompt or ""
    
    @abstractmethod
    def generate_response(self, user_prompt):
        """Generate a response from the AI model.
        
        Args:
            user_prompt: User's input prompt
            
        Returns:
            str: The AI's response (Markdown format)
            
        Raises:
            Exception: If the request fails
        """
        pass
    
    def set_system_prompt(self, prompt):
        """Set the system prompt.
        
        Args:
            prompt: The system prompt text
        """
        self.system_prompt = prompt
    
    def set_api_key(self, api_key):
        """Set the API key.
        
        Args:
            api_key: The API key
        """
        self.api_key = api_key
