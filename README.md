# JellyTUI

A keyboard-driven [Jellyfin](https://jellyfin.org) client for the terminal,
styled with [Textual](https://textual.textualize.io) and playing media in
[mpv](https://mpv.io).

```
▶  JellyTUI
   Jellyfin in your terminal
```

Browse your libraries, jump into a show's seasons and episodes, resume what you
were watching, search the whole server, switch audio and subtitle tracks, and
keep your Jellyfin progress in sync — all without leaving the terminal.

## Features

- **Browse** movies, shows, seasons, episodes, collections, music artists,
  albums and tracks from every Jellyfin library.
- **Continue watching** and **Recently added** views, plus server-wide search.
- **Direct playback in mpv** using the Jellyfin direct-play URL. A separate mpv
  window opens for video; audio plays without one.
- **Audio and subtitle track picker** (`a` / `v`). Embedded tracks are selected
  directly in mpv; separate subtitle files hosted by Jellyfin are added to mpv
  on demand. Image-based subtitles (PGS/VobSub) work too, since mpv renders
  them from the container.
- **Resume prompt** when an item has a saved position, and progress is reported
  back to Jellyfin while playing (every 10 seconds, on pause and on stop).
- **Watched toggle** (`r`), stored on the server.
- **Cover art** in Kitty/Sixel terminals via the Kitty graphics protocol or
  Sixel. On other terminals JellyTUI renders art with
  [chafa](https://hpjansson.org/chafa/); without chafa it falls back to a clean
  text-only layout.
- **Live Omarchy and pywal theming**: the TUI follows the current Omarchy theme
  or pywal palette and repaints when either changes, without a restart.
- **Language preferences** for audio and subtitles, including forced subtitles.
- Everything is keyboard-driven and sized for real terminals.

## Requirements

- Python 3.12+
- [mpv](https://mpv.io/installation/) on `PATH`
- A Jellyfin server (tested against Jellyfin 12.x)
- Optional: [chafa](https://hpjansson.org/chafa/) for cover art on terminals
  without Kitty/Sixel graphics
- Optional: a terminal with Kitty graphics (Kitty, WezTerm, Ghostty, …) or
  Sixel support for pixel-perfect artwork

## Install

From a checkout, the installer checks for `mpv` and `chafa`, offers to install
missing system packages, and installs or updates the `jellytui` command. It
prefers [uv](https://docs.astral.sh/uv/) and falls back to
[pipx](https://pipx.pypa.io/) when uv is unavailable; if neither is present it
installs uv.

```sh
./install.sh
```

Useful installer options:

```sh
./install.sh --yes       # don't prompt before installing system packages
./install.sh --pull      # git pull --ff-only before reinstalling
./install.sh --no-deps   # don't touch mpv/chafa
./install.sh --no-dev    # don't update a local .venv
```

Alternatively, install it directly with uv:

```sh
uv tool install .
jellytui
```

Or run it from a checkout without installing:

```sh
uv sync
uv run jellytui
```

For development, install the project and its test dependencies in editable mode:

```sh
uv sync
uv run pytest
```

## Usage

```sh
jellytui --server http://your-server:8096
```

Enter your username and password on the login screen. JellyTUI stores the
resulting access token (not your password) in
`~/.config/jellytui/config.json`, with file mode `0600`. The device ID and
language/playback preferences live in the same file.

### Options

```
--server URL             Jellyfin URL, including any base path
--username NAME          Prefill the username
--logout                 Forget the saved session
--mpv PATH               Path to the mpv executable
--mpv-arg ARG            Extra argument for mpv (repeatable)
--audio-language LANG    Preferred audio language (eng, en, English, …)
--subtitle-language LANG Preferred subtitle language
--subtitles MODE         auto | off | forced
--no-images              Disable cover art
--graphics MODE          auto | kitty | sixel | chafa | text
--theme SOURCE           auto | omarchy | pywal | jellyfin
--no-theme-watch         Don't watch palette files for live changes
--no-progress-report     Don't report progress to Jellyfin
--diagnostics            Print graphics/theme/chafa/mpv detection details
--config PATH            Use a different config file
```

Environment overrides:

- `JELLYTUI_CONFIG` — config file path
- `JELLYTUI_GRAPHICS` — force a graphics backend (`auto`, `kitty`, `sixel`, `chafa`, `text`)
- `JELLYTUI_CHAFA` — path to chafa
- `JELLYTUI_THEME` — force a theme source (`auto`, `omarchy`, `pywal`, `jellyfin`)
- `JELLYTUI_OMARCHY_COLORS` — path to an Omarchy `colors.toml`
- `JELLYTUI_PYWAL_COLORS` — path to a pywal `colors.json` or `colors` file

Run `jellytui --diagnostics` to see exactly what was detected.

## Theming

By default (`theme = "auto"`) JellyTUI follows, in order:

1. The current **Omarchy** theme at
   `~/.local/state/omarchy/current/theme/colors.toml`
2. A **pywal** palette at `~/.cache/wal/colors.json` or `~/.cache/wal/colors`
3. The built-in Jellyfin theme

The palette is re-read once a second. Running `omarchy theme set …` or
`wal -i …` recolours the interface — including tables, dialogs, progress
accents, the sidebar and the header — while it is running. Files are replaced
atomically by these tools, so JellyTUI re-checks the paths instead of holding
them open.

Use `--theme pywal` to prefer pywal on an Omarchy machine, `--theme jellyfin`
to ignore both, or `--no-theme-watch` to read the palette only at startup. A
theme switch is also reported with a short notification. JellyTUI only reads
these files; it never changes Omarchy's or pywal's configuration.

`jellytui --diagnostics` prints the resolved theme source, its title and the
palette paths it found.

## Keys

| Key | Action |
| --- | --- |
| `↑` / `↓` | Move through lists |
| `←` / `→` | Move between the sidebar and the item list |
| `Enter` | Play, or open a show/season/album |
| `Backspace` / `Esc` | Go back |
| `/` | Search the whole server |
| `Ctrl+R` | Refresh |
| `Space` | Pause / resume |
| `,` / `.` | Seek 10 seconds |
| `Shift+←` / `Shift+→` | Seek 10 seconds |
| `[` / `]` | Seek 1 minute |
| `-` / `=` | Volume down / up |
| `a` | Choose an audio track |
| `v` | Choose a subtitle track |
| `S` | Stop playback |
| `r` | Mark watched / unwatched |
| `i` | Toggle cover art |
| `L` | Log out |
| `q` | Quit |

## Graphics detection

At startup JellyTUI asks the terminal whether it supports the Kitty graphics
protocol or Sixel, using the same protocol probe as `textual-image`. If either
is available, the Jellyfin logo on the login screen and cover art in the
details pane are rendered as real images.

If the terminal has no graphics support, JellyTUI uses `chafa` to render the
logo and cover art as ANSI art. If chafa is not installed, the interface stays
fully functional with a text-only layout.

The `--graphics` flag is useful for testing or for terminals where the probe
cannot get an answer (for example through some multiplexers):

```sh
jellytui --graphics kitty
jellytui --graphics chafa
jellytui --graphics text
```

## Notes and limitations

- JellyTUI always requests **direct play** and lets mpv handle the container.
  This keeps A/V quality intact and track switching instant, but it does not ask
  Jellyfin to transcode. Formats mpv cannot decode will fail to play.
- Playback progress is reported as `DirectPlay`; Jellyfin's "now playing"
  dashboard shows the session accordingly.
- Live TV, plugin content and playlist editing are not implemented.
- The mpv window has its own keyboard controls (space, arrows, `f`, `9`/`0`,
  subtitles via `j`, etc.) in addition to the TUI controls.

## Development

```sh
uv sync
uv run pytest
```

The test suite includes unit tests for the API client and models, an mpv IPC
integration test using a generated video with multiple audio/subtitle tracks,
Textual UI smoke tests, and a full playback test where a fake Jellyfin server
streams a generated video into mpv and verifies the progress reports that come
back. Optional live tests run against a real server when `JELLYFIN_URL`,
`JELLYFIN_USERNAME` and `JELLYFIN_PASSWORD` are set.

## Credits

The Jellyfin icon in `jellytui/assets/` comes from the
[Jellyfin UX repository](https://github.com/jellyfin/jellyfin-ux) and is used
under CC-BY-SA-4.0. Jellyfin is a trademark of the Jellyfin project. JellyTUI is
an independent client, not affiliated with or endorsed by the Jellyfin project.

Media playback is powered by mpv. Terminal UI by Textual. ANSI art fallback by
chafa.
