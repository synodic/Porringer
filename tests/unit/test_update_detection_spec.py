"""Helpers for test update detection spec.

Tests for manifest-level PluginSpec parsing and per-plugin prerelease opt-in.
"""

from porringer.backend.command.core.action_builder import build_actions
from porringer.core.schema import Ecosystem
from porringer.schema import SetupManifest
from porringer.schema.manifest import PackageSpec, PluginSpec


class TestPluginSpec:
    """Verify that PluginSpec allows per-plugin include_prereleases in manifests."""

    @staticmethod
    def test_string_shorthand() -> None:
        """A plain string coerces to PluginSpec with defaults."""
        spec = PluginSpec.model_validate('cppython')
        assert spec.name.name == 'cppython'
        assert spec.include_prereleases is False
        assert spec.description is None

    @staticmethod
    def test_object_form_with_prereleases() -> None:
        """An object with include_prereleases=true is parsed correctly."""
        spec = PluginSpec.model_validate({'name': 'cppython', 'include_prereleases': True})
        assert spec.name.name == 'cppython'
        assert spec.include_prereleases is True

    @staticmethod
    def test_object_form_with_constraint() -> None:
        """An object with a versioned name is parsed correctly."""
        spec = PluginSpec.model_validate({'name': 'cppython>=1.0'})
        assert spec.name.name == 'cppython'
        assert spec.name.constraint == '>=1.0'
        assert spec.include_prereleases is False

    @staticmethod
    def test_package_spec_plugins_accepts_mixed() -> None:
        """PackageSpec.plugins accepts a mix of strings and objects."""
        spec = PackageSpec.model_validate({
            'name': 'pdm',
            'plugins': [
                'cppython',
                {'name': 'another-plugin', 'include_prereleases': True},
            ],
        })
        expected_plugin_count = 2
        assert len(spec.plugins) == expected_plugin_count
        assert spec.plugins[0].name.name == 'cppython'
        assert spec.plugins[0].include_prereleases is False
        assert spec.plugins[1].name.name == 'another-plugin'
        assert spec.plugins[1].include_prereleases is True

    @staticmethod
    def test_plugin_prereleases_independent_of_parent() -> None:
        """Plugin include_prereleases does not inherit from the parent PackageSpec."""
        spec = PackageSpec.model_validate({
            'name': 'pdm',
            'include_prereleases': True,
            'plugins': ['cppython'],
        })
        # Parent has include_prereleases=True, but the plugin string shorthand defaults to False
        assert spec.include_prereleases is True
        assert spec.plugins[0].include_prereleases is False

    @staticmethod
    def test_action_builder_uses_plugin_prereleases() -> None:
        """build_actions reads include_prereleases from the PluginSpec, not the parent."""
        manifest = SetupManifest(
            tools={
                Ecosystem('python'): [
                    {
                        'name': 'pdm',
                        'include_prereleases': False,
                        'plugins': [{'name': 'cppython', 'include_prereleases': True}],
                    }
                ]
            }
        )
        actions = build_actions(manifest, {})

        parent = next(a for a in actions if a.plugin_target is None and str(a.package) == 'pdm')
        plugin = next(a for a in actions if a.plugin_target is not None and str(a.package) == 'cppython')

        # The plugin opts into prereleases even though the parent package does not.
        assert parent.include_prereleases is False
        assert plugin.include_prereleases is True
