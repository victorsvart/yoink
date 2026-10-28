#!/usr/bin/env bash
set -euo pipefail

RELEASES_URL="https://github.com/victorsvart/yoink/releases"
UV_VERSION="0.13.6"
YOINK_TEMP_DIR=""

fail() { printf '\nyoink installer: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
download() { curl --proto '=https' --proto-redir '=https' --tlsv1.2 --fail --silent --show-error --location "$1" --output "$2"; }

cleanup() {
    # bash can drop main's locals before running EXIT. keep this path outside it
    if [[ -n "$YOINK_TEMP_DIR" ]]; then
        printf '\nRemoving temporary installer files...\n' >&2
        rm -rf -- "$YOINK_TEMP_DIR"
        YOINK_TEMP_DIR=""
    fi
}

uv_is_compatible() {
    local found major minor patch required_major required_minor required_patch
    found=$("$1" --version 2>/dev/null) || return 1
    [[ "$found" =~ ^uv\ ([0-9]+)\.([0-9]+)\.([0-9]+)([[:space:]]|$) ]] || return 1
    major=$((10#${BASH_REMATCH[1]}))
    minor=$((10#${BASH_REMATCH[2]}))
    patch=$((10#${BASH_REMATCH[3]}))
    IFS=. read -r required_major required_minor required_patch <<<"$UV_VERSION"
    (( major > required_major || (major == required_major && minor > required_minor) ||
       (major == required_major && minor == required_minor && patch >= required_patch) ))
}

confirm() {
    local answer
    printf '\n%s [y/N] ' "$1" >&2
    # stdin might be the installer itself. eating shell code isn't consent
    if ! read -r answer </dev/tty; then
        return 1
    fi
    case "$answer" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

checksum() {
    if have sha256sum; then sha256sum "$1" | cut -d ' ' -f 1
    elif have shasum; then shasum -a 256 "$1" | cut -d ' ' -f 1
    else fail "SHA-256 verification needs shasum or sha256sum."; fi
}

verify_asset() {
    local name="$1" expected actual
    expected=$(awk -v name="$name" '$2 == name { print $1 }' "$temporary/SHA256SUMS")
    [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || fail "Missing or invalid checksum for $name."
    actual=$(checksum "$temporary/$name")
    [[ "$actual" == "$expected" ]] || fail "Checksum failed for $name. Nothing installed."
}

offer_path_setup() {
    case ":$PATH:" in *":$bin_directory:"*) return ;; esac
    local profile line block escaped shell_name="${SHELL:-}"
    case "${shell_name##*/}" in
        zsh) profile="$HOME/.zshrc" ;;
        bash) profile="$HOME/.bashrc" ;;
        *) printf '\nAdd %s to PATH to use yoink.\n' "$bin_directory"; return ;;
    esac
    printf -v escaped '%q' "$bin_directory"
    line="export PATH=$escaped:\"\$PATH\""
    if confirm "Add $bin_directory to PATH in $profile?"; then
        if [[ ! -f "$profile" ]] || ! grep -Fqx "$line" "$profile"; then
            block=$(printf '\n# yoink PATH (%s)\n%s\n# end yoink PATH\n' "$data_directory" "$line")
            printf '%s\n' "$block" >>"$profile"
            "$python" -m yoink.installation record-path --root "$data_directory" --profile "$profile" --block "$block"
        fi
        printf '\nOpen a new terminal, or run:\n  %s\n' "$line"
    else
        printf '\nTo use yoink in this terminal, run:\n  %s\n' "$line"
    fi
}

main() {
    local version="" browser=true
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --version)
                [[ $# -ge 2 ]] || fail "--version needs a version such as 0.1.0."
                version="$2"; shift 2 ;;
            --without-browser) browser=false; shift ;;
            --help|-h)
                printf 'Usage: bash install.sh [--version X.Y.Z] [--without-browser]\n'
                printf 'Installs a GitHub release after asking for consent. No --yes shortcut.\n'
                return ;;
            *) fail "Unknown option: $1" ;;
        esac
    done
    have curl || fail "curl is required to download release files."
    printf 'Checking existing dependencies...\n'
    local system temporary data_directory bin_directory needs_ffmpeg=false runtime="" python=""
    local configure_args=(configure)
    system=$(uname -s)
    case "$system" in Darwin|Linux) ;; *) fail "This installer supports macOS and Linux." ;; esac
    data_directory="${YOINK_INSTALL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/yoink/install}"
    bin_directory="${YOINK_BIN_DIR:-$HOME/.local/bin}"
    [[ "$data_directory" == /* && "$bin_directory" == /* ]] || fail "Install paths must be absolute."
    if ! have ffmpeg || ! have ffprobe; then needs_ffmpeg=true; fi
    if [[ "$system" == Darwin ]] && $needs_ffmpeg && ! have brew; then
        fail "FFmpeg is missing and Homebrew is not installed. Choose a setup option:

  1. Install Homebrew: https://brew.sh
     Then run: brew install ffmpeg

  2. Install ffmpeg and ffprobe directly: https://ffmpeg.org/download.html
     Put both commands on your PATH.

Rerun this installer after setup. Nothing was installed."
    fi
    if [[ "$system" == Linux ]] && { $needs_ffmpeg || $browser; } && ! have apt-get; then
        fail "Automatic system dependencies need Debian/Ubuntu. Install FFmpeg yourself and use --without-browser on other Linux distributions."
    fi
    if [[ "$system" == Linux && $(id -u) != 0 ]] && { $needs_ffmpeg || $browser; } && ! have sudo; then
        fail "sudo is needed to install system dependencies."
    fi
    temporary=$(mktemp -d)
    YOINK_TEMP_DIR="$temporary"
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    local installed_python="$data_directory/tools/yoink-cli/bin/python"
    if [[ -x "$installed_python" ]]; then
        python=$("$installed_python" -c 'import sys; from pathlib import Path; sys.exit(1) if sys.version_info < (3, 13) else print(Path(sys.executable).resolve())' 2>/dev/null || true)
    fi
    if have uv; then
        runtime=$(command -v uv)
        if uv_is_compatible "$runtime"; then
            printf 'Found compatible uv: %s\n' "$runtime"
            if [[ -z "$python" ]]; then
                python=$("$runtime" --no-config python find --system --no-python-downloads '>=3.13' 2>/dev/null || true)
            fi
        else
            printf 'Existing uv needs %s or later; a temporary helper will be used instead.\n' "$UV_VERSION"
            runtime=""
        fi
    fi
    if [[ -z "$python" ]]; then
        local candidate
        for candidate in python3 python3.15 python3.14 python3.13; do
            if have "$candidate" && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 13))' 2>/dev/null; then
                python=$("$candidate" -c 'import sys; print(sys.executable)')
                break
            fi
        done
    fi
    if [[ -z "$version" ]]; then
        printf 'Finding the latest published release...\n'
        download "$RELEASES_URL/latest/download/VERSION" "$temporary/VERSION" ||
            fail "No downloadable release found. Check $RELEASES_URL."
        version=$(cat "$temporary/VERSION")
    fi
    [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "Expected a stable version such as 0.1.0."
    local base wheel asset
    base="$RELEASES_URL/download/v$version"
    wheel="yoink_cli-$version-py3-none-any.whl"
    printf 'Downloading release files and verifying SHA-256 checksums...\n'
    download "$base/SHA256SUMS" "$temporary/SHA256SUMS"
    for asset in "$wheel" constraints.txt; do
        download "$base/$asset" "$temporary/$asset"
        verify_asset "$asset"
    done

    printf '\nYoink %s\n----------\n\n' "$version"
    printf 'The installer will install or update:\n'
    printf '  - Yoink and its locked Python dependencies (yt-dlp, Playwright, etc.)\n'
    if [[ -n "$runtime" ]]; then
        printf '  - Reuse your existing uv installer\n'
    else
        printf '  - Temporary uv installer helper (removed when finished)\n'
    fi
    if [[ -n "$python" ]]; then
        printf '  - Reuse compatible Python: %s\n' "$python"
    else
        printf '  - Python 3.13, needed to run Yoink, under %s\n' "$data_directory"
    fi
    printf '  - The yoink command in %s\n' "$bin_directory"
    printf '  - App packages stay isolated; existing matching packages are reused\n'
    if $needs_ffmpeg; then
        if [[ "$system" == Darwin ]]; then
            printf '  - Missing FFmpeg through your existing Homebrew\n'
        else
            printf '  - FFmpeg through apt (may require your sudo password)\n'
        fi
    else
        printf '  - Reuse installed ffmpeg and ffprobe\n'
    fi
    if $browser; then
        printf '  - Reuse Chromium, or install a private copy for Yoink if missing (large download)\n'
        if [[ "$system" == Linux ]]; then
            printf '  - Chromium system libraries through apt (may require sudo)\n'
        fi
    fi
    printf '  - Ownership receipt so yoink uninstall can remove only what Yoink added\n'
    printf '\nNo passwords or browser cookies are read during installation.\n'
    confirm "Install Yoink and the dependencies listed above?" || fail "Cancelled. Nothing installed."

    umask 077
    mkdir -p "$data_directory" "$bin_directory"
    data_directory=$(cd "$data_directory" && pwd -P)
    bin_directory=$(cd "$bin_directory" && pwd -P)
    if [[ -z "$runtime" ]]; then
        printf '\nDownloading temporary uv installer helper...\n'
        download "https://astral.sh/uv/$UV_VERSION/install.sh" "$temporary/uv-install.sh"
        UV_UNMANAGED_INSTALL="$temporary/uv" sh "$temporary/uv-install.sh"
        runtime="$temporary/uv/uv"
    fi
    export UV_TOOL_DIR="$data_directory/tools"
    export UV_TOOL_BIN_DIR="$bin_directory"
    if [[ -z "$python" ]]; then
        [[ ! -e "$data_directory/python" ]] || fail "An untracked private Python directory already exists. Select its interpreter manually before installing."
        configure_args+=(--python-owned)
        export UV_PYTHON_INSTALL_DIR="$data_directory/python"
        printf '\nInstalling Python 3.13 for Yoink...\n'
        "$runtime" --no-config python install --no-bin 3.13
        python=$("$runtime" --no-config python find --system --no-python-downloads 3.13)
    fi
    printf '\nInstalling Yoink %s and resolving only missing app packages...\n' "$version"
    local wheel_hash
    wheel_hash=$(checksum "$temporary/$wheel")
    "$runtime" --no-config tool install --python "$python" \
        --constraints "$temporary/constraints.txt" "$base/$wheel#sha256=$wheel_hash"
    python="$UV_TOOL_DIR/yoink-cli/bin/python"
    $needs_ffmpeg && configure_args+=(--ffmpeg)
    $browser && configure_args+=(--browser)
    "$python" -m yoink.installation "${configure_args[@]}" --root "$data_directory" \
        --command "$bin_directory/yoink"
    "$bin_directory/yoink" --version
    printf '\nInstalled. Try:\n  %s --help\n' "$bin_directory/yoink"
    offer_path_setup
    cleanup
    trap - EXIT INT TERM
    printf '\nRemoved temporary downloads and installer-only helpers.\n'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
