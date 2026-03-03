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
        """Empty specifiers raise validation errors."""
        with pytest.raises((ValueError, ValidationError)):
            PackageRef.model_validate('')

    @staticmethod
    def test_invalid_scoped_specifier() -> None:
        """Scoped package without a slash raises validation errors."""
        with pytest.raises((ValueError, ValidationError)):
            PackageRef.model_validate('@no-slash')

    @staticmethod
    def test_preserves_name_casing() -> None:
        """Model validation preserves original casing."""
        ref = PackageRef.model_validate('My-Package>=1.0')
        assert ref.name == 'My-Package'


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


class TestPackageRefRoundTrip:
    """Tests for serialization / deserialization round-trips."""

    @staticmethod
    def test_dict_round_trip() -> None:
        """Model dumps and restores via dict."""
        ref = PackageRef(name='ruff', constraint='>=0.8.0')
        data = ref.model_dump()
        assert data == {'name': 'ruff', 'extras': (), 'constraint': '>=0.8.0'}
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
        assert restored.extras == ref.extras
        assert restored.constraint == ref.constraint


class TestPackageRefNpmStyle:
    """Tests for npm / non-PEP-440 specifier handling."""

    @staticmethod
    def test_bare_npm_name() -> None:
        """Bare npm package name is accepted."""
        ref = PackageRef.model_validate('typescript')
        assert ref.name == 'typescript'
        assert ref.constraint is None

    @staticmethod
    def test_scoped_name() -> None:
        """Scoped npm package name is accepted."""
        ref = PackageRef.model_validate('@types/node')
        assert ref.name == '@types/node'
        assert ref.constraint is None

    @staticmethod
    def test_name_at_constraint() -> None:
        """name@constraint syntax is parsed."""
        ref = PackageRef.model_validate('lodash@^4.0.0')
        assert ref.name == 'lodash'
        assert ref.constraint == '^4.0.0'

    @staticmethod
    def test_scoped_name_at_constraint() -> None:
        """@scope/name@constraint syntax is parsed."""
        ref = PackageRef.model_validate('@angular/core@~18.0')
        assert ref.name == '@angular/core'
        assert ref.constraint == '~18.0'

    @staticmethod
    def test_scoped_name_at_latest() -> None:
        """@scope/name@latest syntax is parsed."""
        ref = PackageRef.model_validate('@types/node@latest')
        assert ref.name == '@types/node'
        assert ref.constraint == 'latest'

    @staticmethod
    def test_name_at_latest() -> None:
        """name@latest syntax is parsed."""
        ref = PackageRef.model_validate('typescript@latest')
        assert ref.name == 'typescript'
        assert ref.constraint == 'latest'

    @staticmethod
    def test_pep440_still_works() -> None:
        """PEP 440 specifiers still go through the PEP 440 path."""
        ref = PackageRef.model_validate('ruff>=0.8.0')
        assert ref.name == 'ruff'
        assert ref.constraint == '>=0.8.0'

    @staticmethod
    def test_specifier_property_at_syntax() -> None:
        """Specifier joins name + constraint for npm-style refs."""
        ref = PackageRef(name='lodash', constraint='^4.0.0')
        assert ref.specifier == 'lodash^4.0.0'

    @staticmethod
    def test_non_pep440_name_accepted() -> None:
        """Names that are invalid PEP 508 but valid in other ecosystems are accepted."""
        ref = PackageRef.model_validate('!!!special!!!')
        assert ref.name == '!!!special!!!'
        assert ref.constraint is None


class TestPackageRefExtras:
    """Tests for PEP 508 extras bracket syntax."""

    @staticmethod
    def test_extras_from_string() -> None:
        """Extras are captured from bracket syntax."""
        ref = PackageRef.model_validate('cppython[cmake,conan,git]')
        assert ref.name == 'cppython'
        assert ref.extras == ('cmake', 'conan', 'git')
        assert ref.constraint is None

    @staticmethod
    def test_extras_with_constraint() -> None:
        """Extras and version constraint are both captured."""
        ref = PackageRef.model_validate('cppython[cmake,conan]>=1.0')
        assert ref.name == 'cppython'
        assert ref.extras == ('cmake', 'conan')
        assert ref.constraint == '>=1.0'

    @staticmethod
    def test_single_extra() -> None:
        """Single extra is captured as a one-element tuple."""
        ref = PackageRef.model_validate('pkg[one]')
        assert ref.extras == ('one',)

    @staticmethod
    def test_no_extras_default() -> None:
        """Bare name has empty extras tuple."""
        ref = PackageRef(name='ruff')
        assert ref.extras == ()

    @staticmethod
    def test_no_extras_from_string() -> None:
        """String without brackets has empty extras tuple."""
        ref = PackageRef.model_validate('ruff>=0.8.0')
        assert ref.extras == ()

    @staticmethod
    def test_explicit_construction() -> None:
        """Extras can be provided via explicit construction."""
        ref = PackageRef(name='pkg', extras=('a', 'b'))
        assert ref.extras == ('a', 'b')

    @staticmethod
    def test_specifier_with_extras() -> None:
        """Specifier includes extras brackets."""
        ref = PackageRef.model_validate('cppython[cmake,conan,git]')
        assert ref.specifier == 'cppython[cmake,conan,git]'

    @staticmethod
    def test_specifier_extras_and_constraint() -> None:
        """Specifier includes extras and constraint."""
        ref = PackageRef.model_validate('cppython[cmake,conan]>=1.0')
        assert ref.specifier == 'cppython[cmake,conan]>=1.0'

    @staticmethod
    def test_str_includes_extras() -> None:
        """String conversion includes extras."""
        ref = PackageRef.model_validate('cppython[cmake,conan,git]')
        assert str(ref) == 'cppython[cmake,conan,git]'

    @staticmethod
    def test_extras_sorted_deterministically() -> None:
        """Extras are sorted regardless of input order."""
        ref_a = PackageRef.model_validate('pkg[b,a,c]')
        ref_b = PackageRef.model_validate('pkg[c,a,b]')
        assert ref_a.extras == ('a', 'b', 'c')
        assert ref_a == ref_b

    @staticmethod
    def test_extras_dict_round_trip() -> None:
        """Model with extras dumps and restores via dict."""
        ref = PackageRef.model_validate('cppython[cmake,conan,git]')
        data = ref.model_dump()
        assert data == {'name': 'cppython', 'extras': ('cmake', 'conan', 'git'), 'constraint': None}
        restored = PackageRef.model_validate(data)
        assert restored == ref

    @staticmethod
    def test_extras_json_round_trip() -> None:
        """Model with extras dumps and restores via JSON."""
        ref = PackageRef.model_validate('cppython[cmake,conan,git]>=1.0')
        json_str = ref.model_dump_json()
        restored = PackageRef.model_validate_json(json_str)
        assert restored == ref
        assert restored.extras == ('cmake', 'conan', 'git')

    @staticmethod
    def test_npm_style_no_extras() -> None:
        """npm-style specifiers produce empty extras (not a PEP 508 concept)."""
        ref = PackageRef.model_validate('lodash@^4.0.0')
        assert ref.extras == ()
