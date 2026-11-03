"""CLI entry point.

Launches the interactive menu. Future scripting / Docker entry points will
be added here when needed.
"""

import shutil
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from auto_chapter.config import Config, ConfigError, default_config_path
from auto_chapter.console import console
from auto_chapter.detector.ffmpeg import ffmpeg_decoder_problem, ffmpeg_filter_problem
from auto_chapter.logging_setup import setup_logging
from auto_chapter.menu import run_menu
from auto_chapter.orchestrator import Orchestrator
from auto_chapter.path_mapping import PathMapper
from auto_chapter.plex_client import PlexClient

app = typer.Typer(
    name="auto-chapter",
    help="Automatic chapter marker generation for MKV movies on a Plex server.",
    invoke_without_command=True,
    no_args_is_help=False,
)

# Re-checked on every launch, not just at install time -- a tool can vanish
# after installation (a system update, a manual removal, an interrupted
# package manager run) and install.sh only ever checked once.
_REQUIRED_TOOLS = {
    "ffmpeg": "ffmpeg (e.g. sudo pacman -S ffmpeg / sudo apt install ffmpeg)",
    "ffprobe": "ffmpeg (comes with the ffmpeg package)",
    "mkvmerge": "mkvtoolnix (e.g. sudo pacman -S mkvtoolnix-cli / sudo apt install mkvtoolnix)",
    "mkvextract": "mkvtoolnix (comes with the mkvtoolnix package)",
}


@app.callback()
def main(
    ctx: typer.Context,
    config_path: Annotated[
        Path | None,
        typer.Option(
            "--config", "-c", help="Path to config.yml (default: ~/.config/auto-chapter/config.yml)"
        ),
    ] = None,
) -> None:
    """Run the interactive menu."""
    if ctx.invoked_subcommand is None:
        _check_required_tools()
        config = _load_config(config_path)
        orch = _build_orchestrator(config)
        run_menu(orch, config)


def _check_required_tools() -> None:
    """Exit with a clear, actionable message if a required tool is missing.

    There's no reasonable degraded mode here (unlike an unreachable Plex --
    see PlexClient) since detection/writing fundamentally cannot happen
    without these (ffmpeg must also have the filters the detection uses).
    All problems are reported together, not just the first one found, so
    fixing this doesn't turn into a fix-one-relaunch-see-the-next-one loop.
    """
    missing = [
        f"{tool} -- install {hint}"
        for tool, hint in _REQUIRED_TOOLS.items()
        if shutil.which(tool) is None
    ]
    if missing:
        console.print("[red]Missing required tool(s):[/red]")
        for line in missing:
            console.print(f"  {line}")

    # Only worth asking if ffmpeg is there at all (a missing ffmpeg is already
    # reported above). An old or stripped-down build would otherwise fail much
    # later, in the middle of a detection.
    filter_problem = ffmpeg_filter_problem() if shutil.which("ffmpeg") else None
    if filter_problem:
        console.print(f"[red]{escape(filter_problem)}[/red]")

    if missing or filter_problem:
        raise typer.Exit(code=1)

    # A warning only: movies in other formats still work.
    decoder_problem = ffmpeg_decoder_problem()
    if decoder_problem:
        console.print(f"[yellow]{escape(decoder_problem)}[/yellow]")


def _load_config(config_path: Path | None) -> Config:
    """Load config from file, exit with error if it fails."""
    try:
        config = Config.load(config_path)
    except FileNotFoundError:
        console.print(
            f"[red]Config file not found: {config_path or default_config_path()}[/red]\n"
            "[dim]Run ./install.sh in the auto-chapter folder: it creates this file.[/dim]"
        )
        raise typer.Exit(code=1) from None
    except ConfigError as e:
        console.print(f"[red]{escape(e.problem)}[/red]")
        console.print(f"[dim]{escape(e.advice)}[/dim]")
        raise typer.Exit(code=1) from None
    except Exception as e:
        # Not expected (ConfigError covers what can be wrong in the files), but
        # never a traceback.
        console.print(f"[red]Failed to load config:[/red] {escape(str(e))}")
        raise typer.Exit(code=1) from None

    setup_logging(config.logging, config.log_file_path())
    return config


def _build_orchestrator(config: Config) -> Orchestrator:
    """Create the orchestrator (Plex client + path mapper) for this run."""
    mapper = PathMapper(config.paths)
    plex = PlexClient(config.plex, mapper)
    return Orchestrator(config, plex)


if __name__ == "__main__":
    app()
