import evaluate
from langchain_core.documents import Document


def test_evaluation_reports_hit_rate_and_mean_reciprocal_rank(monkeypatch):
    class FakeRetriever:
        def similarity_search_with_relevance_scores(self, query, k):
            if "first" in query:
                return [
                    (Document(page_content="other", metadata={"source": "other.pdf"}), 0.8),
                    (
                        Document(
                            page_content="expected phrase match",
                            metadata={"source": "expected.pdf"},
                        ),
                        0.7,
                    ),
                ]
            return [(Document(page_content="miss", metadata={"source": "other.pdf"}), 0.6)]

    monkeypatch.setattr(evaluate, "load_dotenv", lambda: None)
    monkeypatch.setattr(evaluate, "_required_settings", lambda require_groq: {})
    monkeypatch.setattr(
        evaluate, "_get_retriever", lambda settings, namespace: FakeRetriever()
    )
    monkeypatch.setattr(evaluate, "user_namespace", lambda subject: "user-test")

    report = evaluate.evaluate_cases(
        [
            {
                "query": "first query",
                "expected_sources": ["expected.pdf"],
                "expected_terms": ["expected phrase"],
            },
            {"query": "second query", "expected_sources": ["expected.pdf"]},
        ],
        top_k=3,
        auth0_subject="auth0|researcher",
    )

    assert report["metrics"] == {
        "cases": 2,
        "labeled_cases": 2,
        "hit_rate_at_k": 0.5,
        "mean_reciprocal_rank_at_k": 0.25,
    }