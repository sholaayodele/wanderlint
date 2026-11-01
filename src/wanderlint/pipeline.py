"""CLI entry point: `wanderlint check` and `wanderlint eval`. Stubs until M2."""

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Wanderlint: quality checks for travel content, at catalog scale.")


class Mode(StrEnum):
    freelancer_batch = "freelancer-batch"
    supplier_feed = "supplier-feed"
    full_cycle = "full-cycle"


class Split(StrEnum):
    dev = "dev"
    test = "test"


def _not_implemented(what: str) -> None:
    typer.echo(f"{what}: not implemented yet.", err=True)
    raise typer.Exit(code=1)


@app.command()
def check(
    mode: Annotated[Mode, typer.Option(help="Run mode.")],
    input_path: Annotated[Path, typer.Option("--input", help="Listing file or directory.")],
) -> None:
    """Check listings and route them to pass / flag / review."""
    _not_implemented(f"check --mode {mode.value} --input {input_path}")


@app.command("eval")
def eval_(
    split: Annotated[
        Split, typer.Option(help="Data split. Use 'test' at milestones only.")
    ] = Split.dev,
    prompt: Annotated[str, typer.Option(help="Prompt version, e.g. v1.")] = "v1",
) -> None:
    """Score predictions against gold labels."""
    _not_implemented(f"eval --split {split.value} --prompt {prompt}")


if __name__ == "__main__":
    app()
