"""Login screen."""

from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Label, Static
from textual.worker import get_current_worker

from ..api import AuthenticationError, JellyfinClient, JellyfinError
from ..config import normalise_server
from .capabilities import asset_path, chafa_ansi, detect_graphics, image_widget_class
from .theme import logo_text, tagline_text, wordmark_text


class LoginScreen(Screen):
    """Connect to a Jellyfin server and remember the session."""

    BINDINGS = [
        Binding("escape", "quit_app", "Quit"),
        Binding("q", "quit_app", "Quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.graphics = detect_graphics()
        self._status_text = ""
        self._status_error = False

    def compose(self) -> ComposeResult:
        config = self.app.config
        with Vertical(id="login-shell"), Vertical(id="login-card"):
            with Vertical(id="brand"):
                if self.graphics.graphical:
                    image_class = image_widget_class(self.graphics.mode)
                    yield image_class(
                        str(asset_path("jellyfin-icon.png")), id="login-logo"
                    )
                    yield Static(wordmark_text(), id="wordmark")
                elif self.graphics.mode == "chafa":
                    yield Static("", id="login-logo-art")
                    yield Static(wordmark_text(), id="wordmark")
                else:
                    yield Static(logo_text(), id="logo")
            yield Static(tagline_text(), id="tagline")
            yield Label("Server URL", classes="field-label")
            yield Input(
                value=config.server,
                placeholder="http://localhost:8096",
                id="server",
            )
            yield Label("Username", classes="field-label")
            yield Input(value=config.username, placeholder="username", id="username")
            yield Label("Password", classes="field-label")
            yield Input(placeholder="password", password=True, id="password")
            with Horizontal(id="login-actions"):
                yield Button("Connect", id="connect", variant="primary")
            yield Static("", id="login-status")
        yield Footer()

    def on_mount(self) -> None:
        config = self.app.config
        if config.username:
            self.query_one("#password", Input).focus()
        else:
            self.query_one("#username", Input).focus()
        self._apply_layout()
        if self.graphics.mode == "chafa":
            self._render_logo_art()

    def on_resize(self) -> None:
        self._apply_layout()

    def _apply_layout(self) -> None:
        try:
            brand = self.query_one("#brand")
            tagline = self.query_one("#tagline")
            card = self.query_one("#login-card")
        except Exception:
            return
        height = self.app.size.height
        brand.display = height >= 34
        tagline.display = height >= 30
        card.set_class(height < 30, "compact")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "server":
            self.query_one("#username", Input).focus()
        elif event.input.id == "username":
            self.query_one("#password", Input).focus()
        else:
            self._connect()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "connect":
            self._connect()

    def _connect(self) -> None:
        server_input = self.query_one("#server", Input)
        username_input = self.query_one("#username", Input)
        password_input = self.query_one("#password", Input)
        try:
            server = normalise_server(server_input.value)
        except ValueError:
            self._set_status("Enter a server URL.", error=True)
            server_input.focus()
            return
        username = username_input.value.strip()
        if not username:
            self._set_status("Enter a username.", error=True)
            username_input.focus()
            return
        password = password_input.value

        server_input.value = server
        self._set_busy(True)
        self._set_status(f"Connecting to {server}…")
        self._login(server, username, password)

    @work(thread=True, exclusive=True, group="login")
    def _login(self, server: str, username: str, password: str) -> None:
        worker = get_current_worker()
        client = JellyfinClient(server, device_id=self.app.config.device_id)
        try:
            user = client.login(username, password)
        except AuthenticationError:
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self._login_failed, "Wrong username or password."
                )
            return
        except JellyfinError as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._login_failed, str(exc))
            return
        except Exception as exc:  # defensive: never leave the UI stuck
            if not worker.is_cancelled:
                self.app.call_from_thread(
                    self._login_failed, f"Unexpected error: {exc}"
                )
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._login_succeeded, client, user)

    def _login_succeeded(self, client: JellyfinClient, user: dict) -> None:
        self.app.start_session(client, user)

    def _login_failed(self, message: str) -> None:
        self._set_busy(False)
        self._set_status(message, error=True)
        self.query_one("#password", Input).focus()

    def _set_busy(self, busy: bool) -> None:
        for input_id in ("#server", "#username", "#password"):
            self.query_one(input_id, Input).disabled = busy
        button = self.query_one("#connect", Button)
        button.disabled = busy
        button.label = "Connecting…" if busy else "Connect"

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self._status_text = message
        self._status_error = error
        style = (
            f"bold {self.app.current_theme.error}" if error else "dim"
        )
        self.query_one("#login-status", Static).update(Text(message, style=style))

    def refresh_theme(self) -> None:
        if not self.is_mounted:
            return
        if self._status_text:
            self._set_status(self._status_text, error=self._status_error)

    @work(thread=True, exclusive=True, group="logo")
    def _render_logo_art(self) -> None:
        worker = get_current_worker()
        art = chafa_ansi(
            str(asset_path("jellyfin-icon.png")), width=26, height=9, colors="full"
        )
        if not worker.is_cancelled:
            self.app.call_from_thread(self._set_logo_art, art)

    def _set_logo_art(self, art: str | None) -> None:
        if not self.is_mounted:
            return
        widget = self.query_one("#login-logo-art", Static)
        if not art:
            widget.display = False
            return
        widget.update(Text.from_ansi(art))

    def action_quit_app(self) -> None:
        self.app.exit()
