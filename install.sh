#!/usr/bin/env bash
#
# JellyTUI installer and updater.
#
# Checks the system for mpv/chafa, installs what it can with the system package
# manager, then installs or updates JellyTUI as a tool. uv is preferred; pipx
# is used when uv is unavailable. If neither exists, uv is installed first.
#
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ASSUME_YES=0
PULL=0
SKIP_DEPS=0
SKIP_DEV=0
BACKEND=""

usage() {
    cat <<'EOF'
Usage: ./install.sh [options]

Installs or updates JellyTUI and its runtime dependencies.

Options:
  -y, --yes       Install missing system packages without asking
      --pull      Run "git pull --ff-only" before installing
      --no-deps   Do not touch system packages (mpv/chafa)
      --no-dev    Do not update a local .venv development environment
  -h, --help      Show this help
EOF
}

log()  { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

while (($#)); do
    case "$1" in
        -y|--yes) ASSUME_YES=1 ;;
        --pull) PULL=1 ;;
        --no-deps) SKIP_DEPS=1 ;;
        --no-dev) SKIP_DEV=1 ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
    shift
done

[[ -f "$SCRIPT_DIR/pyproject.toml" ]] || die "run install.sh from the JellyTUI checkout"
[[ -f "$SCRIPT_DIR/jellytui/__init__.py" ]] || die "the checkout looks incomplete"

if [[ $EUID -eq 0 ]]; then
    SUDO=()
elif command -v sudo >/dev/null 2>&1; then
    SUDO=(sudo)
else
    SUDO=()
fi

confirm() {
    [[ $ASSUME_YES -eq 1 ]] && return 0
    [[ -t 0 ]] || return 1
    local answer
    read -r -p "$1 [Y/n] " answer
    [[ -z "$answer" || "$answer" =~ ^[Yy] ]]
}

detect_pm() {
    local pm
    for pm in pacman apt-get dnf zypper apk brew; do
        if command -v "$pm" >/dev/null 2>&1; then
            printf '%s\n' "$pm"
            return 0
        fi
    done
    return 1
}

install_pkgs() {
    local pkgs=("$@")
    case "$PM" in
        pacman)
            "${SUDO[@]}" pacman -S --needed --noconfirm "${pkgs[@]}"
            ;;
        apt-get)
            "${SUDO[@]}" apt-get update
            "${SUDO[@]}" apt-get install -y "${pkgs[@]}"
            ;;
        dnf)
            "${SUDO[@]}" dnf install -y "${pkgs[@]}"
            ;;
        zypper)
            "${SUDO[@]}" zypper --non-interactive install "${pkgs[@]}"
            ;;
        apk)
            "${SUDO[@]}" apk add --no-cache "${pkgs[@]}"
            ;;
        brew)
            brew install "${pkgs[@]}"
            ;;
        *)
            return 1
            ;;
    esac
}

python_ok() {
    command -v python3 >/dev/null 2>&1 || return 1
    python3 - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
}

install_uv() {
    if command -v uv >/dev/null 2>&1; then
        return 0
    fi
    if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
        if [[ -n "$PM" ]]; then
            log "Installing curl to download uv"
            install_pkgs curl || die "could not install curl"
        else
            die "curl or wget is required to install uv"
        fi
    fi
    log "Installing uv"
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    else
        wget -qO- https://astral.sh/uv/install.sh | sh
    fi
    export PATH="$HOME/.local/bin:$PATH"
    hash -r
    command -v uv >/dev/null 2>&1 || die "uv installation failed"
}

# ----------------------------------------------------------------- deps

PM="$(detect_pm || true)"
MISSING=()
command -v mpv >/dev/null 2>&1 || MISSING+=(mpv)
command -v ffmpeg >/dev/null 2>&1 || MISSING+=(ffmpeg)
command -v chafa >/dev/null 2>&1 || MISSING+=(chafa)

if [[ ${#MISSING[@]} -gt 0 ]]; then
    if [[ $SKIP_DEPS -eq 1 ]]; then
        warn "missing optional/recommended packages: ${MISSING[*]} (skipped by --no-deps)"
    elif [[ -z "$PM" ]]; then
        warn "could not find a supported package manager"
        warn "install manually: mpv (required), chafa (recommended for cover art)"
    else
        log "Missing packages: ${MISSING[*]}"
        if confirm "Install them with $PM now?"; then
            if ! install_pkgs "${MISSING[@]}"; then
                warn "package installation failed"
                warn "install manually: ${MISSING[*]}"
            fi
        else
            warn "skipping package installation"
        fi
    fi
fi

hash -r
MPV_OK=0
command -v mpv >/dev/null 2>&1 && MPV_OK=1
CHAFA_OK=0
command -v chafa >/dev/null 2>&1 && CHAFA_OK=1

if [[ $MPV_OK -eq 0 ]]; then
    warn "mpv is not installed: browsing will work, but video playback will not start"
    warn "install mpv before trying to play a video"
fi
if [[ $CHAFA_OK -eq 0 ]]; then
    log "chafa is not installed; terminals without Kitty/Sixel graphics will use the text UI"
fi
FFMPEG_OK=0
command -v ffmpeg >/dev/null 2>&1 && FFMPEG_OK=1
AUDIO_TOOL=""
for tool in pw-play pacat aplay; do
    if command -v "$tool" >/dev/null 2>&1; then
        AUDIO_TOOL="$tool"
        break
    fi
done
if [[ $FFMPEG_OK -eq 0 ]]; then
    warn "ffmpeg is not installed; music playback will fall back to mpv"
fi
if [[ -z "$AUDIO_TOOL" ]]; then
    warn "no pw-play/pacat/aplay found; the ffmpeg music backend will fall back to mpv"
fi

# ------------------------------------------------------------- tool backend

if command -v uv >/dev/null 2>&1; then
    BACKEND=uv
elif command -v pipx >/dev/null 2>&1; then
    BACKEND=pipx
    if ! python_ok; then
        warn "pipx is available but Python 3.12+ was not found"
        log "Installing uv to provide a suitable Python"
        install_uv
        BACKEND=uv
    fi
else
    log "Neither uv nor pipx is available; installing uv"
    install_uv
    BACKEND=uv
fi

if [[ $PULL -eq 1 ]]; then
    if ! command -v git >/dev/null 2>&1; then
        [[ -n "$PM" ]] || die "--pull requires git"
        log "Installing git"
        install_pkgs git || die "could not install git"
    fi
    [[ -d "$SCRIPT_DIR/.git" ]] || die "--pull requires a git checkout"
    log "Updating source"
    git -C "$SCRIPT_DIR" pull --ff-only
fi

# --------------------------------------------------------------- install

if [[ "$BACKEND" == "uv" ]]; then
    log "Installing/updating JellyTUI with uv"
    if ! uv tool install --force "$SCRIPT_DIR"; then
        die "uv tool install failed; check your network and Python setup"
    fi
    BIN_DIR="$(uv tool dir --bin 2>/dev/null || true)"
elif [[ "$BACKEND" == "pipx" ]]; then
    log "Installing/updating JellyTUI with pipx"
    if ! pipx install --force "$SCRIPT_DIR"; then
        die "pipx install failed; check your network and Python setup"
    fi
    BIN_DIR="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)"
else
    die "no installation backend available"
fi
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
if [[ -d "$BIN_DIR" ]]; then
    BIN_DIR="$(CDPATH= cd -- "$BIN_DIR" && pwd)"
fi

# Update an existing development checkout as well, so .venv/bin/jellytui
# stays in sync with the source.
if [[ $SKIP_DEV -eq 0 && -x "$SCRIPT_DIR/.venv/bin/python" && "$BACKEND" == "uv" ]]; then
    log "Updating development environment (.venv)"
    if ! (cd "$SCRIPT_DIR" && uv sync --quiet); then
        warn "could not update the .venv development environment"
    fi
fi

if [[ "$BACKEND" == "uv" ]] && command -v pipx >/dev/null 2>&1; then
    if pipx list --short 2>/dev/null | awk '{print $1}' | grep -qx 'jellytui'; then
        warn "jellytui is also installed with pipx; run 'pipx uninstall jellytui' to avoid a stale copy"
    fi
fi

# ---------------------------------------------------------------- verify

if [[ -x "$BIN_DIR/jellytui" ]]; then
    log "Installed $("$BIN_DIR/jellytui" --version)"
else
    warn "could not find the installed jellytui binary in $BIN_DIR"
fi

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH; add it to use the jellytui command" ;;
esac

log "Dependency check: mpv=$([[ $MPV_OK -eq 1 ]] && echo yes || echo no) ffmpeg=$([[ $FFMPEG_OK -eq 1 ]] && echo yes || echo no) chafa=$([[ $CHAFA_OK -eq 1 ]] && echo yes || echo no) audio=${AUDIO_TOOL:-none}"
if [[ -f "${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/current/theme/colors.toml" ]]; then
    log "Omarchy theme detected; JellyTUI will follow it live"
elif [[ -f "${XDG_CACHE_HOME:-$HOME/.cache}/wal/colors.json" ]] || [[ -f "${XDG_CACHE_HOME:-$HOME/.cache}/wal/colors" ]]; then
    log "pywal palette detected; JellyTUI will follow it live"
fi
if [[ $MPV_OK -eq 0 ]]; then
    exit 1
fi

cat <<EOF

JellyTUI is ready.

  jellytui --server http://your-server:8096
  jellytui --diagnostics

The diagnostics command reports whether Kitty graphics, Sixel or chafa will
be used for the logo and cover art. See README.md for keys and options.
EOF
