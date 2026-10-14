from io import BytesIO

import pytest
from langchain_core.documents import Document
from pypdf import PdfWriter
from langchain_community.document_loaders import PyPDFLoader

import ingest
from ingest import _stable_chunk_ids
import medical_rag


def test_pinecone_setup_does_not_require_groq(monkeypatch):
    monkeypatch.setenv("PINECONE_API_KEY", "test-key")
    monkeypatch.setenv("PINECONE_INDEX_NAME", "test-index")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    assert medical_rag._required_settings(require_groq=False) == {
        "PINECONE_API_KEY": "test-key",
        "PINECONE_INDEX_NAME": "test-index",
    }


def test_generation_still_requires_groq(monkeypatch):
    monkeypatch.setenv("PINECONE_API_KEY", "test-key")
    monkeypatch.setenv("PINECONE_INDEX_NAME", "test-index")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(medical_rag.ConfigurationError, match="GROQ_API_KEY"):
        medical_rag._required_settings()


def test_relevance_filter_discards_weak_and_missing_scores():
    relevant = Document(page_content="supported passage")
    weak = Document(page_content="weak passage")
    unscored = Document(page_content="unscored passage")

    assert medical_rag._filter_relevant_documents(
        [(relevant, 0.7), (weak, 0.1), (unscored, None)], 0.25
    ) == [relevant]


@pytest.mark.parametrize("value", ["not-a-number", "-0.1", "1.1"])
def test_invalid_relevance_threshold_is_rejected(monkeypatch, value):
    monkeypatch.setenv("MIN_RELEVANCE_SCORE", value)

    with pytest.raises(medical_rag.ConfigurationError, match="MIN_RELEVANCE_SCORE"):
        medical_rag._minimum_relevance_score()


def test_sources_include_one_based_page_and_remove_duplicates():
    first = Document(page_content="a", metadata={"source": "reference.pdf", "page": 0.0})
    duplicate = Document(page_content="b", metadata={"source": "reference.pdf", "page": 0.0})

    assert medical_rag._format_sources([first, duplicate]) == ["reference.pdf, page 1"]


def test_ingestion_uses_repeatable_unique_chunk_ids():
    chunks = [
        Document(page_content="first", metadata={"source": "guide.pdf", "page": 0}),
        Document(page_content="second", metadata={"source": "guide.pdf", "page": 0}),
    ]

    assert _stable_chunk_ids(chunks) == _stable_chunk_ids(chunks)
    assert len(set(_stable_chunk_ids(chunks))) == len(chunks)


def test_different_uploaded_documents_use_separate_chunk_ids():
    first = Document(
        page_content="same page text",
        metadata={"source": "first.pdf", "document_id": "content-hash-one", "page": 0},
    )
    second = Document(
        page_content="same page text",
        metadata={"source": "second.pdf", "document_id": "content-hash-two", "page": 0},
    )

    assert _stable_chunk_ids([first]) != _stable_chunk_ids([second])


def test_pdf_upload_stores_content_hash_and_passes_safe_source_metadata(monkeypatch, tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    pdf_stream = BytesIO()
    writer.write(pdf_stream)
    pdf_bytes = pdf_stream.getvalue()
    captured = {}

    class FakeLoader:
        def __init__(self, path):
            captured["stored_path"] = path

        def load(self):
            return [Document(page_content="Reference text", metadata={"page": 0})]

    monkeypatch.setattr("langchain_community.document_loaders.PyPDFLoader", FakeLoader)
    monkeypatch.setattr(ingest, "_required_settings", lambda require_groq: {"PINECONE_API_KEY": "x"})

    def capture_index(pages, settings):
        captured["page"] = pages[0]
        captured["settings"] = settings
        return 1

    monkeypatch.setattr(ingest, "_index_pages", lambda pages, settings, namespace: capture_index(pages, settings))

    result = ingest.ingest_uploaded_pdf("../headache reference.pdf", pdf_bytes, tmp_path, "account-test")

    assert result["chunks"] == 1
    assert captured["page"].metadata["source"] == "headache_reference.pdf"
    assert len(captured["page"].metadata["document_id"]) == 64
    assert captured["stored_path"].startswith(str(tmp_path))
    with open(captured["stored_path"], "rb") as stored_file:
        assert stored_file.read() == pdf_bytes


def test_low_relevance_abstains_without_calling_groq(monkeypatch):
    class FakeRetriever:
        def similarity_search_with_relevance_scores(self, question, k):
            return [(Document(page_content="Unrelated excerpt."), 0.05)]

    monkeypatch.setenv("PINECONE_API_KEY", "test-key")
    monkeypatch.setenv("PINECONE_INDEX_NAME", "test-index")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("MIN_RELEVANCE_SCORE", "0.25")
    monkeypatch.setattr(medical_rag, "_get_retriever", lambda settings, namespace: FakeRetriever())

    result = medical_rag.answer_question("A question unsupported by this excerpt")

    assert result["sources"] == []
    assert "did not provide sufficiently relevant information" in result["answer"]