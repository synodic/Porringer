"""Helpers for strategies.

Shared Hypothesis strategies for property-based tests.

These strategies generate :class:`~porringer.core.schema.PackageRef`
inputs — both well-formed and adversarial — so invariants can be
asserted over a broad input space instead of a handful of hand-picked
examples.  Keeping them in one module lets every property test draw
from the same generators.
"""

import string

from hypothesis import strategies as st

from porringer.core.schema import PackageRef

# Constraint strings spanning PEP 440 and npm-family grammars.  The
# constraint field is stored verbatim, so the exact spelling is
# irrelevant to the invariants — only that it is non-empty and varied.
_CONSTRAINTS = (
    '>=1.0',
    '==2.3.4',
    '<3',
    '>=2,<3',
    '~=1.4',
    '!=1.2',
    '^4.0.0',
    '~18.0',
    'latest',
)

# Names that must never escape a single argv token nor crash the
# builders.  Each encodes a shell-injection, flag-injection, or
# whitespace-splitting attempt that a naive string-concatenating
# command builder would mishandle.
ADVERSARIAL_NAMES = (
    'pkg; rm -rf /',
    'pkg && evil',
    'pkg | tee out',
    'pkg $(whoami)',
    'pkg `id`',
    '--config=evil',
    '-rf',
    'a b c',
    'pkg\nrm',
    'pkg\trm',
    "pkg' OR '1'='1",
    'pkg"quote',
    '../../etc/passwd',
    'pkg%00null',
)


@st.composite
def simple_names(draw: st.DrawFn) -> str:
    """Generate a conservative, single-token package name.

    Produces names from the intersection of what the PEP 440 and
    npm parsers accept (leading alphanumeric, then alphanumerics and
    ``-_.``), so the result is a stable, splittable-free identifier.

    Args:
        draw: Hypothesis draw callable.

    Returns:
        A package name such as ``"ruff"`` or ``"my-pkg.2"``.
    """
    first = draw(st.sampled_from(string.ascii_lowercase + string.digits))
    rest = draw(st.text(alphabet=string.ascii_lowercase + string.digits + '-_.', max_size=20))
    return first + rest


@st.composite
def scoped_names(draw: st.DrawFn) -> str:
    """Generate an npm-style ``@scope/name`` package name.

    Args:
        draw: Hypothesis draw callable.

    Returns:
        A scoped name such as ``"@types/node"``.
    """
    scope = draw(simple_names())
    name = draw(simple_names())
    return f'@{scope}/{name}'


# PEP 440 operator constraints that attach directly to a name to form a
# specifier the parser can round-trip (npm-family operators such as
# ``latest`` do not concatenate cleanly and are excluded here).
_PEP440_CONSTRAINTS = (
    '>=1.0',
    '==2.3.4',
    '<3',
    '>=2,<3',
    '~=1.4',
    '!=1.2',
)


def constraints() -> st.SearchStrategy[str]:
    """Strategy over representative constraint strings.

    Returns:
        A strategy yielding non-empty constraint strings.
    """
    return st.sampled_from(_CONSTRAINTS)


@st.composite
def parseable_spec_strings(draw: st.DrawFn) -> str:
    """Generate a specifier string guaranteed to parse via PEP 440.

    Used to probe parse idempotence: feeding the result through
    :meth:`PackageRef.model_validate` and re-rendering must reach a
    fixed point.

    Args:
        draw: Hypothesis draw callable.

    Returns:
        A specifier string such as ``"ruff>=1.0"`` or a bare name.
    """
    name = draw(simple_names())
    constraint = draw(st.none() | st.sampled_from(_PEP440_CONSTRAINTS))
    return name if constraint is None else f'{name}{constraint}'


def adversarial_names() -> st.SearchStrategy[str]:
    """Strategy over names that probe injection and splitting bugs.

    Returns:
        A strategy yielding hostile package-name strings.
    """
    return st.sampled_from(ADVERSARIAL_NAMES)


@st.composite
def package_refs(draw: st.DrawFn, *, allow_adversarial: bool = False) -> PackageRef:
    """Generate a :class:`PackageRef` via direct field construction.

    Direct construction (rather than string coercion) lets the
    generator place arbitrary content in ``name`` so that argv-safety
    invariants can be probed independently of the parser.

    Args:
        draw: Hypothesis draw callable.
        allow_adversarial: When ``True``, names may include hostile
            strings from :data:`ADVERSARIAL_NAMES`.

    Returns:
        A constructed package reference.
    """
    name_strategy = st.one_of(simple_names(), scoped_names())
    if allow_adversarial:
        name_strategy = st.one_of(name_strategy, adversarial_names())
    name = draw(name_strategy)
    constraint = draw(st.none() | constraints())
    extras = draw(st.lists(simple_names(), max_size=3, unique=True).map(tuple))
    return PackageRef(name=name, extras=extras, constraint=constraint)
