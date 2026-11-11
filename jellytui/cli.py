"""Command line entry point for JellyTUI."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import Config, default_config_path, normalise_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jellytui",
        description="A keyboard-driven Jellyfin client for the terminal, with mpv playback.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, help="Path to the config file.")
    parser.add_argument("--server", help="Jellyfin server URL (including any base path).")
    parser.add_argument("--username", help="Username to prefill on the login screen.")
    parser.add_argument(
        "--logout",
        action="store_true",
        help="Forget the saved session and show the login screen.",
    )
    parser.add_argument("--mpv", dest="mpv_path", help="Path to the mpv executable.")
    parser.add_argument(
        "--mpv-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="Extra argument passed to mpv (repeatable).",
    )
    parser.add_argument(
        "--audio-language",
        help="Preferred audio language (for example: eng, en, English).",
    )
    parser.add_argument(
        "--subtitle-language",
        help="Preferred subtitle language (for example: eng, en, English).",
    )
    parser.add_argument(
        "--subtitles",
        choices=("auto", "off", "forced"),
        help="Subtitle selection mode.",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Do not show cover art.",
    )
    parser.add_argument(
        "--theme",
        choices=("auto", "omarchy", "pywal", "jellyfin"),
        help="Colour theme source (default: auto: Omarchy, then pywal, then Jellyfin).",
    )
    parser.add_argument(
        "--no-theme-watch",
        action="store_true",
        help="Do not watch Omarchy/pywal palettes for live changes.",
    )
    parser.add_argument(
        "--graphics",
        choices=("auto", "kitty", "sixel", "chafa", "text"),
        help="Force a terminal graphics backend (default: auto-detect).",
    )
    parser.add_argument(
        "--no-progress-report",
        action="store_true",
        help="Do not report playback progress to the Jellyfin server.",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Print terminal, chafa and mpv capability information, then exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = (args.config or default_config_path()).expanduser()
    config = Config.load(config_path)
    changed = False

    if args.server:
        server = normalise_server(args.server)
        if server != config.server:
            config.server = server
            config.clear_session()
        changed = True
    if args.username:
        if args.username != config.username:
            config.username = args.username
            config.clear_session()
        changed = True
    if args.logout:
        config.clear_session()
        changed = True
    if args.mpv_path:
        config.mpv_path = args.mpv_path
        changed = True
    if args.mpv_arg:
        config.mpv_args.extend(args.mpv_arg)
        changed = True
    if args.audio_language is not None:
        config.audio_language = args.audio_language
        changed = True
    if args.subtitle_language is not None:
        config.subtitle_language = args.subtitle_language
        changed = True
    if args.subtitles:
        config.subtitle_mode = args.subtitles
        changed = True
    if args.no_images:
        config.images = False
        changed = True
    if args.theme:
        config.theme = args.theme
        changed = True
    if args.no_theme_watch:
        config.watch_theme = False
        changed = True
    if args.no_progress_report:
        config.report_progress = False
        changed = True
    if args.graphics and args.graphics != "auto":
        os.environ["JELLYTUI_GRAPHICS"] = args.graphics

    if changed:
        config.save(config_path)

    if args.diagnostics:
        return _print_diagnostics(config, config_path)

    # Import the UI only after argument parsing: importing textual-image probes
    # the terminal's graphics capabilities, which needs the real stdin.
    from .app import JellyTuiApp

    app = JellyTuiApp(config, config_path=config_path)
    app.run()
    return 0


def _print_diagnostics(config: Config, config_path: Path) -> int:
    from .theming import (
        ThemeManager,
        omarchy_colors_path,
        pywal_colors_path,
    )
    from .ui.capabilities import asset_path, detect_graphics

    graphics = detect_graphics()
    theme_manager = ThemeManager(config.theme)
    palette = theme_manager.resolve()
    print(f"jellytui        {__version__}")
    print(f"config          {config_path}")
    print(f"server          {config.server}")
    print(f"logged in       {'yes' if config.is_authenticated else 'no'}")
    print(f"graphics mode   {graphics.mode} ({graphics.label})")
    print(f"chafa           {graphics.chafa_path or 'not found'}")
    print(f"theme source    {theme_manager.source} -> {palette.key} ({palette.title})")
    print(f"theme watch     {'yes' if config.watch_theme else 'no'}")
    print(f"omarchy colors  {omarchy_colors_path() or 'not found'}")
    print(f"pywal colors    {pywal_colors_path() or 'not found'}")
    print(f"mpv             {shutil.which(config.mpv_path) or config.mpv_path}")
    print(f"logo asset      {asset_path('jellyfin-icon.png')}")
    print(f"terminal        TERM={os.environ.get('TERM', '')!r} "
          f"TERM_PROGRAM={os.environ.get('TERM_PROGRAM', '')!r} "
          f"KITTY_WINDOW_ID={'yes' if os.environ.get('KITTY_WINDOW_ID') else 'no'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
