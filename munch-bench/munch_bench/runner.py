"""Runner — orchestrates benchmark execution."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from .corpus import Corpus, Question
from .retrieval import retrieve, find_repo_id
from .inference import infer
from .evaluate import evaluate_question, BenchmarkRun


console = Console(stderr=True)


def run_benchmark(
    corpus: Corpus,
    provider: str = "groq",
    model: Optional[str] = None,
    token_budget: int = 8000,
    judge_provider: str = "anthropic",
    judge_model: Optional[str] = None,
    storage_path: Optional[str] = None,
    output_path: Optional[str] = None,
    verbose: bool = False,
) -> BenchmarkRun:
    """Run the full benchmark suite."""

    defaults = {
        "groq": "llama-3.3-70b-versatile",
        "openai": "gpt-4o-mini",
        "anthropic": "claude-sonnet-5",
    }
    effective_model = model or defaults.get(provider, "unknown")

    run = BenchmarkRun(
        provider=provider,
        model=effective_model,
        timestamp=datetime.now(timezone.utc).isoformat(),
        token_budget=token_budget,
    )

    # Verify repos are indexed
    repo_ids: dict[str, str] = {}
    for repo in corpus.repos:
        repo_id = find_repo_id(repo, storage_path=storage_path)
        if repo_id is None:
            console.print(f"[red]Error:[/red] Repo '{repo}' not indexed. Run: jcodemunch-mcp index {repo}")
            sys.exit(1)
        repo_ids[repo] = repo_id

    total = len(corpus.questions)
    console.print(f"\n[bold]munch-bench[/bold] — {total} questions, provider={provider}, model={effective_model}\n")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Running benchmark...", total=total)

        for i, q in enumerate(corpus.questions):
            progress.update(task, description=f"[{i+1}/{total}] {q.id}")

            repo_id = repo_ids[q.repo]

            # Retrieval
            try:
                retrieval_result = retrieve(
                    repo_id=repo_id,
                    query=q.question,
                    token_budget=token_budget,
                    storage_path=storage_path,
                )
            except Exception as e:
                console.print(f"[red]Retrieval failed for {q.id}:[/red] {e}")
                progress.advance(task)
                continue

            # Inference
            try:
                inference_result = infer(
                    context=retrieval_result.context_text,
                    question=q.question,
                    provider=provider,
                    model=effective_model,
                )
            except Exception as e:
                console.print(f"[red]Inference failed for {q.id}:[/red] {e}")
                progress.advance(task)
                continue

            # Evaluate
            qr = evaluate_question(
                q=q,
                retrieval_result=retrieval_result,
                inference_result=inference_result,
                judge_provider=judge_provider,
                judge_model=judge_model,
            )
            run.results.append(qr)

            if verbose:
                console.print(f"  P@5={qr.retrieval_precision_at_5:.2f} "
                              f"Judge={qr.llm_judge_score:.2f} "
                              f"Time={qr.retrieval_wall_time_s + qr.inference_wall_time_s:.2f}s")

            progress.advance(task)

    # Print summary
    _print_summary(run)

    # Save results
    if output_path is None:
        output_path = f"results/{provider}_{effective_model}_{run.timestamp[:10]}.json"
    run.save(output_path)
    console.print(f"\n[green]Results saved to:[/green] {output_path}")

    return run


def _print_summary(run: BenchmarkRun) -> None:
    """Print a summary table of the run."""
    table = Table(title=f"\nmunch-bench Results: {run.provider}/{run.model}")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Questions", str(len(run.results)))
    table.add_row("Retrieval P@5", f"{run.avg_retrieval_precision_at_5:.4f}")
    table.add_row("Retrieval P@10", f"{run.avg_retrieval_precision_at_10:.4f}")
    table.add_row("Retrieval Recall", f"{run.avg_retrieval_recall:.4f}")
    table.add_row("LLM Judge Score", f"{run.avg_llm_judge_score:.4f}")
    table.add_row("Exact Match Rate", f"{run.exact_match_rate:.4f}")
    table.add_row("Avg Wall Time", f"{run.avg_wall_time_s:.3f}s")
    table.add_row("Total Cost", f"${run.total_cost_usd:.6f}")

    console.print(table)
