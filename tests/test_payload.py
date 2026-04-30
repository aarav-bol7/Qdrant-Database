import datetime

from apps.ingestion.chunker import Chunk
from apps.ingestion.payload import (
    ScrapedItem,
    ScrapedSource,
    build_chunk_id,
    build_payload,
)


def test_build_chunk_id_format():
    assert build_chunk_id("doc-abc", 0, 0) == "doc-abc__i0__c0"
    assert build_chunk_id("doc-xyz", 3, 12) == "doc-xyz__i3__c12"


def _make_chunk(text: str = "hello world") -> Chunk:
    return Chunk(text=text, chunk_index=0, char_count=len(text), token_count=2)


def _make_source(**overrides) -> ScrapedSource:
    return ScrapedSource(
        type=overrides.get("type", "pdf"),
        filename=overrides.get("filename", "doc.pdf"),
        url=overrides.get("url"),
        content_hash=overrides.get("content_hash", "sha256:abc"),
    )


def _make_item(**overrides) -> ScrapedItem:
    return ScrapedItem(
        item_index=overrides.get("item_index", 0),
        section_path=overrides.get("section_path", ["Top", "Section A"]),
        page_number=overrides.get("page_number", 1),
    )


class TestBuildPayload:
    def test_required_fields_present(self):
        chunk = _make_chunk()
        p = build_payload(
            chunk,
            tenant_id="pizzapalace",
            bot_id="supportv1",
            doc_id="doc-abc",
            item=_make_item(),
            source=_make_source(),
        )
        assert p["tenant_id"] == "pizzapalace"
        assert p["bot_id"] == "supportv1"
        assert p["doc_id"] == "doc-abc"
        assert p["chunk_id"] == "doc-abc__i0__c0"
        assert p["version"] == 1
        assert p["is_active"] is True
        assert p["text"] == "hello world"
        assert p["char_count"] == 11
        assert p["token_count"] == 2

    def test_uploaded_at_is_iso8601(self):
        p = build_payload(
            _make_chunk(),
            tenant_id="t",
            bot_id="b",
            doc_id="d",
            item=_make_item(),
            source=_make_source(),
        )
        datetime.datetime.fromisoformat(p["uploaded_at"])

    def test_source_url_uses_source_only(self):
        p = build_payload(
            _make_chunk(),
            tenant_id="t",
            bot_id="b",
            doc_id="d",
            item=_make_item(),
            source=_make_source(url="https://example.com/doc.pdf"),
        )
        assert p["source_url"] == "https://example.com/doc.pdf"

    def test_source_url_none_when_source_url_missing(self):
        p = build_payload(
            _make_chunk(),
            tenant_id="t",
            bot_id="b",
            doc_id="d",
            item=_make_item(),
            source=_make_source(url=None),
        )
        assert p["source_url"] is None

    def test_dropped_fields_not_in_payload(self):
        p = build_payload(
            _make_chunk(),
            tenant_id="t",
            bot_id="b",
            doc_id="d",
            item=_make_item(),
            source=_make_source(),
        )
        for k in ("section_title", "category", "tags"):
            assert k not in p, f"{k} should not be in slim payload"

    def test_section_path_is_list_copy(self):
        path = ["A", "B", "C"]
        p = build_payload(
            _make_chunk(),
            tenant_id="t",
            bot_id="b",
            doc_id="d",
            item=_make_item(section_path=path),
            source=_make_source(),
        )
        assert p["section_path"] == path
        path.append("D")
        assert p["section_path"] == ["A", "B", "C"]

    def test_chunk_id_from_payload_matches_helper(self):
        p = build_payload(
            _make_chunk(),
            tenant_id="t",
            bot_id="b",
            doc_id="myDocId",
            item=_make_item(item_index=4),
            source=_make_source(),
        )
        assert p["chunk_id"] == build_chunk_id("myDocId", 4, 0)
