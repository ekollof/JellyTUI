"""Textual application shell for JellyTUI."""

from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.theme import Theme

from .api import JellyfinClient
from .config import Config, default_config_path
from .player import MpvPlayer
from .theming import JELLYFIN_PALETTE, Palette, ThemeManager
from .ui.login import LoginScreen
from .ui.main import MainScreen
from .ui.theme import JELLYFIN_THEME


class JellyTuiApp(App):
    """The JellyTUI application."""

    CSS_PATH = "ui/styles.tcss"
    TITLE = "JellyTUI"
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, config: Config, *, config_path: Path | None = None) -> None:
        super().__init__()
        self.config = config
        self.config_path = Path(config_path) if config_path else default_config_path()
        self.client: JellyfinClient | None = None
        self.player = MpvPlayer(config.mpv_path, config.mpv_args)
        self.theme_manager = ThemeManager(config.theme)
        self._theme_seq = 0
        self._dynamic_theme_name: str | None = None
        self._active_palette: Palette | None = None
        self.register_theme(JELLYFIN_THEME)
        self.theme = "jellyfin"

    def on_mount(self) -> None:
        try:
            initial_palette = self.theme_manager.resolve()
        except Exception:
            self.log.exception("Could not read the initial theme palette")
            initial_palette = JELLYFIN_PALETTE
        self._apply_palette(initial_palette, notify=False)
        if (
            self.config.watch_theme
            and self.theme_manager.source != "jellyfin"
        ):
            self.set_interval(1.0, self._poll_theme)

        self.sub_title = self.config.username or "Jellyfin"
        if self.config.is_authenticated:
            self.client = JellyfinClient(
                self.config.server,
                token=self.config.token,
                user_id=self.config.user_id,
                device_id=self.config.device_id,
            )
            self.push_screen(MainScreen())
        else:
            self.push_screen(LoginScreen())

    def _apply_palette(self, palette: Palette, *, notify: bool) -> None:
        previous_palette = self._active_palette
        previous = self._dynamic_theme_name
        if palette.key == "jellyfin":
            self.theme = "jellyfin"
            self._dynamic_theme_name = None
            if previous:
                self.unregister_theme(previous)
        else:
            name = f"jellytui-live-{palette.key}-{self._theme_seq}"
            self._theme_seq += 1
            self.register_theme(
                Theme(
                    name=name,
                    primary=palette.primary,
                    secondary=palette.secondary,
                    accent=palette.accent,
                    warning=palette.warning,
                    error=palette.error,
                    success=palette.success,
                    foreground=palette.foreground,
                    background=palette.background,
                    surface=palette.surface,
                    panel=palette.panel,
                    boost=palette.boost,
                    dark=palette.dark,
                )
            )
            self.theme = name
            self._dynamic_theme_name = name
            if previous and previous != name:
                self.unregister_theme(previous)

        self._active_palette = palette
        for screen in self.screen_stack:
            refresh = getattr(screen, "refresh_theme", None)
            if callable(refresh):
                try:
                    refresh()
                except Exception:
                    self.log.exception("Could not refresh a screen after a theme change")
        same_theme = bool(
            previous_palette
            and previous_palette.key == palette.key
            and previous_palette.title == palette.title
        )
        if notify and not same_theme:
            self.notify(f"Theme: {palette.title}", timeout=2, markup=False)

    def _poll_theme(self) -> None:
        try:
            palette = self.theme_manager.poll()
        except Exception:
            self.log.exception("Could not read the live theme palette")
            return
        if palette is not None:
            self._apply_palette(palette, notify=True)

    def save_config(self) -> None:
        self.config.save(self.config_path)

    def start_session(self, client: JellyfinClient, user: dict) -> None:
        self.client = client
        self.config.server = client.server
        self.config.token = client.token
        self.config.user_id = client.user_id
        self.config.username = str(user.get("Name") or self.config.username)
        self.save_config()
        self.sub_title = self.config.username
        self.switch_screen(MainScreen())

    def on_unmount(self) -> None:
        self.player.quit()
