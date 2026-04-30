import hashlib
import struct

from apps.tenants.validators import validate_slug


def collection_name(tenant_id: str, bot_id: str) -> str:
    """Derive a Qdrant collection name from a (tenant_id, bot_id) pair.

    This is the SOLE constructor of collection-name strings in the codebase.
    """
    validate_slug(tenant_id, field_name="tenant_id")
    validate_slug(bot_id, field_name="bot_id")
    return f"t_{tenant_id}__b_{bot_id}"


def advisory_lock_key(tenant_id: str, bot_id: str, doc_id: str) -> tuple[int, int]:
    """Derive a (key1, key2) pair for pg_advisory_lock(int, int).

    Deterministic across processes / hosts. 64-bit namespace.
    """
    validate_slug(tenant_id, field_name="tenant_id")
    validate_slug(bot_id, field_name="bot_id")
    if not isinstance(doc_id, str) or not doc_id:
        raise ValueError(f"doc_id must be a non-empty string, got {doc_id!r}")

    digest = hashlib.sha256(f"{tenant_id}|{bot_id}|{doc_id}".encode()).digest()
    key1, key2 = struct.unpack(">ii", digest[:8])
    return key1, key2
