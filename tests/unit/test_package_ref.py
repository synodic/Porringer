"""Tests for PackageRef model."""

import pytest
from pydantic import ValidationError

from porringer.core.schema import PackageRef


class TestPackageRefConstruction:
    """Tests for constructing PackageRef instances."""

    @staticmethod
    def test_bare_name() -> None:
        ref = PackageRef(name='ruff')
        assert ref.name == 'ruff'
        assert ref.constraint is None

    @staticmethod
    def test_name_with_constraint() -> None:
        ref = PackageRef(name='ruff', constraint='>=0.8.0')
        assert ref.name == 'ruff'
        assert ref.constraint == '>=0.8.0'

    @staticmethod
    def test_compound_constraint() -> None:
        ref = PackageRef(name='pydantic', constraint='>=2,<3')
        assert ref.name == 'pydantic'
        assert ref.constraint == '>=2,<3'


class TestPackageRefStringCoercion:
    """Tests for the model_validator that accepts plain strings."""

    @staticmethod
    def test_bare_string() -> None:
        ref = PackageRef.model_validate('pytest')
        assert ref.name == 'pytest'
        assert ref.constraint is None

    @staticmethod
    def test_string_with_version() -> None:
        ref = PackageRef.model_validate('ruff>=0.8.0')
        assert ref.name == 'ruff'
        assert ref.constraint == '>=0.8.0'

    @staticmethod
    def test_string_exact_version() -> None:
        ref = PackageRef.model_validate('requests==2.31.0')
        assert ref.name == 'requests'
        assert ref.constraint == '==2.31.0'

    @staticmethod
    def test_string_upper_bound() -> None:
        ref = PackageRef.model_validate('flask<3')
        assert ref.name == 'flask'
        assert ref.constraint == '<3'

    @staticmethod
    def test_string_compound() -> None:
        ref = PackageRef.model_validate('pydantic>=2,<3')
        assert ref.name == 'pydantic'
        # packaging sorts specifier components
        assert ref.constraint == '<3,>=2'

    @staticmethod
    def test_invalid_specifier() -> None:
        with pytest.raises((ValueError, ValidationError)):
            PackageRef.model_validate('!!!invalid!!!')


class TestPackageRefModelValidate:
    """Tests for model_validate (replaces the removed parse() classmethod)."""

    @staticmethod
    def test_validate_bare() -> None:
        ref = PackageRef.model_validate('black')
        assert ref.name == 'black'
        assert ref.constraint is None

    @staticmethod
    def test_validate_with_constraint() -> None:
        ref = PackageRef.model_validate('ruff>=0.8.0')
        assert ref.name == 'ruff'
        assert ref.constraint == '>=0.8.0'

    @staticmethod
    def test_validate_preserves_name_casing() -> None:
        """packaging.requirements.Requirement preserves original casing."""
        ref = PackageRef.model_validate('My-Package>=1.0')
        assert ref.name == 'My-Package'

    @staticmethod
    def test_validate_invalid() -> None:
        with pytest.raises((ValueError, ValidationError)):
            PackageRef.model_validate('!!!bad!!!')


class TestPackageRefSpecifier:
    """Tests for the specifier property and __str__."""

    @staticmethod
    def test_specifier_bare() -> None:
        ref = PackageRef(name='pytest')
        assert ref.specifier == 'pytest'

    @staticmethod
    def test_specifier_with_constraint() -> None:
        ref = PackageRef(name='ruff', constraint='>=0.8.0')
        assert ref.specifier == 'ruff>=0.8.0'

    @staticmethod
    def test_str_matches_specifier() -> None:
        ref = PackageRef(name='ruff', constraint='>=0.8.0')
        assert str(ref) == ref.specifier

    @staticmethod
    def test_str_bare() -> None:
        ref = PackageRef(name='pytest')
        assert str(ref) == 'pytest'


class TestPackageRefFrozen:
    """Tests that PackageRef is immutable."""

    @staticmethod
    def test_cannot_set_name() -> None:
        ref = PackageRef(name='ruff')
        with pytest.raises(ValidationError):
            ref.name = 'other'  # type: ignore[misc]

    @staticmethod
    def test_cannot_set_constraint() -> None:
        ref = PackageRef(name='ruff')
        with pytest.raises(ValidationError):
            ref.constraint = '>=1.0'  # type: ignore[misc]


class TestPackageRefRoundTrip:
    """Tests for serialization / deserialization round-trips."""

    @staticmethod
    def test_dict_round_trip() -> None:
        ref = PackageRef(name='ruff', constraint='>=0.8.0')
        data = ref.model_dump()
        assert data == {'name': 'ruff', 'constraint': '>=0.8.0'}
        restored = PackageRef.model_validate(data)
        assert restored.name == ref.name
        assert restored.constraint == ref.constraint

    @staticmethod
    def test_json_round_trip() -> None:
        ref = PackageRef(name='pytest')
        json_str = ref.model_dump_json()
        restored = PackageRef.model_validate_json(json_str)
        assert restored.name == ref.name
        assert restored.constraint == ref.constraint
