"""Domain-level errors."""


class DomainError(Exception):
    """Base class for every rule violation inside the domain."""


class InvalidValueError(DomainError, ValueError):
    """A value object or entity was built with invalid data."""


class CurrencyMismatchError(DomainError):
    """Two amounts in different currencies were combined or compared."""
