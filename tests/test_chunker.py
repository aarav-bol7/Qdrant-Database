from unittest.mock import patch

import pytest

from apps.ingestion.chunker import (
    CHUNK_CONFIG,
    MAX_CHUNK_TOKENS,
    MIN_CHUNK_CHARS,
    chunk_item,
)


def _fake_token_count(text: str) -> int:
    return max(1, len(text) // 4)


@pytest.fixture(autouse=True)
def mock_count_tokens():
    with patch("apps.ingestion.chunker.count_tokens", side_effect=_fake_token_count):
        yield


class TestChunkItemBasic:
    def test_empty_content_returns_empty_list(self):
        assert chunk_item("", source_type="pdf", item_index=0) == []

    def test_whitespace_only_returns_empty_list(self):
        assert chunk_item("   \n\t  ", source_type="pdf", item_index=0) == []

    def test_short_content_returns_single_chunk(self):
        text = "Tiny."
        chunks = chunk_item(text, source_type="pdf", item_index=0)
        assert len(chunks) == 1
        assert chunks[0].text == "Tiny."
        assert chunks[0].chunk_index == 0

    def test_chunk_indices_are_sequential(self):
        text = "Sentence one. " * 500
        chunks = chunk_item(text, source_type="pdf", item_index=0)
        assert len(chunks) > 1
        for i, c in enumerate(chunks):
            assert c.chunk_index == i


class TestSourceTypeRouting:
    @pytest.mark.parametrize("source_type", list(CHUNK_CONFIG.keys()))
    def test_known_source_types_use_their_config(self, source_type):
        text = "x " * 5000
        chunks = chunk_item(text, source_type=source_type, item_index=0)
        assert len(chunks) > 0

    def test_unknown_source_type_uses_default(self):
        text = "x " * 5000
        chunks = chunk_item(text, source_type="unknown_type", item_index=0)
        assert len(chunks) > 0


class TestSizeLimits:
    def test_no_chunk_exceeds_max_tokens(self):
        text = ("Long sentence. " * 2000).strip()
        chunks = chunk_item(text, source_type="pdf", item_index=0)
        for c in chunks:
            assert c.token_count <= MAX_CHUNK_TOKENS

    def test_tiny_final_chunk_is_merged(self):
        text = "Big chunk content. " * 100 + " End."
        chunks = chunk_item(text, source_type="pdf", item_index=0)
        for c in chunks[:-1]:
            assert c.char_count >= MIN_CHUNK_CHARS or c is chunks[0]
