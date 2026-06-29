"""Core helpers and types for manifest.

Protocol for plugins that contribute manifest source files.

Plugins that host porringer configuration inside their own ecosystem
files (e.g. ``pyproject.toml``, ``package.json``)
implement ``ManifestContributor`` so the manifest discovery engine
can probe those files automatically.

The pattern mirrors ``RuntimeProvider`` / ``RuntimeConsumer`` in
`porringer.core.plugin_schema.runtime` — a ``@runtime_checkable``
protocol with a single classmethod.
"""

from typing import Protocol, runtime_checkable

from porringer.core.schema import ManifestContribution


@runtime_checkable
class ManifestContributor(Protocol):
    """A plugin that declares a file it can host porringer configuration in.

    The sync engine checks ``isinstance(plugin_cls, ManifestContributor)``
    during manifest discovery and calls ``manifest_contribution()`` to
    learn which file to probe in a given directory.

    Return ``None`` to opt out (the default for plugins that don't
    contribute manifest sources).
    """

    @classmethod
    def manifest_contribution(cls) -> ManifestContribution | None:
        """Return the manifest contribution this plugin provides, or ``None``.

        The returned ``ManifestContribution`` tells the discovery engine:

        * Which **filename** to look for (e.g. ``'pyproject.toml'``).
        * The **config path** within the parsed file where porringer
          configuration lives (e.g. ``('tool', 'porringer')``).
        * The **file format** to use for parsing (``'toml'`` or ``'json'``).

        The discovered section may contain either:

        * A full inline manifest (parsed as ``SetupManifest``), or
        * A ``manifest = "relative/path.json"`` reference that
          redirects to an external manifest file.

        Returns:
            A ``ManifestContribution`` describing the hosted config, or
            ``None`` if this plugin does not contribute a manifest source.
        """
        ...
