"""Exception definitions"""


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


class ManifestError(SetupError):
    """Raised when there is an error with the setup manifest"""

    pass


class CommandTimeoutError(SetupError):
    """Raised when a post-install command exceeds the allowed timeout"""

    pass


class PrerequisiteError(SetupError):
    """Raised when a prerequisite plugin is not available"""

    pass


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
