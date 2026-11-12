"""Remote metadata lookup screen (MusicBrainz and other Jellyfin providers)."""

from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from ..api import JellyfinClient, JellyfinError
from ..models import RemoteSearchResult


class LookupScreen(ModalScreen[bool | None]):
    """Search remote providers and apply a result to an item."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "apply", "Apply", show=False),
    ]

    def __init__(
        self,
        client: JellyfinClient,
        *,
        target_item_id: str,
        search_type: str,
        name: str,
        year: int | None,
        description: str,
    ) -> None:
        super().__init__()
        self.client = client
        self.target_item_id = target_item_id
        self.search_type = search_type
        self.initial_name = name
        self.initial_year = year
        self.description = description
        self.results: list[RemoteSearchResult] = []
        self.selected_result: RemoteSearchResult | None = None
        self._busy = False

    def compose(self) -> ComposeResult:
        with Vertical(id="lookup-panel"):
            yield Static("LOOK UP METADATA", id="dialog-title")
            yield Static(self.description, classes="dialog-subject")
            yield Static(
                "Applying a result replaces the target item's metadata and may "
                "replace its images.",
                classes="lookup-hint",
            )
            with Horizontal(classes="lookup-search-row"):
                with Vertical():
                    yield Label("Search name", classes="field-label")
                    yield Input(value=self.initial_name, id="lookup-name")
                with Vertical(classes="lookup-year"):
                    yield Label("Year", classes="field-label")
                    yield Input(
                        value=str(self.initial_year or ""),
                        id="lookup-year",
                    )
            with Horizontal(classes="lookup-actions"):
                yield Button("Search", id="lookup-search", variant="primary")
                yield Button("Apply", id="lookup-apply", disabled=True)
                yield Button("Cancel", id="lookup-cancel")
                yield Checkbox("Replace images", value=True, id="lookup-images")
            yield Static("", id="lookup-status")
            yield OptionList(id="lookup-results")
            yield Static("", id="lookup-details")

    def on_mount(self) -> None:
        self.query_one("#lookup-name", Input).focus()
        self._search()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {"lookup-name", "lookup-year"}:
            self._search()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "lookup-search":
            self._search()
        elif event.button.id == "lookup-apply":
            self.action_apply()
        elif event.button.id == "lookup-cancel":
            self.action_cancel()

    def action_cancel(self) -> None:
        if not self._busy:
            self.dismiss(None)

    def action_apply(self) -> None:
        if self._busy or self.selected_result is None:
            return
        name_input = self.query_one("#lookup-name", Input)
        if not name_input.value.strip():
            self._set_status("Enter a name to apply.", error=True)
            return
        self._set_busy(True)
        self._set_status("Applying metadata and refreshing the item…")
        self._apply_worker(
            self.selected_result,
            self.query_one("#lookup-images", Checkbox).value,
        )

    @work(thread=True, exclusive=True, group="lookup-apply")
    def _apply_worker(
        self, result: RemoteSearchResult, replace_images: bool
    ) -> None:
        worker = get_current_worker()
        try:
            self.client.apply_remote_search(
                self.target_item_id, result, replace_images=replace_images
            )
        except JellyfinError as exc:
            if not worker.is_cancelled and self.is_mounted:
                self.app.call_from_thread(self._apply_failed, str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            if not worker.is_cancelled and self.is_mounted:
                self.app.call_from_thread(
                    self._apply_failed, f"Unexpected error: {exc}"
                )
            return
        if not worker.is_cancelled and self.is_mounted:
            self.app.call_from_thread(self.dismiss, True)

    def _search(self) -> None:
        name = self.query_one("#lookup-name", Input).value.strip()
        if not name:
            self._set_status("Enter a name to search for.", error=True)
            return
        year_text = self.query_one("#lookup-year", Input).value.strip()
        year: int | None = None
        if year_text:
            try:
                year = int(year_text)
            except ValueError:
                self._set_status("Year must be a whole number.", error=True)
                return
        self._set_status("Searching…")
        self.query_one("#lookup-apply", Button).disabled = True
        self._search_worker(name, year)

    @work(thread=True, exclusive=True, group="lookup-search")
    def _search_worker(self, name: str, year: int | None) -> None:
        worker = get_current_worker()
        search_info: dict[str, object] = {"Name": name}
        if year:
            search_info["Year"] = year
        try:
            results = self.client.remote_search(
                self.target_item_id, self.search_type, search_info
            )
        except JellyfinError as exc:
            if not worker.is_cancelled and self.is_mounted:
                self.app.call_from_thread(self._search_failed, str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            if not worker.is_cancelled and self.is_mounted:
                self.app.call_from_thread(
                    self._search_failed, f"Unexpected error: {exc}"
                )
            return
        if not worker.is_cancelled and self.is_mounted:
            self.app.call_from_thread(self._search_done, results)

    def _search_done(self, results: list[RemoteSearchResult]) -> None:
        self.results = results
        results_widget = self.query_one("#lookup-results", OptionList)
        results_widget.clear_options()
        if not results:
            results_widget.add_option(
                Option("No results — check that a metadata provider is enabled", disabled=True)
            )
            self._set_selected_result(None)
            self._set_status(
                "No remote results. Enable the MusicBrainz provider in Jellyfin, "
                "or try a different name/year.",
                error=True,
            )
            return
        options = [
            Option(
                Text(f"{result.label}"),
                id=str(index),
            )
            for index, result in enumerate(results)
        ]
        results_widget.add_options(options)
        results_widget.highlighted = 0
        results_widget.focus()
        self._select_result(0)
        self._set_status(
            f"{len(results)} result{'s' if len(results) != 1 else ''} found."
        )

    def _search_failed(self, message: str) -> None:
        self._set_status(message, error=True)

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if event.option.id is None:
            return
        try:
            index = int(event.option.id)
        except ValueError:
            return
        self._select_result(index)

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        if event.option.id is None:
            return
        try:
            index = int(event.option.id)
        except ValueError:
            return
        self._select_result(index)
        self.action_apply()

    def _select_result(self, index: int | None) -> None:
        if index is None or not 0 <= index < len(self.results):
            self._set_selected_result(None)
            return
        self._set_selected_result(self.results[index])

    def _set_selected_result(self, result: RemoteSearchResult | None) -> None:
        self.selected_result = result
        self.query_one("#lookup-apply", Button).disabled = (
            result is None or self._busy
        )
        if result is None:
            self.query_one("#lookup-details", Static).update(Text(""))
            return
        details = Text()
        details.append(result.name, style="bold")
        if result.year:
            details.append(f"  ({result.year})", style="dim")
        details.append("\n")
        if result.provider_name:
            details.append(result.provider_name, style="dim")
            details.append("\n")
        if result.album_artist:
            details.append("Album artist: ", style="dim")
            details.append(f"{result.album_artist}\n")
        if result.artists:
            details.append("Artists: ", style="dim")
            details.append(f"{', '.join(result.artists)}\n")
        if result.provider_ids:
            details.append("Provider IDs: ", style="dim")
            details.append(
                ", ".join(
                    f"{key}={value}"
                    for key, value in result.provider_ids.items()
                )
                + "\n"
            )
        if result.overview:
            details.append("\n")
            details.append(result.overview.strip())
        self.query_one("#lookup-details", Static).update(details)

    def _apply_failed(self, message: str) -> None:
        self._set_busy(False)
        self._set_status(message, error=True)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget in self.query(Input):
            widget.disabled = busy
        self.query_one("#lookup-search", Button).disabled = busy
        self.query_one("#lookup-cancel", Button).disabled = busy
        self.query_one("#lookup-images", Checkbox).disabled = busy
        self.query_one("#lookup-apply", Button).disabled = (
            busy or self.selected_result is None
        )

    def _set_status(self, message: str, *, error: bool = False) -> None:
        style = f"bold {self.app.current_theme.error}" if error else "dim"
        self.query_one("#lookup-status", Static).update(
            Text(message, style=style)
        )
