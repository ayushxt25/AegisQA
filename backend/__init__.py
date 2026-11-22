"""Phase 1 backend foundation for AegisQA."""

def create_app(*args, **kwargs):
    """Lazily import the API factory so child processes do not load the API."""
    from backend.app import create_app as factory
    return factory(*args, **kwargs)


__all__ = ("create_app",)
