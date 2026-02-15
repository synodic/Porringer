"""Exception definitions"""

from __future__ import annotations

from enum import Enum


class PorringerError(Exception):
    """Base class for all Porringer exceptions.

    Provides a common error property for accessing the error message.
    """

    def __init__(self, error: str) -> None:
        """Initializes the error.

        Args:
            error: The error message
        """
        self._error = error
        super().__init__(error)

    @property
    def error(self) -> str:
        """Returns the underlying error.

        Returns:
            The underlying error message
        """
        return self._error


class ProcessError(PorringerError):
    """Raised when there is a configuration error"""


class PluginError(PorringerError):
    """Raised when there is a plugin error"""


class NotSupportedError(PorringerError):
    """Raised when something is not supported"""


class SetupError(PorringerError):
    """Base class for setup-related errors"""


class ManifestErrorCode(Enum):
    """Machine-readable code carried by `ManifestError`.

    Allows downstream code to classify errors structurally instead of
    relying on substring matching against the human-readable message.
    """

    NO_MANIFEST = 'no_manifest'
    SYNTAX_ERROR = 'syntax_error'
    LOAD_FAILED = 'load_failed'
    SCHEMA_INVALID = 'schema_invalid'


class ManifestError(SetupError):
    """Raised when there is an error with the setup manifest.

    This includes errors such as:
    - Invalid JSON/TOML syntax
    - Missing required fields
    - Invalid package specifications

    Carries an optional `code` for structured error classification.
    """

    def __init__(self, error: str, *, code: ManifestErrorCode | None = None) -> None:
        """Initializes the error.

        Args:
            error: Human-readable error message.
            code: Optional machine-readable code for this error.
        """
        super().__init__(error)
        self._code = code

    @property
    def code(self) -> ManifestErrorCode | None:
        """Return the machine-readable error code, if set."""
        return self._code


class CommandTimeoutError(SetupError):
    """Raised when a post-install command exceeds the allowed timeout.

    This exception is raised when a command specified in the manifest
    runs longer than the configured timeout duration.
    """


class PluginDependencyError(PluginError):
    """Raised when a required plugin dependency is not available"""

    def __init__(self, plugin: str, dependency: str, error: str | None = None) -> None:
        """Initializes the error

        Args:
            plugin: The plugin that has the unmet dependency
            dependency: The name of the missing dependency plugin
            error: Optional additional error message
        """
        self._plugin = plugin
        self._dependency = dependency

        message = f"Plugin '{plugin}' requires '{dependency}' but it is not available"
        if error:
            message = f'{message}: {error}'

        super().__init__(message)

    @property
    def plugin(self) -> str:
        """Returns the plugin that has the unmet dependency

        Returns:
            str -- The plugin name
        """
        return self._plugin

    @property
    def dependency(self) -> str:
        """Returns the missing dependency plugin name

        Returns:
            str -- The dependency plugin name
        """
        return self._dependency


class UpdateError(PorringerError):
    """Raised when there is an error checking for or downloading updates"""
