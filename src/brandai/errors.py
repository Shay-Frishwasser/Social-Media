class BrandAIError(Exception):
    """Base exception for the BrandAI domain."""


class BrandIsolationError(BrandAIError):
    """Raised when data or a path crosses a brand boundary."""


class GenerationBlockedError(BrandAIError):
    """Raised when approved brand state is missing."""


class InvalidStateTransitionError(BrandAIError):
    """Raised when an immutable version transition is not allowed."""


class NotFoundError(BrandAIError):
    """Raised when a requested domain object does not exist."""

