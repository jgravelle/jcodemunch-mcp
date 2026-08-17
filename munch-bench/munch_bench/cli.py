"""CLI entrypoint — munch-bench run / compare / corpus-stats."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__


def _find_corpus_dir() -> str:
    """Find corpus directory relative to package or CWD."""
    # Check package-relative first
    pkg_corpus = Path(__file__).parent.parent.parent / "corpus"
    if pkg_corpus.is_dir():
        return str(pkg_corpus)
    # CWD fallback
    cwd_corpus = Path.cwd() / "corpus"
    if cwd_corpus.is_dir():
        return str(cwd_corpus)
    return str(pkg_corpus)


def cmd_run(args: argparse.Namespace) -> None:
    """Run the benchmark."""
    from .corpus import Corpus
    from .runner import run_benchmark

    corpus = Corpus.load(args.corpus)
    if args.repo:
        corpus = corpus.filter(repo=args.repo)
    if args.difficulty:
        corpus = corpus.filter(difficulty=args.difficulty)
    if args.category:
        corpus = corpus.filter(category=args.category)

    if not corpus.questions:
        print("No questions match the given filters.", file=sys.stderr)
        sys.exit(1)

    run_benchmark(
        corpus=corpus,
        provider=args.provider,
        model=args.model,
        token_budget=args.token_budget,
        judge_provider=args.judge_provider,
        judge_model=args.judge_model,
        storage_path=args.storage_path,
        output_path=args.output,
        verbose=args.verbose,
    )


def cmd_compare(args: argparse.Namespace) -> None:
    """Compare benchmark runs and generate leaderboard."""
    from .evaluate import BenchmarkRun
    from .leaderboard import generate_leaderboard

    runs = []
    for path in args.results:
        runs.append(BenchmarkRun.load(path))

    output = args.output or "leaderboard.html"
    generate_leaderboard(runs, output)
    print(f"Leaderboard written to: {output}")


def cmd_corpus_stats(args: argparse.Namespace) -> None:
    """Print corpus statistics."""
    from .corpus import Corpus
    from rich.console import Console
    from rich.table import Table

    corpus = Corpus.load(args.corpus)
    console = Console()

    table = Table(title="Corpus Statistics")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total Questions", str(len(corpus.questions)))
    table.add_row("Repos", str(len(corpus.repos)))

    # Difficulty breakdown
    for diff in ["easy", "medium", "hard"]:
        count = len([q for q in corpus.questions if q.difficulty == diff])
        table.add_row(f"  {diff}", str(count))

    # Category breakdown
    categories = sorted({q.category for q in corpus.questions})
    for cat in categories:
        count = len([q for q in corpus.questions if q.category == cat])
        table.add_row(f"  [{cat}]", str(count))

    # Per-repo breakdown
    for repo in corpus.repos:
        count = len([q for q in corpus.questions if q.repo == repo])
        table.add_row(f"  {repo}", str(count))

    console.print(table)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="munch-bench",
        description="Retrieval + Inference benchmark for LLM-powered codebase Q&A",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command")

    # run
    p_run = sub.add_parser("run", help="Run the benchmark")
    p_run.add_argument("--provider", default="groq", choices=["groq", "openai", "anthropic"],
                       help="Inference provider (default: groq)")
    p_run.add_argument("--model", default=None, help="Model name (default: provider-specific)")
    p_run.add_argument("--corpus", default=_find_corpus_dir(), help="Path to corpus directory")
    p_run.add_argument("--repo", default=None, help="Filter to a specific repo")
    p_run.add_argument("--difficulty", default=None, choices=["easy", "medium", "hard"])
    p_run.add_argument("--category", default=None)
    p_run.add_argument("--token-budget", type=int, default=8000, help="Retrieval token budget")
    p_run.add_argument("--judge-provider", default="anthropic", help="LLM judge provider")
    p_run.add_argument("--judge-model", default=None, help="LLM judge model")
    p_run.add_argument("--storage-path", default=None, help="jCodeMunch storage path")
    p_run.add_argument("--output", "-o", default=None, help="Output JSON path")
    p_run.add_argument("--verbose", "-v", action="store_true")
    p_run.set_defaults(func=cmd_run)

    # compare
    p_cmp = sub.add_parser("compare", help="Compare runs and generate leaderboard")
    p_cmp.add_argument("results", nargs="+", help="JSON result files to compare")
    p_cmp.add_argument("--output", "-o", default=None, help="Output HTML path")
    p_cmp.set_defaults(func=cmd_compare)

    # corpus-stats
    p_stats = sub.add_parser("corpus-stats", help="Print corpus statistics")
    p_stats.add_argument("--corpus", default=_find_corpus_dir(), help="Path to corpus directory")
    p_stats.set_defaults(func=cmd_corpus_stats)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
