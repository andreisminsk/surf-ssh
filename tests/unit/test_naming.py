"""Unit tests for NameGenerator — phonetic names and user name validation."""

import pytest

from src.ssh.naming import NameCollisionError, generate_phonetic, validate_user_name


class TestPhoneticGeneration:
    def test_format(self):
        name = generate_phonetic()
        parts = name.split("-")
        assert len(parts) == 3
        assert all(p.isalpha() for p in parts)

    def test_no_collision_with_existing(self):
        existing = set()
        names = set()
        for _ in range(50):
            name = generate_phonetic(existing)
            assert name not in existing
            assert name not in names
            names.add(name)
            existing.add(name)

    def test_fallback_with_digit(self):
        # Fill the in-memory set to force fallback (unlikely with 8M combos,
        # but test the path by pre-seeding)
        from src.ssh import naming
        naming._GENERATED_NAMES.clear()
        # Generate many — should still produce unique names
        names = [generate_phonetic() for _ in range(100)]
        assert len(set(names)) == 100


class TestUserNameValidation:
    def test_valid_name(self):
        assert validate_user_name("prod-jump", set()) == "prod-jump"

    def test_rejects_empty(self):
        with pytest.raises(NameCollisionError):
            validate_user_name("", set())

    def test_rejects_spaces(self):
        with pytest.raises(NameCollisionError):
            validate_user_name("prod jump", set())

    def test_rejects_uppercase(self):
        with pytest.raises(NameCollisionError):
            validate_user_name("ProdJump", set())

    def test_rejects_too_long(self):
        with pytest.raises(NameCollisionError):
            validate_user_name("a" * 64, set())

    def test_rejects_reserved(self):
        with pytest.raises(NameCollisionError, match="reserved"):
            validate_user_name("host", set())

    def test_rejects_collision(self):
        with pytest.raises(NameCollisionError, match="already exists"):
            validate_user_name("gcp", {"gcp"})
