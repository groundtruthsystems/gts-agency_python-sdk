"""Tests for session vault DTOs — Classification StrEnum semantics (PR #5 L1)."""

from enum import StrEnum

from agency_sdk.delegates.session_vault_dto import Classification


def test_classification_is_a_str_enum():
    assert issubclass(Classification, StrEnum)


def test_members_equal_their_string_values():
    assert Classification.PUBLIC == "public"
    assert Classification.INTERNAL == "internal"
    assert Classification.CONFIDENTIAL == "confidential"
    assert Classification.RESTRICTED == "restricted"


def test_str_yields_bare_value():
    # Query-param serialization relies on str() being "public", not "Classification.PUBLIC".
    assert str(Classification.PUBLIC) == "public"
    assert f"{Classification.INTERNAL}" == "internal"


def test_default_is_an_alias_of_restricted():
    assert Classification.DEFAULT is Classification.RESTRICTED
    assert Classification.DEFAULT == "restricted"
