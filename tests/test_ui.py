"""Textual UI smoke tests against a fake Jellyfin server."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from textual.widgets import DataTable, Input, OptionList, Static

from jellytui.app import JellyTuiApp
from jellytui.audio import AudioPlayer
from jellytui.config import Config
from jellytui.ui.capabilities import detect_graphics
from jellytui.ui.login import LoginScreen
from jellytui.ui.main import MainScreen
from jellytui.ui.modals import HelpScreen, ResumeScreen
from jellytui.ui.music import MusicPlayerScreen


async def wait_for(pilot, predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await pilot.pause(0.1)
    return False


@pytest.fixture(autouse=True)
def text_graphics(monkeypatch):
    monkeypatch.setenv("JELLYTUI_GRAPHICS", "text")
    monkeypatch.setenv("JELLYTUI_THEME", "jellyfin")
    monkeypatch.delenv("JELLYTUI_OMARCHY_COLORS", raising=False)
    monkeypatch.delenv("JELLYTUI_PYWAL_COLORS", raising=False)
    detect_graphics.cache_clear()
    yield
    detect_graphics.cache_clear()


def make_config(server_url: str, config_path: Path, *, authenticated=True) -> Config:
    return Config(
        server=server_url,
        username="tester",
        user_id="user-1" if authenticated else "",
        token="test-token" if authenticated else "",
        device_id="device-1",
        mpv_args=["--no-config", "--vo=null", "--ao=null"],
        report_progress=False,
        images=False,
    )


async def test_browse_search_and_modals(fake_server, tmp_path):
    config = make_config(fake_server.url, tmp_path / "config.json")
    app = JellyTuiApp(config, config_path=tmp_path / "config.json")
    async with app.run_test(size=(130, 40)) as pilot:
        screen = app.screen
        assert isinstance(screen, MainScreen)
        table = screen.query_one("#items", DataTable)
        assert await wait_for(pilot, lambda: len(screen.items) == 2 and not table.loading)
        assert table.row_count == 2
        assert screen.location is not None and screen.location.kind == "resume"
        assert "2 items" in str(screen.query_one("#breadcrumb", Static).render())
        assert "The Matrix" in str(screen.query_one("#details-title", Static).render())

        libraries = screen.query_one("#libraries", OptionList)
        assert libraries.option_count == 5  # resume, latest, separator, 2 views

        # Open the Shows library and drill into series -> season -> episodes.
        option_ids = [option.id for option in libraries.options]
        libraries.highlighted = option_ids.index("view:lib-shows")
        libraries.focus()
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: screen.location.kind == "library" and screen.items)
        assert screen.items[0].name == "Severance"

        table.focus()
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: screen.items and screen.items[0].type == "Season")
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: screen.items and screen.items[0].type == "Episode")
        assert screen.items[0].title.startswith("S01E01")
        assert "Shows  /  Severance  /  Season 1" in str(
            screen.query_one("#breadcrumb", Static).render()
        )

        await pilot.press("backspace")
        assert await wait_for(pilot, lambda: screen.items and screen.items[0].type == "Season")
        await pilot.press("escape")
        assert await wait_for(pilot, lambda: screen.items and screen.items[0].type == "Series")

        # Search from anywhere and return to the previous location afterwards.
        await pilot.press("slash")
        await pilot.press(*"matrix")
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: screen.location.kind == "search" and screen.items)
        assert screen.items[0].name == "The Matrix"
        assert screen.query_one("#items", DataTable).has_focus
        await pilot.press("escape")
        assert await wait_for(pilot, lambda: screen.location.kind == "library")
        assert not screen.query_one("#search", Input).display

        # Resume prompt for an item with saved progress.
        libraries.highlighted = 0
        libraries.focus()
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: screen.location.kind == "resume" and len(screen.items) == 2)
        table.focus()
        table.move_cursor(row=0)
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: isinstance(app.screen, ResumeScreen))
        await pilot.press("escape")
        assert await wait_for(pilot, lambda: isinstance(app.screen, MainScreen))

        # Watched toggle hits the server and updates the row.
        table.focus()
        table.move_cursor(row=0)
        await pilot.press("r")
        assert await wait_for(
            pilot,
            lambda: any(
                req["method"] == "POST"
                and req["path"] == "/UserPlayedItems/movie-1"
                for req in fake_server.requests
            ),
        )
        await pilot.press("r")
        assert await wait_for(
            pilot,
            lambda: any(
                req["method"] == "DELETE"
                and req["path"] == "/UserPlayedItems/movie-1"
                for req in fake_server.requests
            ),
        )

        # Help modal.
        await pilot.press("question_mark")
        assert await wait_for(pilot, lambda: isinstance(app.screen, HelpScreen))
        await pilot.press("escape")
        assert await wait_for(pilot, lambda: isinstance(app.screen, MainScreen))


async def test_playback_reports_progress(fake_server, media_file, tmp_path):
    config = make_config(fake_server.url, tmp_path / "config.json")
    config.report_progress = True
    fake_server.include_playable = True
    fake_server.media_path = media_file
    app = JellyTuiApp(config, config_path=tmp_path / "config.json")
    async with app.run_test(size=(130, 40)) as pilot:
        screen = app.screen
        assert isinstance(screen, MainScreen)
        assert await wait_for(pilot, lambda: len(screen.items) == 3)
        index = next(
            position
            for position, item in enumerate(screen.items)
            if item.id == "playable-1"
        )
        table = screen.query_one("#items", DataTable)
        table.focus()
        table.move_cursor(row=index)
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: screen.playback is not None, timeout=30)

        assert await wait_for(
            pilot,
            lambda: any(
                request["method"] == "POST"
                and request["path"] == "/Sessions/Playing"
                for request in fake_server.requests
            ),
            timeout=10,
        )
        start = next(
            request
            for request in fake_server.requests
            if request["method"] == "POST"
            and request["path"] == "/Sessions/Playing"
        )
        assert start["body"]["ItemId"] == "playable-1"
        assert start["body"]["MediaSourceId"] == "playable-1"
        assert start["body"]["PlayMethod"] == "DirectPlay"

        assert await wait_for(
            pilot,
            lambda: any(
                request["path"] == "/Sessions/Playing/Progress"
                and request["body"]
                and request["body"].get("PositionTicks", 0) >= 0
                for request in fake_server.requests
            ),
            timeout=10,
        )

        await pilot.press("space")
        assert await wait_for(
            pilot,
            lambda: any(
                request["path"] == "/Sessions/Playing/Progress"
                and request["body"]
                and request["body"].get("IsPaused") is True
                for request in fake_server.requests
            ),
            timeout=10,
        )

        await pilot.press("S")
        assert await wait_for(
            pilot,
            lambda: any(
                request["path"] == "/Sessions/Playing/Stopped"
                for request in fake_server.requests
            ),
            timeout=10,
        )
        assert await wait_for(pilot, lambda: screen.playback is None)


async def test_login_flow(fake_server, tmp_path):
    config = make_config(fake_server.url, tmp_path / "config.json", authenticated=False)
    config.username = ""
    config_path = tmp_path / "config.json"
    app = JellyTuiApp(config, config_path=config_path)
    async with app.run_test(size=(100, 38)) as pilot:
        assert isinstance(app.screen, LoginScreen)
        username = app.screen.query_one("#username", Input)
        password = app.screen.query_one("#password", Input)
        username.value = "alice"
        password.value = "secret"
        password.focus()
        await pilot.press("enter")

        assert await wait_for(
            pilot,
            lambda: isinstance(app.screen, MainScreen) and app.screen.items,
            timeout=15,
        )
        assert config_path.exists()
        saved = Config.load(config_path)
        assert saved.token == "fake-token"
        assert saved.user_id == "user-1"
        assert saved.username == "alice"
        assert saved.is_authenticated


THEME_TOML = """\
mode = "dark"
accent = "#ff7059"
background = "#101112"
foreground = "#f4dcc3"
red = "#d7503f"
green = "#d9ad6f"
yellow = "#f5c080"
cyan = "#6ab8de"
blue = "#6284af"
magenta = "#b66d68"
"""


async def test_live_omarchy_theme_change(fake_server, tmp_path, monkeypatch):
    colors_path = tmp_path / "colors.toml"
    colors_path.write_text(THEME_TOML, encoding="utf-8")
    monkeypatch.setenv("JELLYTUI_OMARCHY_COLORS", str(colors_path))
    monkeypatch.setenv("JELLYTUI_THEME", "omarchy")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config = make_config(fake_server.url, tmp_path / "config.json")
    app = JellyTuiApp(config, config_path=tmp_path / "config.json")
    async with app.run_test(size=(130, 40)) as pilot:
        assert await wait_for(pilot, lambda: isinstance(app.screen, MainScreen))
        assert app.current_theme.background == "#101112"
        assert app.current_theme.primary == "#ff7059"
        assert app.theme.startswith("jellytui-live-omarchy")

        colors_path.write_text(
            THEME_TOML.replace("#ff7059", "#00ff00").replace(
                "#101112", "#202020"
            ),
            encoding="utf-8",
        )
        assert await wait_for(
            pilot,
            lambda: app.current_theme.background == "#202020",
            timeout=6,
        )
        assert app.current_theme.primary == "#00ff00"
        breadcrumb = app.screen.query_one("#breadcrumb", Static)
        assert await wait_for(
            pilot,
            lambda: breadcrumb.styles.background.hex.lower()
            == app.current_theme.panel.lower(),
            timeout=6,
        )


def _pywal_json(background: str, primary: str) -> str:
    import json

    colors = {
        "color0": background,
        "color1": "#d7503f",
        "color2": "#d9ad6f",
        "color3": "#f5c080",
        "color4": primary,
        "color5": "#b66d68",
        "color6": "#6ab8de",
        "color7": "#f4dcc3",
        "color8": "#61686b",
        "color9": "#ff7059",
        "color10": "#f9c977",
        "color11": "#ffc958",
        "color12": "#83a8df",
        "color13": "#e38f88",
        "color14": "#84daff",
        "color15": "#ffe3ab",
    }
    return json.dumps(
        {
            "special": {"background": background, "foreground": "#f4dcc3"},
            "colors": colors,
        }
    )


async def test_live_pywal_theme_change(fake_server, tmp_path, monkeypatch):
    colors_path = tmp_path / "colors.json"
    colors_path.write_text(_pywal_json("#0c0e0f", "#6284af"), encoding="utf-8")
    monkeypatch.setenv("JELLYTUI_THEME", "pywal")
    monkeypatch.setenv("JELLYTUI_PYWAL_COLORS", str(colors_path))
    config = make_config(fake_server.url, tmp_path / "config.json")
    app = JellyTuiApp(config, config_path=tmp_path / "config.json")
    async with app.run_test(size=(130, 40)) as pilot:
        assert await wait_for(pilot, lambda: isinstance(app.screen, MainScreen))
        assert app.current_theme.background == "#0c0e0f"
        assert app.current_theme.primary == "#6284af"

        colors_path.write_text(_pywal_json("#202020", "#00ff00"), encoding="utf-8")
        assert await wait_for(
            pilot,
            lambda: app.current_theme.background == "#202020",
            timeout=6,
        )
        assert app.current_theme.primary == "#00ff00"
        breadcrumb = app.screen.query_one("#breadcrumb", Static)
        assert await wait_for(
            pilot,
            lambda: breadcrumb.styles.background.hex.lower()
            == app.current_theme.panel.lower(),
            timeout=6,
        )


async def test_music_player_queue_and_auto_advance(fake_server, audio_file, tmp_path):
    usable, reason = AudioPlayer().availability()
    if not usable:
        pytest.skip(reason)
    config = make_config(fake_server.url, tmp_path / "config.json")
    config.audio_backend = "ffmpeg"
    config.report_progress = True
    config.images = False
    fake_server.media_path = audio_file
    app = JellyTuiApp(config, config_path=tmp_path / "config.json")
    app.audio_player.set_volume(0)
    async with app.run_test(size=(130, 40)) as pilot:
        main = app.screen
        assert isinstance(main, MainScreen)
        assert await wait_for(
            pilot,
            lambda: not main.query_one("#items", DataTable).loading,
        )
        await pilot.press("slash")
        await pilot.press(*"track")
        await pilot.press("enter")
        assert await wait_for(
            pilot,
            lambda: main.location.kind == "search" and len(main.items) == 2,
        )

        main.query_one("#items", DataTable).focus()
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: main.playback is not None, timeout=20)
        context = main.playback
        assert context is not None
        assert context.engine == "audio"
        assert context.item.name == "Track One"
        assert [item.name for item in main.music_queue] == ["Track One", "Track Two"]
        assert not app.player.state().running

        assert await wait_for(
            pilot,
            lambda: any(
                request["path"] == "/Sessions/Playing"
                and request["body"]
                and request["body"].get("ItemId") == "audio-1"
                for request in fake_server.requests
            ),
            timeout=10,
        )
        assert await wait_for(
            pilot,
            lambda: any(
                request["path"] == "/Sessions/Playing/Progress"
                for request in fake_server.requests
            ),
            timeout=10,
        )

        await pilot.press("m")
        assert await wait_for(pilot, lambda: isinstance(app.screen, MusicPlayerScreen))
        music = app.screen
        assert "Track One" in str(music.query_one("#music-title", Static).render())
        queue = music.query_one("#music-queue", OptionList)
        assert queue.option_count == 2

        await pilot.press("space")
        assert await wait_for(pilot, lambda: app.audio_player.state().paused)
        await pilot.press("space")
        assert await wait_for(pilot, lambda: not app.audio_player.state().paused)

        assert await wait_for(
            pilot,
            lambda: main.playback is not None
            and main.playback.item.name == "Track Two",
            timeout=12,
        )
        assert main.music_index == 1
        assert await wait_for(
            pilot,
            lambda: "Track Two"
            in str(music.query_one("#music-title", Static).render()),
            timeout=5,
        )
        assert await wait_for(
            pilot,
            lambda: any(
                request["path"] == "/Sessions/Playing"
                and request["body"]
                and request["body"].get("ItemId") == "audio-2"
                for request in fake_server.requests
            ),
            timeout=10,
        )

        await pilot.press("p")
        assert await wait_for(
            pilot,
            lambda: main.playback is not None
            and main.playback.item.name == "Track One",
            timeout=10,
        )
        assert main.music_index == 0

        await pilot.press("s")
        assert await wait_for(pilot, lambda: main.playback is None)
        await pilot.press("escape")
        assert await wait_for(pilot, lambda: isinstance(app.screen, MainScreen))

        await pilot.press("S")  # no-op, but must not crash after the queue stopped
        await pilot.pause(0.3)
