"""Co-pilot chat — real-time LLM channel parallel to the workflow."""

from .responder import ChatResponderError, respond_to_user_message

__all__ = ["ChatResponderError", "respond_to_user_message"]
