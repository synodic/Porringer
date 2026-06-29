"""Helpers for test package ref invariants.

Property-based invariants for :class:`~porringer.core.schema.PackageRef`.

These tests assert *relationships that hold for all inputs* rather than
checking captured example values.  Each property is its own oracle, so
nothing here goes stale when an external tool changes.
"""

from hypothesis import given
from hypothesis import strategies as st

from porringer.core.schema import PackageRef
from tests.fixtures.strategies import (
    adversarial_names,
    package_refs,
    parseable_spec_strings,
)

_STYLES = ('pep440', 'at', 'equals')


class TestSpecifierSeparatorSafety:
    """``specifier_for`` must never invent a separator out of nothing."""

    @staticmethod
    @given(name=st.one_of(adversarial_names(), st.text(min_size=1, max_size=40)))
    def test_no_constraint_renders_bare_name(name: str) -> None:
        """With no constraint, every style renders exactly the name.

        A spurious ``=`` / ``@`` separator here would change which
        package a wrapped tool receives, so the absence of a constraint
        must produce the bare name verbatim.
        """
        ref = PackageRef(name=name)
        for style in _STYLES:
            assert ref.specifier_for(style) == name
        assert ref.specifier == name

    @staticmethod
    @given(ref=package_refs(allow_adversarial=True))
    def test_rendering_never_raises(ref: PackageRef) -> None:
        """All rendering helpers are total over every constructable ref."""
        assert isinstance(ref.specifier, str)
        assert isinstance(str(ref), str)
        for style in _STYLES:
            assert isinstance(ref.specifier_for(style), str)


class TestNameConfinement:
    """A name must remain a single, intact token through rendering."""

    @staticmethod
    @given(name=adversarial_names())
    def test_adversarial_name_is_preserved_verbatim(name: str) -> None:
        """Rendering a hostile name neither escapes, splits, nor mutates it.

        The package layer builds ``list`` argv (never a shell string),
        so confinement here means the exact name survives as one piece —
        the plugin layer then places that piece in a single argv slot.
        """
        ref = PackageRef(name=name)
        assert ref.specifier == name
        assert str(ref) == name


class TestParseIdempotence:
    """Parsing must reach a fixed point in a single step."""

    @staticmethod
    @given(spec=parseable_spec_strings())
    def test_reparse_is_stable(spec: str) -> None:
        """``parse(str(parse(s))) == parse(s)`` for well-formed specs.

        Normalisation (e.g. specifier sorting) may change a string on
        the *first* parse; it must not keep changing on the second.
        """
        first = PackageRef.model_validate(spec)
        second = PackageRef.model_validate(str(first))
        assert second == first


class TestSerializationRoundTrip:
    """Model serialization and validation are inverse operations."""

    @staticmethod
    @given(ref=package_refs(allow_adversarial=True))
    def test_dict_round_trip(ref: PackageRef) -> None:
        """A ref survives a ``model_dump`` / ``model_validate`` cycle."""
        assert PackageRef.model_validate(ref.model_dump()) == ref

    @staticmethod
    @given(ref=package_refs(allow_adversarial=True))
    def test_json_round_trip(ref: PackageRef) -> None:
        """A ref survives a JSON serialization round-trip."""
        assert PackageRef.model_validate_json(ref.model_dump_json()) == ref
