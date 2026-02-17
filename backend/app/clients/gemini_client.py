"""
Google Gemini client — LLM wrapper for sync report generation.
Version: 1.0.0
"""
import logging

import google.generativeai as genai

logger = logging.getLogger(__name__)


class GeminiClient:
    """Thin synchronous wrapper around the Google Generative AI SDK."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model)
        self._model_name = model
        logger.info(f"GeminiClient initialised with model={model}")

    def generate_content(self, prompt: str) -> str:
        """Generate text content from a prompt (synchronous).

        Args:
            prompt: The full prompt to send to Gemini.

        Returns:
            The generated text response.
        """
        response = self._model.generate_content(prompt)
        return response.text
