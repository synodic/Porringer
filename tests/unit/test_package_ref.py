"""Tests for PackageRef model."""

import pytest
from pydantic import ValidationError

from porringer.core.schema import PackageRef


class TestPackageRefConstruction:
    """Tests for constructing PackageRef instances."""

    @staticmethod
    def test_bare_name() -> None:
        """Bare name sets constraint to None."""
        ref = PackageRef(name='ruff')
        assert ref.name == 'ruff'
        assert ref.constraint is None

    @staticmethod
    def test_name_with_constraint() -> None:
        """Explicit constraint is preserved."""
        ref = PackageRef(name='ruff', constraint='>=0.8.0')
        assert ref.name == 'ruff'
        assert ref.constraint == '>=0.8.0'

    @staticmethod
    def test_compound_constraint() -> None:
        """Compound constraint is accepted."""
        ref = PackageRef(name='pydantic', constraint='>=2,<3')
        assert ref.name == 'pydantic'
        assert ref.constraint == '>=2,<3'


class TestPackageRefStringCoercion:
    """Tests for the model_validator that accepts plain strings."""

    @staticmethod
    def test_bare_string() -> None:
        """Bare string parses to name only."""
        ref = PackageRef.model_validate('pytest')
        assert ref.name == 'pytest'
        assert ref.constraint is None

    @staticmethod
    def test_string_with_version() -> None:
        """Version constraint is parsed from string."""
        ref = PackageRef.model_validate('ruff>=0.8.0')
        assert ref.name == 'ruff'
        assert ref.constraint == '>=0.8.0'

    @staticmethod
    def test_string_exact_version() -> None:
        """Exact version constraint is parsed."""
        ref = PackageRef.model_validate('requests==2.31.0')
        assert ref.name == 'requests'
        assert ref.constraint == '==2.31.0'

    @staticmethod
    def test_string_upper_bound() -> None:
        """Upper bound constraint is parsed."""
        ref = PackageRef.model_validate('flask<3')
        assert ref.name == 'flask'
        assert ref.constraint == '<3'

    @staticmethod
    def test_string_compound() -> None:
        """Compound constraints are normalized by packaging."""
        ref = PackageRef.model_validate('pydantic>=2,<3')
        assert ref.name == 'pydantic'
        # packaging sorts specifier components
        assert ref.constraint == '<3,>=2'

    @staticmethod
    def test_invalid_specifier() -> None:
        """Invalid specifiers raise validation errors."""
        with pytest.raises((ValueError, ValidationError)):
            PackageRef.model_validate('!!!invalid!!!')


class TestPackageRefModelValidate:
    """Tests for model_validate (replaces the removed parse() classmethod)."""

    @staticmethod
    def test_validate_bare() -> None:
        """Model validation accepts bare names."""
        ref = PackageRef.model_validate('black')
        assert ref.name == 'black'
        assert ref.constraint is None

    @staticmethod
    def test_validate_with_constraint() -> None:
        """Model validation accepts constraints."""
        ref = PackageRef.model_validate('ruff>=0.8.0')
        assert ref.name == 'ruff'
        assert ref.constraint == '>=0.8.0'

    @staticmethod
    def test_validate_preserves_name_casing() -> None:
        """Model validation preserves original casing."""
        ref = PackageRef.model_validate('My-Package>=1.0')
        assert ref.name == 'My-Package'

    @staticmethod
    def test_validate_invalid() -> None:
        """Invalid package strings raise errors."""
        with pytest.raises((ValueError, ValidationError)):
            PackageRef.model_validate('!!!bad!!!')


class TestPackageRefSpecifier:
    """Tests for the specifier property and __str__."""

    @staticmethod
    def test_specifier_bare() -> None:
        """Specifier equals name when no constraint."""
        ref = PackageRef(name='pytest')
        assert ref.specifier == 'pytest'

    @staticmethod
    def test_specifier_with_constraint() -> None:
        """Specifier includes constraint when provided."""
        ref = PackageRef(name='ruff', constraint='>=0.8.0')
        assert ref.specifier == 'ruff>=0.8.0'

    @staticmethod
    def test_str_matches_specifier() -> None:
        """String conversion matches specifier."""
        ref = PackageRef(name='ruff', constraint='>=0.8.0')
        assert str(ref) == ref.specifier

    @staticmethod
    def test_str_bare() -> None:
        """String conversion returns bare name."""
        ref = PackageRef(name='pytest')
        assert str(ref) == 'pytest'


class TestPackageRefFrozen:
    """Tests that PackageRef is immutable."""

    @staticmethod
    def test_cannot_set_name() -> None:
        """Name attribute is immutable."""
        ref = PackageRef(name='ruff')
        with pytest.raises(ValidationError):
            ref.name = 'other'  # type: ignore[misc]

    @staticmethod
    def test_cannot_set_constraint() -> None:
        """Constraint attribute is immutable."""
        ref = PackageRef(name='ruff')
        with pytest.raises(ValidationError):
            ref.constraint = '>=1.0'  # type: ignore[misc]


class TestPackageRefRoundTrip:
    """Tests for serialization / deserialization round-trips."""

    @staticmethod
    def test_dict_round_trip() -> None:
        """Model dumps and restores via dict."""
        ref = PackageRef(name='ruff', constraint='>=0.8.0')
        data = ref.model_dump()
        assert data == {'name': 'ruff', 'constraint': '>=0.8.0'}
        restored = PackageRef.model_validate(data)
        assert restored.name == ref.name
        assert restored.constraint == ref.constraint

    @staticmethod
    def test_json_round_trip() -> None:
        """Model dumps and restores via JSON."""
        ref = PackageRef(name='pytest')
        json_str = ref.model_dump_json()
        restored = PackageRef.model_validate_json(json_str)
        assert restored.name == ref.name
        assert restored.constraint == ref.constraint
