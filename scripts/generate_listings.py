"""Generate synthetic listings with seeded defects.

    uv run python scripts/generate_listings.py --n 10 --seed 42 --dry-run
    uv run python scripts/generate_listings.py --n 10 --seed 42 --max-eur 0.50
    uv run python scripts/generate_listings.py --n 150 --seed 42 --yes

Step 1 plans specs deterministically (seed), step 2 generates clean listings,
step 3 injects defects in a separate call so seeded labels are exact. LLM
responses are cached in .cache/llm, so re-runs are free and identical.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from wanderlint.cost import CostTracker, Pricing
from wanderlint.datagen.plan import PlanConfig, plan_specs
from wanderlint.datagen.run import (
    GenSettings,
    check_context,
    generation_user_prompt,
    render_prompts,
    run_all,
    summarize,
    write_outputs,
)
from wanderlint.llm import LLMClient
from wanderlint.models import Taxonomy

ROOT = Path(__file__).resolve().parents[1]
app = typer.Typer(add_completion=False)


def _estimate_eur(specs, prompts, pricing: Pricing, model: str) -> float:
    """Rough pre-run estimate: ~3.5 chars/token input, observed-scale outputs (effort low)."""
    p = pricing.price(model)
    usd = 0.0
    for s in specs:
        gen_in = (len(prompts.listing_system) + len(generation_user_prompt(s, []))) / 3.5
        usd += gen_in * p.input / 1e6 + 1000 * p.output / 1e6
        n_llm = sum(1 for d in s.defects if d.method == "llm")
        inj_in = (len(prompts.inject_system) + 3500) / 3.5
        usd += n_llm * (inj_in * p.input / 1e6 + 800 * p.output / 1e6)
    return usd * 1.2 * pricing.usd_to_eur  # +20% retries


def _git_state() -> dict[str, Any]:
    """Commit the code ran from; dirty = tracked files changed (untracked data ignored)."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        return {
            "git_commit": git("rev-parse", "HEAD"),
            "git_dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit": None, "git_dirty": None}


@app.command()
def main(
    n: Annotated[int, typer.Option(help="Number of listings.")] = 150,
    seed: Annotated[int, typer.Option(help="Seed for planning and injection.")] = 42,
    dry_run: Annotated[bool, typer.Option(help="Plan + estimate only; no API calls.")] = False,
    max_eur: Annotated[
        float | None, typer.Option(help="Override the datagen hard cap (EUR).")
    ] = None,
    yes: Annotated[
        bool, typer.Option(help="Confirm a run above the API confirmation threshold.")
    ] = False,
    config_path: Annotated[Path, typer.Option("--config")] = ROOT / "config.yaml",
) -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    dg = config["datagen"]
    paths_cfg = config["paths"]
    taxonomy = Taxonomy.model_validate(
        yaml.safe_load((ROOT / paths_cfg["taxonomy"]).read_text(encoding="utf-8"))
    )
    categories = taxonomy.by_id()["D7"].categories or []
    ctx = check_context(taxonomy)
    prompts = render_prompts(ROOT / paths_cfg["prompts_dir"], dg["prompt_version"], taxonomy)
    pricing = Pricing.from_config(config)
    model = config["models"]["datagen"]

    specs = plan_specs(n, seed, PlanConfig.from_config(dg), categories)
    planned = Counter(d.defect_id for s in specs for d in s.defects)
    typer.echo(
        f"Planned {n} listings: {sum(s.is_clean for s in specs)} clean, "
        f"{sum(not s.is_clean for s in specs)} defective; "
        f"locales {dict(Counter(s.locale for s in specs))}; "
        f"defects {dict(sorted(planned.items()))}; "
        f"hard cases {sum(1 for s in specs for d in s.defects if d.hard_case)}"
    )
    estimate = _estimate_eur(specs, prompts, pricing, model)
    cap = max_eur if max_eur is not None else float(dg["hard_cap_eur_per_run"])
    typer.echo(
        f"Model {model}, effort {dg['effort']}. Estimated cost ≈ €{estimate:.2f}; cap €{cap:.2f}."
    )
    if dry_run:
        raise typer.Exit()
    if n > int(config["cost"]["confirm_above_n_listings"]) and not yes:
        typer.echo("More than the confirmation threshold of listings: re-run with --yes.", err=True)
        raise typer.Exit(code=2)

    import anthropic  # imported here so --dry-run works without credentials

    llm = LLMClient(
        client=anthropic.Anthropic(),
        cache_dir=ROOT / paths_cfg["cache_dir"],
        tracker=CostTracker(pricing=pricing, cap_eur=cap),
        taxonomy_version=taxonomy.version,
    )
    settings = GenSettings(
        model=model,
        prompt_version=dg["prompt_version"],
        effort=dg["effort"],
        max_tokens=int(dg["max_tokens"]),
        max_retries=int(dg["max_retries"]),
    )

    def progress(i: int, total: int, lid: str, eur: float) -> None:
        typer.echo(f"  [{i}/{total}] {lid}  spent €{eur:.4f}")

    results, failures, stop = run_all(specs, llm, prompts, settings, ctx, seed, progress)
    out = {
        "listings": ROOT / dg["output_listings"],
        "seeded": ROOT / dg["output_seeded"],
        "meta": ROOT / dg["output_meta"],
        "failures": ROOT / dg["output_failures"],
    }
    provenance = {
        **_git_state(),
        "prompt_version": dg["prompt_version"],
        "taxonomy_version": taxonomy.version,
    }
    write_outputs(results, failures, out, provenance)
    summary = summarize(results, failures, llm)
    summary.update({"n": n, "seed": seed, "model": model, "stopped": stop, **provenance})
    run_path = out["listings"].with_name("generation_run.json")
    calls = [
        {
            "purpose": c.purpose,
            "cached": c.cached,
            "cost_eur": round(c.cost_eur, 6),
            "latency_s": round(c.latency_s, 2),
            **c.usage.__dict__,
        }
        for c in llm.tracker.calls
    ]
    run_path.write_text(
        json.dumps({"summary": summary, "calls": calls}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))
    if stop:
        typer.echo(f"STOPPED: {stop}", err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
