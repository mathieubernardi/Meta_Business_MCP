"""Tests des outils audiences (normalisation, hachage, garde-fou)."""

from __future__ import annotations

import hashlib

import pytest

from meta_mcp.client import MetaClient, WritesDisabledError
from meta_mcp.tools.audiences import normalize_and_hash


def test_email_is_lowercased_and_hashed():
    digest = normalize_and_hash("  Mat.Bernardi@Gmail.COM ", "EMAIL")
    expected = hashlib.sha256(b"mat.bernardi@gmail.com").hexdigest()
    assert digest == expected


def test_phone_keeps_digits_only():
    digest = normalize_and_hash("+33 (6) 12-34-56-78", "PHONE")
    expected = hashlib.sha256(b"33612345678").hexdigest()
    assert digest == expected


def test_name_is_normalized():
    assert normalize_and_hash("Mat ", "FN") == normalize_and_hash("mat", "FN")


def test_hash_is_deterministic_and_irreversible():
    digest = normalize_and_hash("test@example.com", "EMAIL")
    assert digest == normalize_and_hash("test@example.com", "EMAIL")
    assert len(digest) == 64
    assert "test@example.com" not in digest


async def test_audience_writes_are_blocked_by_default():
    client = MetaClient("fake-token")
    with pytest.raises(WritesDisabledError):
        client.require_writes("add_users_to_audience")
    await client.aclose()
