"""Evaluation — measures retrieval precision and answer accuracy."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from .corpus import Question
from .retrieval import RetrievalResult
from .inference import InferenceResult


@dataclass
class QuestionResult:
    """Full evaluation result for a single question."""

    question_id: str
    repo: str
    question: str
    difficulty: str
    category: str

    # Retrieval metrics
    retrieval_precision_at_5: float
    retrieval_precision_at_10: float
    retrieval_recall: float
    retrieval_wall_time_s: float
    retrieval_tokens: int
    symbols_returned: list[str]

    # Inference metrics
    answer: str
    model: str
    provider: str
    inference_wall_time_s: float
    inference_input_tokens: int
    inference_output_tokens: int
    inference_cost_usd: float

    # Accuracy
    exact_match: bool
    llm_judge_score: float  # 0.0 - 1.0

    # Ground truth
    ground_truth_answer: str
    ground_truth_symbols: list[str]


@dataclass
class BenchmarkRun:
    """Complete benchmark run results."""

    provider: str
    model: str
    timestamp: str
    token_budget: int
    results: list[QuestionResult] = field(default_factory=list)

    @property
    def avg_retrieval_precision_at_5(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.retrieval_precision_at_5 for r in self.results) / len(self.results)

    @property
    def avg_retrieval_precision_at_10(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.retrieval_precision_at_10 for r in self.results) / len(self.results)

    @property
    def avg_retrieval_recall(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.retrieval_recall for r in self.results) / len(self.results)

    @property
    def avg_llm_judge_score(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.llm_judge_score for r in self.results) / len(self.results)

    @property
    def exact_match_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.exact_match) / len(self.results)

    @property
    def total_cost_usd(self) -> float:
        return sum(r.inference_cost_usd for r in self.results)

    @property
    def avg_wall_time_s(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.retrieval_wall_time_s + r.inference_wall_time_s for r in self.results) / len(self.results)

    def save(self, path: str) -> None:
        """Save run results to JSON."""
        data = {
            "provider": self.provider,
            "model": self.model,
            "timestamp": self.timestamp,
            "token_budget": self.token_budget,
            "summary": {
                "total_questions": len(self.results),
                "avg_retrieval_precision_at_5": round(self.avg_retrieval_precision_at_5, 4),
                "avg_retrieval_precision_at_10": round(self.avg_retrieval_precision_at_10, 4),
                "avg_retrieval_recall": round(self.avg_retrieval_recall, 4),
                "avg_llm_judge_score": round(self.avg_llm_judge_score, 4),
                "exact_match_rate": round(self.exact_match_rate, 4),
                "total_cost_usd": round(self.total_cost_usd, 6),
                "avg_wall_time_s": round(self.avg_wall_time_s, 3),
            },
            "results": [asdict(r) for r in self.results],
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, path: str) -> "BenchmarkRun":
        """Load run results from JSON."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        run = cls(
            provider=data["provider"],
            model=data["model"],
            timestamp=data["timestamp"],
            token_budget=data["token_budget"],
        )
        for r in data.get("results", []):
            run.results.append(QuestionResult(**r))
        return run


def _symbol_matches(returned_sym: str, relevant_sym: str) -> bool:
    """Check if a returned symbol matches a relevant symbol.

    Handles qualified names: "Flask.route" matches "route",
    "route.decorator" matches "route", etc.
    Symbol IDs use "." as scope separator (e.g. method.inner_func).
    """
    r = returned_sym.lower()
    g = relevant_sym.lower()
    # Exact match
    if r == g:
        return True
    # Either side contains the other as a dot-separated segment
    r_parts = r.split(".")
    g_parts = g.split(".")
    # Any segment of returned matches any segment of ground truth
    if set(r_parts) & set(g_parts):
        return True
    # Ground truth is a prefix of returned (e.g. "route" matches "route.decorator")
    if r.startswith(g + ".") or g.startswith(r + "."):
        return True
    return False


def _count_hits(returned: list[str], relevant: list[str]) -> int:
    """Count how many returned symbols match any relevant symbol."""
    return sum(1 for s in returned if any(_symbol_matches(s, g) for g in relevant))


def _count_found(returned: list[str], relevant: list[str]) -> int:
    """Count how many relevant symbols were found in returned."""
    return sum(1 for g in relevant if any(_symbol_matches(s, g) for s in returned))


def precision_at_k(returned: list[str], relevant: list[str], k: int) -> float:
    """Compute precision@k: fraction of top-k returned symbols that are relevant."""
    if not relevant or k <= 0:
        return 0.0
    top_k = returned[:k]
    if not top_k:
        return 0.0
    hits = _count_hits(top_k, relevant)
    return hits / min(k, len(top_k))


def recall(returned: list[str], relevant: list[str]) -> float:
    """Compute recall: fraction of relevant symbols that were returned."""
    if not relevant:
        return 1.0  # vacuously true
    found = _count_found(returned, relevant)
    return found / len(relevant)


def exact_match(answer: str, ground_truth: str) -> bool:
    """Check if the answer contains the key content of the ground truth."""
    gt_normalized = ground_truth.strip().lower()
    ans_normalized = answer.strip().lower()
    # Check if GT is a substring of the answer (lenient matching)
    if len(gt_normalized) < 100:
        return gt_normalized in ans_normalized
    # For long GTs, check key phrases (first sentence)
    first_sentence = gt_normalized.split(".")[0].strip()
    return first_sentence in ans_normalized if first_sentence else False


def llm_judge(
    question: str,
    ground_truth: str,
    answer: str,
    judge_provider: str = "anthropic",
    judge_model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> float:
    """Use an LLM to judge answer quality on a 0-1 scale."""
    from .inference import PROVIDER_MAP, infer

    # Validated before the try block below: that block catches ValueError to
    # degrade a non-numeric judge reply to 0.0, which would otherwise swallow
    # `infer`'s unknown-provider ValueError and score the whole corpus zero.
    if judge_provider not in PROVIDER_MAP:
        raise ValueError(
            f"Unknown judge provider: {judge_provider}. "
            f"Choose from: {', '.join(PROVIDER_MAP)}"
        )

    judge_prompt = f"""You are an expert judge evaluating answers about code.

## Question
{question}

## Reference Answer (Ground Truth)
{ground_truth}

## Candidate Answer
{answer}

Rate the candidate answer's accuracy on a scale of 0.0 to 1.0:
- 1.0: Fully correct, covers all key points from the reference
- 0.75: Mostly correct, minor omissions or imprecisions
- 0.5: Partially correct, gets the gist but misses important details
- 0.25: Mostly wrong, but shows some relevant understanding
- 0.0: Completely wrong or irrelevant

Respond with ONLY a single number between 0.0 and 1.0, nothing else."""

    defaults = {"groq": "llama-3.3-70b-versatile", "openai": "gpt-4o-mini", "anthropic": "claude-sonnet-5"}
    model = judge_model or defaults.get(judge_provider, "claude-sonnet-5")

    # Deliberately NOT inside the try below: a judge that could not run (missing
    # credentials, transport error, API rejection) has produced no opinion, and
    # scoring that 0.0 is indistinguishable from a judge that ran and rated the
    # answer worthless. Let it propagate and fail the run.
    result = infer(
        context="",
        question=judge_prompt,
        provider=judge_provider,
        model=model,
        api_key=api_key,
    )

    # Only a reply the judge actually produced but that is not a number degrades
    # to 0.0 — the judge was asked for a bare float and answered something else.
    try:
        score = float(result.answer.strip())
    except (ValueError, TypeError):
        return 0.0
    return max(0.0, min(1.0, score))


def evaluate_question(
    q: Question,
    retrieval_result: RetrievalResult,
    inference_result: InferenceResult,
    judge_provider: str = "anthropic",
    judge_model: Optional[str] = None,
) -> QuestionResult:
    """Evaluate a single question's retrieval + inference results."""
    p_at_5 = precision_at_k(retrieval_result.symbols_returned, q.ground_truth_symbols, 5)
    p_at_10 = precision_at_k(retrieval_result.symbols_returned, q.ground_truth_symbols, 10)
    rec = recall(retrieval_result.symbols_returned, q.ground_truth_symbols)
    em = exact_match(inference_result.answer, q.ground_truth_answer)

    judge_score = llm_judge(
        question=q.question,
        ground_truth=q.ground_truth_answer,
        answer=inference_result.answer,
        judge_provider=judge_provider,
        judge_model=judge_model,
    )

    return QuestionResult(
        question_id=q.id,
        repo=q.repo,
        question=q.question,
        difficulty=q.difficulty,
        category=q.category,
        retrieval_precision_at_5=p_at_5,
        retrieval_precision_at_10=p_at_10,
        retrieval_recall=rec,
        retrieval_wall_time_s=retrieval_result.wall_time_s,
        retrieval_tokens=retrieval_result.token_count,
        symbols_returned=retrieval_result.symbols_returned,
        answer=inference_result.answer,
        model=inference_result.model,
        provider=inference_result.provider,
        inference_wall_time_s=inference_result.wall_time_s,
        inference_input_tokens=inference_result.input_tokens,
        inference_output_tokens=inference_result.output_tokens,
        inference_cost_usd=inference_result.cost_usd,
        exact_match=em,
        llm_judge_score=judge_score,
        ground_truth_answer=q.ground_truth_answer,
        ground_truth_symbols=q.ground_truth_symbols,
    )
