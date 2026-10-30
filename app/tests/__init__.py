"""Test support. Its helpers assert as tests do, so pytest rewrites their asserts too."""

import pytest

pytest.register_assert_rewrite("app.tests.support")
