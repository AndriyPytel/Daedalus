"""
Markdown parser to extract Python code blocks
"""
import re


class MarkdownParser:
    """Parser for extracting code blocks from Markdown text."""
    
    def __init__(self):
        """Initialize the parser."""
        # Pattern to match Python code blocks: ```python ... ```
        self.code_block_pattern = re.compile(
            r'```python\s*\n(.*?)```',
            re.DOTALL | re.MULTILINE
        )
    
    def extract_code_blocks(self, markdown_text):
        """Extract all Python code blocks from markdown text.
        
        Args:
            markdown_text: The full markdown text from AI
            
        Returns:
            list: List of code block strings (without the ```python wrapper)
        """
        matches = self.code_block_pattern.findall(markdown_text)
        # Strip leading/trailing whitespace from each block
        return [match.strip() for match in matches]
    
    def remove_code_blocks(self, markdown_text):
        """Remove code blocks from markdown, leaving only text.
        
        Args:
            markdown_text: The full markdown text
            
        Returns:
            str: Markdown text with code blocks removed
        """
        return self.code_block_pattern.sub('', markdown_text).strip()
