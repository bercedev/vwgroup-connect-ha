# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A constructor argument that looked like brand support and was never read.

@bzykcar (#1805) asked a direct question: is there any sign-in path left that
would give an Audi a BFF token? Answering it meant reading the strategy chain,
and on the way through, ``WebsiteAuthProxyConnector`` turned out to accept a
``brand`` argument. The runtime passed the entry's **real** brand into it:

    connector = WebsiteAuthProxyConnector(
        session, self._email, self._password, brand=self._brand.name,
    )

``self._brand = brand`` was the only line that ever touched it. Nothing read it
again, anywhere. The connector is volkswagen.de in every path it has, so an
Audi entry routed through it would have reached the Volkswagen site and signed
in successfully — and anyone wiring Audi through this as a "lever" would have
watched that work and concluded the lever existed.

That is worse than having no parameter. A missing parameter is a question you
have to answer; a dead one is an answer you can believe. Both it and the dead
``self._aiohttp_session`` store beside it are gone.

The test below is deliberately not "assert there is no brand argument". That
would pin the symptom and let the next dead knob through. It pins the shape:
**an __init__ parameter copied onto self must be read somewhere in the class.**
That invariant is what the brand argument violated, and it is what would have
caught it the day it was added.

Scope note: this checks the constructors of the auth connectors, not every
class in the integration. These are the classes a brand is plumbed through, so
they are where a dead knob reads as capability. Widening it is cheap if it ever
earns its place; pretending it is already repository-wide would not be.
"""
from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest

from custom_components.vag_connect.cariad.auth._http2 import H2Session
from custom_components.vag_connect.cariad.auth._website_authproxy import (
    WebsiteAuthProxyConnector,
)

# Attributes that are legitimately written and never read, with the reason.
# Every entry here is a hole in the check above, so each one has to say why it
# is not the bug this file exists to catch.
_ALLOWED_WRITE_ONLY: dict[tuple[str, str], str] = {
    # _canaries.py explains this: the marker exists so a copy of the module
    # carries provenance. Being inert is the whole point, and it is not
    # assigned from a constructor parameter, so the check below never sees it.
}


def _init_of(cls: type) -> tuple[ast.FunctionDef, ast.AST]:
    """The class's own ``__init__`` node plus the parsed class body."""
    lines = inspect.getsource(cls).splitlines()
    # Re-indent so the class body parses standalone regardless of nesting.
    tree = ast.parse("class _Probe:\n" + "\n".join("    " + ln for ln in lines[1:]))
    probe = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "_Probe"
    )
    init = next(
        n for n in probe.body
        if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    )
    return init, tree


def _params_copied_to_self(init: ast.FunctionDef) -> dict[str, str]:
    """``{attribute: parameter}`` for every bare ``self._x = param``."""
    params = {a.arg for a in init.args.args} | {a.arg for a in init.args.kwonlyargs}
    params.discard("self")
    out: dict[str, str] = {}
    for node in ast.walk(init):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            continue
        if isinstance(node.value, ast.Name) and node.value.id in params:
            out[target.attr] = node.value.id
    return out


def _attributes_read(tree: ast.AST) -> set[str]:
    """Every ``self.x`` the class actually READS.

    Counted from the syntax tree rather than by searching the source text. The
    first version of this test counted occurrences of the string ``self._x``,
    and the paragraph in ``_website_authproxy.py`` explaining why the dead
    store was removed *mentions the attribute by name* — so the comment
    registered as a reader and the mutation check caught nothing. A gate that
    a comment can satisfy is not a gate.
    """
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and isinstance(node.ctx, ast.Load)
    }


# The auth connectors a brand is plumbed through.
_CONNECTORS = [WebsiteAuthProxyConnector, H2Session]


@pytest.mark.parametrize("cls", _CONNECTORS, ids=lambda c: c.__name__)
def test_no_constructor_argument_is_stored_and_never_read(cls: type) -> None:
    """The invariant the brand argument broke.

    If this fails, the question to answer is not "how do I silence it" but
    "does the caller believe this parameter does something?" — because the
    runtime was passing a real, meaningful value into one that did nothing.
    """
    init, tree = _init_of(cls)
    read = _attributes_read(tree)
    dead: list[str] = []
    for attr, param in _params_copied_to_self(init).items():
        if (cls.__name__, attr) in _ALLOWED_WRITE_ONLY:
            continue
        if attr not in read:
            dead.append(f"self.{attr} (from parameter {param!r})")
    assert not dead, (
        f"{cls.__name__}: assigned from a constructor parameter and never read: "
        + ", ".join(dead)
        + ". A parameter the caller fills in and the class ignores advertises "
        "behaviour that does not exist."
    )


def test_the_website_connector_takes_no_brand() -> None:
    """The specific case, kept alongside the general one.

    The general test above would also catch a re-added ``brand`` — but only if
    it were stored. A ``brand`` argument that is accepted and dropped on the
    floor entirely is even quieter, and this catches that too.
    """
    params = inspect.signature(WebsiteAuthProxyConnector.__init__).parameters
    assert "brand" not in params, (
        "the website authproxy is volkswagen.de for every caller; a brand "
        "argument here reads as multi-brand support (see #1805)"
    )
    assert list(params) == ["self", "session", "email", "password"]


def test_the_connector_is_constructible_without_a_brand() -> None:
    """The call sites were changed too, not just the signature."""
    conn = WebsiteAuthProxyConnector(object(), "u@example.com", "pw")  # type: ignore[arg-type]
    assert not hasattr(conn, "_brand")
    assert not hasattr(conn, "_aiohttp_session")
    assert conn.logged_in is False


def test_no_caller_still_passes_a_brand() -> None:
    """Grep-level guard: a stale keyword argument would be a TypeError at
    runtime, on a path only reachable with a real login, so it would not show
    up in the suite until a user hit it."""
    import pathlib

    import custom_components.vag_connect as pkg

    offenders: list[str] = []
    for path in pathlib.Path(pkg.__file__).parent.rglob("*.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines):
            if "WebsiteAuthProxyConnector(" not in line:
                continue
            # the call's argument list: this line plus the next few, since the
            # construction is wrapped across lines at every call site.
            call = "\n".join(lines[idx:idx + 5])
            head = call.split(")", 1)[0]
            if "brand=" in head:
                offenders.append(f"{path.name}:{idx + 1}")
    assert not offenders, f"brand= still passed to the connector at {offenders}"


def test_the_h2_wrapper_still_holds_the_session() -> None:
    """Why dropping ``self._aiohttp_session`` was safe rather than tidy.

    The removed store was not keeping the aiohttp session alive: the wrapper
    keeps its own strong reference. If that ever stops being true, the session
    could be collected under a live connector, and this is the test that should
    fail first.
    """
    sentinel: Any = object()
    wrapper = H2Session(sentinel)
    held = [v for v in vars(wrapper).values() if v is sentinel]
    assert held, "H2Session no longer holds a reference to the session it wraps"
