import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from medical_rag import _get_retriever, _one_based_page, _required_settings
from user_identity import user_namespace


def evaluate_cases(cases, top_k=3, auth0_subject=None):
    if not 1 <= top_k <= 10:
        raise ValueError("top_k must be between 1 and 10")
    if not auth0_subject:
        raise ValueError("An Auth0 subject is required to evaluate a user's private namespace.")
    load_dotenv()
    settings = _required_settings(require_groq=False)
    retriever = _get_retriever(settings, user_namespace(auth0_subject))
    report = []
    for case in cases:
        query = case.get("query", "").strip()
        if not query:
            raise ValueError("Each evaluation case must have a non-empty query.")
        expected_sources = {
            os.path.basename(source).lower()
            for source in case.get("expected_sources", [])
        }
        expected_terms = [term.lower() for term in case.get("expected_terms", [])]
        results = retriever.similarity_search_with_relevance_scores(query, k=top_k)
        ranked = []
        relevant_rank = None
        for rank, (document, score) in enumerate(results, start=1):
            source = os.path.basename(
                (document.metadata or {}).get("source", "Reference document")
            )
            source_matches = not expected_sources or source.lower() in expected_sources
            text_matches = not expected_terms or any(
                term in document.page_content.lower() for term in expected_terms
            )
            if relevant_rank is None and source_matches and text_matches:
                relevant_rank = rank
            ranked.append(
                {
                    "rank": rank,
                    "score": round(float(score), 4),
                    "source": source,
                    "page": _one_based_page((document.metadata or {}).get("page")),
                    "preview": " ".join(document.page_content.split())[:240],
                }
            )
        report.append(
            {
                "query": query,
                "expected_sources": sorted(expected_sources),
                "expected_terms": expected_terms,
                "hit_at_k": (
                    relevant_rank is not None
                    if expected_sources or expected_terms
                    else None
                ),
                "reciprocal_rank": 1 / relevant_rank if relevant_rank else 0,
                "results": ranked,
            }
        )
    labeled = [case for case in report if case["expected_sources"] or case["expected_terms"]]
    metrics = {
        "cases": len(report),
        "labeled_cases": len(labeled),
        "hit_rate_at_k": (
            round(sum(case["hit_at_k"] for case in labeled) / len(labeled), 4)
            if labeled
            else None
        ),
        "mean_reciprocal_rank_at_k": (
            round(sum(case["reciprocal_rank"] for case in labeled) / len(labeled), 4)
            if labeled
            else None
        ),
    }
    return {"metrics": metrics, "cases": report}


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate Pinecone retrieval against labeled source expectations; no LLM call."
    )
    parser.add_argument("cases", type=Path, help="JSON file containing evaluation cases")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--auth0-subject", required=True)
    args = parser.parse_args()
    if not 1 <= args.top_k <= 10:
        parser.error("--top-k must be between 1 and 10")
    try:
        cases = json.loads(args.cases.read_text(encoding="utf-8"))
        if not isinstance(cases, list) or not cases:
            raise ValueError("The evaluation file must contain a non-empty JSON array.")
        print(
            json.dumps(
                evaluate_cases(cases, args.top_k, args.auth0_subject),
                indent=2,
                ensure_ascii=True,
            )
        )
    except Exception as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()