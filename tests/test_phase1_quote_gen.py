"""
Tests for pipeline/quote_gen.py (Phase 1).
All Ollama calls are mocked — tests run offline.
"""
import json
import math
import sqlite3
from unittest.mock import patch, MagicMock

import pytest

from pipeline.quote_gen import (
    _cosine,
    _is_duplicate,
    _parse_quotes,
    generate_batch,
)


# ── _cosine ───────────────────────────────────────────────────────────────────

class TestCosine:
    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert _cosine(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert _cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_zero_vector_returns_zero(self):
        assert _cosine([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_similar_vectors_high_score(self):
        a = [1.0, 1.0, 0.0]
        b = [1.0, 0.9, 0.1]
        assert _cosine(a, b) > 0.99


# ── _is_duplicate ─────────────────────────────────────────────────────────────

class TestIsDuplicate:
    def _corpus(self, vecs):
        return [(i, v) for i, v in enumerate(vecs)]

    def test_empty_corpus_never_duplicate(self):
        assert not _is_duplicate([1.0, 0.0], [], threshold=0.92)

    def test_identical_is_duplicate(self):
        v = [1.0, 0.0]
        assert _is_duplicate(v, self._corpus([v]), threshold=0.92)

    def test_orthogonal_not_duplicate(self):
        assert not _is_duplicate([1.0, 0.0], self._corpus([[0.0, 1.0]]), threshold=0.92)

    def test_threshold_boundary(self):
        # Similarity just below threshold — not duplicate
        a = [1.0, 0.0, 0.0]
        b = [0.91, 0.41, 0.0]  # cosine ~0.91
        b_norm = [x / math.sqrt(sum(x**2 for x in b)) for x in b]
        sim = _cosine(a, b_norm)
        if sim > 0.92:
            assert _is_duplicate(a, self._corpus([b_norm]), threshold=0.92)
        else:
            assert not _is_duplicate(a, self._corpus([b_norm]), threshold=0.92)

    def test_checks_all_corpus_entries(self):
        v_new = [1.0, 0.0]
        corpus = [
            (0, [0.0, 1.0]),   # orthogonal
            (1, [1.0, 0.0]),   # identical
        ]
        assert _is_duplicate(v_new, corpus, threshold=0.92)


# ── _parse_quotes ─────────────────────────────────────────────────────────────

class TestParseQuotes:
    def test_basic_parsing(self):
        raw = "Line one quote here.\nLine two quote here.\nLine three here now."
        result = _parse_quotes(raw)
        assert len(result) == 3

    def test_strips_quotes_marks(self):
        # Single-quoted lines: strip only leading/trailing quote chars
        raw = '"This is a longer quote line here."\n\'Another longer one right here.\''
        result = _parse_quotes(raw)
        assert len(result) == 2
        assert result[0] == "This is a longer quote line here."
        assert result[1] == "Another longer one right here."

    def test_filters_short_lines(self):
        raw = "OK\nThis is a real quote with enough words.\nBad"
        result = _parse_quotes(raw)
        assert len(result) == 1
        assert "real quote" in result[0]

    def test_skips_empty_lines(self):
        raw = "\nFirst real quote line here.\n\nSecond real quote line here.\n"
        result = _parse_quotes(raw)
        assert len(result) == 2

    def test_returns_empty_on_blank_input(self):
        assert _parse_quotes("") == []
        assert _parse_quotes("\n\n\n") == []


# ── generate_batch ────────────────────────────────────────────────────────────

class TestGenerateBatch:
    """Mock Ollama; verify DB writes and dedup logic."""

    FAKE_QUOTES = "\n".join([
        "The sun rises on those who dare to try.",
        "Every failure is a lesson in disguise.",
        "Your courage defines your destiny ahead.",
        "Push beyond the limits you have set yourself.",
        "Believe that tomorrow holds better things.",
    ])

    def _make_db(self):
        from pathlib import Path
        schema = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()
        conn = sqlite3.connect(":memory:")
        conn.executescript(schema)
        return conn

    def _distinct_embeddings(self):
        """Return side_effect list: each call returns a distinct orthogonal-ish embedding."""
        import random
        embeddings = []
        for i in range(20):
            emb = [0.0] * 768
            emb[i * 10] = 1.0  # one hot in different dimension → cosine = 0
            embeddings.append(emb)
        return embeddings

    @patch("pipeline.quote_gen._embed")
    @patch("pipeline.quote_gen._ollama_generate")
    def test_stores_accepted_quotes(self, mock_gen, mock_embed):
        mock_gen.return_value = self.FAKE_QUOTES
        mock_embed.side_effect = self._distinct_embeddings()

        conn = self._make_db()
        stats = generate_batch(conn, model="llama3.1:8b", count=5)

        assert stats["accepted"] == 5
        assert stats["rejected_semantic"] == 0
        count = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        assert count == 5

    @patch("pipeline.quote_gen._embed")
    @patch("pipeline.quote_gen._ollama_generate")
    def test_rejects_exact_duplicate(self, mock_gen, mock_embed):
        mock_gen.return_value = self.FAKE_QUOTES
        # First batch: distinct embeddings so all accepted
        # Second batch: same text → exact duplicate check fires before embed
        mock_embed.side_effect = self._distinct_embeddings()

        conn = self._make_db()
        generate_batch(conn, model="llama3.1:8b", count=5)

        # Second batch: same quotes → all rejected as exact duplicates (no embed called)
        stats = generate_batch(conn, model="llama3.1:8b", count=5)
        assert stats["rejected_exact"] == 5
        assert stats["accepted"] == 0

    @patch("pipeline.quote_gen._embed")
    @patch("pipeline.quote_gen._ollama_generate")
    def test_rejects_semantic_duplicate(self, mock_gen, mock_embed):
        # All quotes get the same embedding → semantic duplicates after first
        same_embedding = [0.1] * 768
        mock_gen.return_value = self.FAKE_QUOTES
        mock_embed.return_value = same_embedding

        conn = self._make_db()
        # Pre-seed DB with one quote with same embedding
        conn.execute(
            "INSERT INTO quotes (quote_text, source, embedding) VALUES ('Seed quote here now.', 'ai', ?)",
            (json.dumps(same_embedding),),
        )
        conn.commit()

        stats = generate_batch(conn, model="llama3.1:8b", count=5)
        # All 5 share same embedding as seed → all semantic duplicates
        assert stats["rejected_semantic"] == 5

    @patch("pipeline.quote_gen._embed")
    @patch("pipeline.quote_gen._ollama_generate")
    def test_stores_model_name(self, mock_gen, mock_embed):
        mock_gen.return_value = "A single unique quote line here now."
        mock_embed.return_value = [0.5] * 768

        conn = self._make_db()
        generate_batch(conn, model="qwen3.5:2b", count=1)

        row = conn.execute("SELECT model_used FROM quotes LIMIT 1").fetchone()
        assert row[0] == "qwen3.5:2b"

    @patch("pipeline.quote_gen._embed")
    @patch("pipeline.quote_gen._ollama_generate")
    def test_embedding_stored_as_json(self, mock_gen, mock_embed):
        emb = [float(i) / 768 for i in range(768)]
        mock_gen.return_value = "Unique quote stored in database now."
        mock_embed.return_value = emb

        conn = self._make_db()
        generate_batch(conn, model="llama3.1:8b", count=1)

        stored = conn.execute("SELECT embedding FROM quotes LIMIT 1").fetchone()[0]
        assert json.loads(stored) == pytest.approx(emb)
