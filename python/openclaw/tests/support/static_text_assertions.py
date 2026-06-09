from __future__ import annotations

import unittest


def assert_static_text_absent(
    case: unittest.TestCase,
    token: str,
    content: str,
    *,
    msg: str | None = None,
) -> None:
    """Assert that a static source/config/doc text surface does not embed a token."""
    if token in content:
        case.fail(msg or f'static text unexpectedly contains {token!r}')
