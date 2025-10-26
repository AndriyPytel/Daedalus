"""
Google Gemini AI handler
"""
import requests
from .base_handler import BaseHandler


class GeminiHandler(BaseHandler):
    """Handler for Google Gemini API."""
    
    def __init__(self, api_key=None, api_endpoint=None, system_prompt=None):
        """Initialize Gemini handler.
        
        Args:
            api_key: Google API key
            api_endpoint: API endpoint (optional, defaults to Gemini)
            system_prompt: System prompt to use
        """
        super().__init__(api_key, api_endpoint, system_prompt)
        
        # Default endpoint
        if not self.api_endpoint:
            self.api_endpoint = "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent"
    
    def generate_response(self, user_prompt):
        """Generate a response using Gemini API.
        
        Args:
            user_prompt: User's input prompt
            
        Returns:
            str: The AI's response (Markdown format)
            
        Raises:
            Exception: If the request fails
        """
        if not self.api_key:
            raise ValueError("API key not set")
        
        # Combine system prompt and user prompt
        full_prompt = f"{self.system_prompt}\n\nUser: {user_prompt}\n\nAssistant:"
        
        # Prepare request
        url = f"{self.api_endpoint}?key={self.api_key}"
        payload = {
            "contents": [{
                "parts": [{
                    "text": full_prompt
                }]
            }]
        }
        
        # TODO: Make actual API call
        # For now, return a mock response
        return self._mock_response(user_prompt)
    
    def _mock_response(self, user_prompt):
        """Return a mock response for testing.
        
        Args:
            user_prompt: User's prompt
            
        Returns:
            str: Mock markdown response
        """
        return f"""I understand you want to: {user_prompt}

Here's how we can do that in FreeCAD:

```python
import Part
# Create a test box
box = Part.makeBox(10, 10, 10)
Part.show(box)
```

Then we can modify it:

```python
# Get the box
box_obj = App.ActiveDocument.getObject("Box")
box_obj.Placement.Base = App.Vector(5, 5, 5)
App.ActiveDocument.recompute()
```

Is this what you were looking for?
"""
