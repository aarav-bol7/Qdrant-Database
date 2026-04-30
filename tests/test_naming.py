import pathlib
import re

import pytest

from apps.qdrant_core.naming import advisory_lock_key, collection_name
from apps.tenants.validators import InvalidIdentifierError


class TestCollectionName:
    def test_happy_path(self):
        assert collection_name("pizzapalace", "supportv1") == "t_pizzapalace__b_supportv1"

    def test_underscores_allowed(self):
        assert collection_name("a_b_c", "x_y_z") == "t_a_b_c__b_x_y_z"

    @pytest.mark.parametrize(
        "tenant_id,bot_id",
        [
            ("", "supportv1"),
            ("pizzapalace", ""),
            ("Pizza", "supportv1"),
            ("pizza-palace", "supportv1"),
            ("_pizza", "supportv1"),
            ("ab", "supportv1"),
            ("a" * 41, "supportv1"),
            ("pizzapalace", "1"),
            (None, "supportv1"),
        ],
    )
    def test_invalid_inputs_rejected(self, tenant_id, bot_id):
        with pytest.raises(InvalidIdentifierError):
            collection_name(tenant_id, bot_id)

    def test_max_length_within_column_size(self):
        long_t = "a" * 40
        long_b = "b" * 40
        result = collection_name(long_t, long_b)
        assert len(result) <= 100


class TestAdvisoryLockKey:
    def test_deterministic(self):
        a = advisory_lock_key("pizzapalace", "supportv1", "doc-uuid-123")
        b = advisory_lock_key("pizzapalace", "supportv1", "doc-uuid-123")
        assert a == b

    def test_returns_two_int32s(self):
        k1, k2 = advisory_lock_key("pizzapalace", "supportv1", "doc-uuid-123")
        assert isinstance(k1, int) and isinstance(k2, int)
        assert -(2**31) <= k1 < 2**31
        assert -(2**31) <= k2 < 2**31

    def test_different_inputs_different_keys(self):
        a = advisory_lock_key("pizzapalace", "supportv1", "doc-1")
        b = advisory_lock_key("pizzapalace", "supportv1", "doc-2")
        assert a != b

    def test_invalid_slug_rejected(self):
        with pytest.raises(InvalidIdentifierError):
            advisory_lock_key("Pizza", "supportv1", "doc-1")

    def test_empty_doc_id_rejected(self):
        with pytest.raises(ValueError):
            advisory_lock_key("pizzapalace", "supportv1", "")


class TestNoOtherCollectionNameConstructors:
    """Enforce: only apps/qdrant_core/naming.py constructs collection name strings.

    Any other code path that has a literal `t_..._b_...` f-string pattern is
    a violation of the guard rule.
    """

    PATTERN = re.compile(r'f"t_.*__b_')

    def test_grep_codebase_for_unauthorized_constructors(self):
        repo_root = pathlib.Path(__file__).resolve().parents[1]
        offending: list[str] = []
        for root in (repo_root / "apps", repo_root / "config"):
            for path in root.rglob("*.py"):
                rel = path.relative_to(repo_root)
                if rel.parts[:2] == ("apps", "qdrant_core") and rel.name == "naming.py":
                    continue
                text = path.read_text(encoding="utf-8")
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if self.PATTERN.search(line):
                        offending.append(f"{rel}:{lineno}: {line.strip()}")
        assert offending == [], (
            "Unauthorized collection-name construction outside naming.py:\n" + "\n".join(offending)
        )
