"""Errors raised across the application boundary."""


class ApplicationError(Exception):
    """Base class for application-level failures."""


class GatewayError(ApplicationError):
    """An external data source failed (network, blocked request, changed format...)."""
