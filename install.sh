#!/usr/bin/env bash
# auto-chapter installer

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

ok()    { echo -e "${GREEN}OK${NC}  $1"; }
warn()  { echo -e "${YELLOW}$1${NC}"; }
err()   { echo -e "${RED}$1${NC}"; }
info()  { echo -e "${BLUE}$1${NC}"; }
step()  { echo -e "\n${BOLD}$1${NC}"; }

# Allow the user to cancel cleanly with Ctrl+C at any prompt
trap 'echo; echo -e "${YELLOW}Installation cancelled.${NC}"; exit 130' INT

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# Everything below (uv sync, config.yml, .env) works relative to the project
# folder, so the installer also runs when started from somewhere else
# (e.g. `bash ~/projects/auto-chapter/install.sh`).
cd "$PROJECT_DIR"

# Settings and the token live outside the app folder, so deleting the folder to
# update keeps them. Same rule as auto_chapter.config.default_config_path().
if [[ "${XDG_CONFIG_HOME:-}" == /* ]]; then
    CONF_DIR="$XDG_CONFIG_HOME/auto-chapter"
else
    CONF_DIR="$HOME/.config/auto-chapter"
fi
CONF_FILE="$CONF_DIR/config.yml"
ENV_FILE="$CONF_DIR/.env"

# Read from pyproject.toml so this can never drift out of sync with the
# actual package being installed.
VERSION=$(grep -m1 '^version = ' "$PROJECT_DIR/pyproject.toml" | sed -E 's/version = "(.*)"/\1/')

# Trim leading/trailing whitespace from a string. Common paste issue on tokens
# and URLs when copying from a browser or terminal.
trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

# Ask a question and read the answer into the variable named $2. Every question
# goes through here: when there is no input at all (no terminal, e.g. started
# with a double click or from a script, or Ctrl+D), `read` fails at once, and
# re-asking would print the question forever. Stop instead. A last line without
# a final newline still counts as an answer.
ask() {
    if ! read -e -rp "$1" "$2" && [ -z "${!2}" ]; then
        echo
        warn "No input received -- installation stopped."
        exit 1
    fi
}

echo -e "${BOLD}"
echo "================================================================"
echo "  auto-chapter installer  (v$VERSION)"
echo "  Plex automatic chapter marker generator"
echo "================================================================"
echo -e "${NC}"
echo -e "${DIM}Press Ctrl+C at any time to cancel the installation.${NC}"

# --- 1. Check tools ---
step "Checking required system tools..."

missing=()

check_tool() {
    local tool=$1
    local install_hint=$2
    if command -v "$tool" &>/dev/null; then
        ok "$tool"
    else
        err "$tool is missing"
        echo -e "  ${DIM}Install: $install_hint${NC}"
        missing+=("$tool")
    fi
}

check_tool "uv"         "https://docs.astral.sh/uv/getting-started/installation/  (or: sudo pacman -S uv on Arch)"
check_tool "ffmpeg"     "sudo pacman -S ffmpeg  (or: sudo apt install ffmpeg)"
check_tool "ffprobe"    "comes with ffmpeg"
check_tool "mkvmerge"   "sudo pacman -S mkvtoolnix-cli  (or: sudo apt install mkvtoolnix)"
check_tool "mkvextract" "comes with mkvtoolnix"
check_tool "timeout"    "part of GNU coreutils (should already be installed)"

if [ ${#missing[@]} -gt 0 ]; then
    echo
    err "Missing tools: ${missing[*]}"
    echo "Please install them and run this script again."
    exit 1
fi

# --- 2. Python deps ---
step "Installing Python dependencies..."
# uv's output is long and noisy, so it goes to a file; it is shown only if
# the install fails (set -e would otherwise stop the script with no reason).
# Only the app's own dependencies: the test and lint tools (the "dev" extra)
# are for development, `uv sync --extra dev` adds them.
uv_log=$(mktemp)
if uv sync >"$uv_log" 2>&1; then
    ok "Dependencies installed"
    rm -f "$uv_log"
else
    err "Installing the Python dependencies failed. Last lines of uv's output:"
    tail -n 15 "$uv_log" | sed 's/^/  /'
    echo -e "  ${DIM}Full output: $uv_log${NC}"
    echo "Fix the cause shown above, then run this script again."
    exit 1
fi

# --- 2b. ffmpeg must have the filters the detection uses ---
# Same check the app runs at every start (one place decides what is enough).
step "Checking ffmpeg..."
if filter_problem=$(uv run python -c "
import sys
from auto_chapter.detector.ffmpeg import ffmpeg_filter_problem
problem = ffmpeg_filter_problem()
if problem:
    print(problem)
    sys.exit(1)
" 2>&1); then
    ok "ffmpeg has the filters auto-chapter needs"
else
    # Usually one sentence; if the app's code itself failed to load, Python's
    # whole error, which is needed to find the cause.
    err "$filter_problem"
    echo "Please fix this and run this script again."
    exit 1
fi

# --- 3. Configuration ---
mkdir -p "$CONF_DIR"
chmod 700 "$CONF_DIR"

# Up to 0.2.28 the settings lived in the app folder. Move them over once: copy,
# check the copy, and only then remove the old file. Never overwrite.
for name in config.yml .env; do
    old="$PROJECT_DIR/$name"
    new="$CONF_DIR/$name"
    [ -f "$old" ] || continue
    if [ -f "$new" ]; then
        warn "An old $name is still in $PROJECT_DIR -- not used, the one in $CONF_DIR is."
        continue
    fi
    cp -p "$old" "$new"
    if cmp -s "$old" "$new"; then
        rm -f "$old"
        ok "Moved $name to $CONF_DIR"
    else
        err "Could not copy $name to $CONF_DIR. Nothing was removed."
        exit 1
    fi
done
[ -f "$ENV_FILE" ] && chmod 600 "$ENV_FILE"

need_config=true
if [ -f "$CONF_FILE" ] && [ -f "$ENV_FILE" ]; then
    token_in_env=$(grep -E '^PLEX_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)
    # An empty or hand-broken config.yml (no "url:" line) is set up again.
    if [ -n "$token_in_env" ] && ! grep -q "YOUR-PLEX-SERVER" "$CONF_FILE" \
        && grep -qE '^\s*url:' "$CONF_FILE"; then
        ok "Existing configuration found (not overwritten)"
        need_config=false
    fi
fi

if [ "$need_config" = "true" ]; then
    step "Configuring your Plex server..."

    # Helper Python script for testing a Plex connection.
    # Outputs one of:
    #   FOLDERS:\n<path1>\n<path2>\n...
    #   ERROR_AUTH
    #   ERROR_CONNECT
    #   ERROR_NO_MOVIES
    test_plex() {
        local url="$1"
        local token="$2"
        # Safety net: hard 10s wall-clock limit on the whole subprocess.
        # PlexServer has its own 5s timeout, but we've seen edge cases where
        # bash `$(...)` swallows Ctrl+C until the subprocess exits. `timeout`
        # guarantees we return even if plexapi hangs internally.
        timeout 10 bash -c "uv run python <<'PYEOF' 2>/dev/null
import sys
try:
    from plexapi.server import PlexServer
    from plexapi.exceptions import Unauthorized, BadRequest
except Exception:
    print('ERROR_CONNECT')
    sys.exit(0)

try:
    s = PlexServer('$url', '$token', timeout=5)
except Unauthorized:
    print('ERROR_AUTH')
    sys.exit(0)
except (BadRequest, Exception):
    print('ERROR_CONNECT')
    sys.exit(0)

seen = set()
folders = []
try:
    for sec in s.library.sections():
        if sec.type != 'movie':
            continue
        for loc in (sec.locations or []):
            loc_s = loc if loc.endswith('/') else loc + '/'
            if loc_s not in seen:
                seen.add(loc_s)
                folders.append(loc_s)
except Exception:
    print('ERROR_CONNECT')
    sys.exit(0)

if not folders:
    print('ERROR_NO_MOVIES')
else:
    print('FOLDERS')
    for f in folders:
        print(f)
PYEOF
"
        # If timeout expired (exit code 124), report as connection error.
        if [ $? -eq 124 ]; then
            echo "ERROR_CONNECT"
        fi
    }

    # Validates an IP-or-hostname:port string.
    is_valid_addr() {
        local s="$1"
        # Strip http:// or https:// if present
        s="${s#http://}"
        s="${s#https://}"
        # Must be host:port with port = 1..65535 (basic check)
        if [[ "$s" =~ ^[A-Za-z0-9._-]+:[0-9]{1,5}$ ]]; then
            local port="${s##*:}"
            if [ "$port" -ge 1 ] && [ "$port" -le 65535 ]; then
                return 0
            fi
        fi
        return 1
    }

    plex_url=""
    plex_token=""

    # --- URL + token loop with pre-flight test ---
    while true; do
        # URL prompt (retry until format is valid)
        if [ -z "$plex_url" ]; then
            echo
            echo "Enter your Plex server address."
            echo -e "  ${DIM}Format: IP:PORT  -  Example: 192.168.1.100:32400${NC}"
            echo -e "  ${DIM}(Plex uses port 32400 by default)${NC}"
            while true; do
                ask "Plex server: " plex_addr
                plex_addr=$(trim "$plex_addr")
                if [ -z "$plex_addr" ]; then
                    echo -e "${YELLOW}Address cannot be empty. Try again.${NC}"
                    continue
                fi
                if is_valid_addr "$plex_addr"; then
                    break
                fi
                echo -e "${YELLOW}Invalid format. Use IP:PORT, like 192.168.1.100:32400${NC}"
            done
            if [[ "$plex_addr" != http://* ]] && [[ "$plex_addr" != https://* ]]; then
                plex_url="http://$plex_addr"
            else
                plex_url="$plex_addr"
            fi
        fi

        # Token prompt (retry until non-empty)
        if [ -z "$plex_token" ]; then
            echo
            echo "Now we need your Plex authentication token."
            echo
            echo "How to find it:"
            echo -e "  ${DIM}1. Open the Plex web app, go to any movie's info page${NC}"
            echo -e "  ${DIM}2. Click the three dots menu on the movie, then 'Get Info'${NC}"
            echo -e "  ${DIM}3. At the bottom of the popup, click 'View XML'${NC}"
            echo -e "  ${DIM}4. In the browser address bar, scroll to the very end of the URL${NC}"
            echo -e "  ${DIM}   The last parameter looks like: ...&X-Plex-Token=AbCdEf1234GhIj5678Kl${NC}"
            echo -e "  ${DIM}   Copy everything after 'X-Plex-Token='${NC}"
            echo
            while true; do
                ask "Plex token: " plex_token
                plex_token=$(trim "$plex_token")
                if [ -z "$plex_token" ]; then
                    echo -e "${YELLOW}Token cannot be empty. Try again.${NC}"
                    continue
                fi
                # Plex tokens are ~20 alphanumeric characters. If the user pasted
                # something wildly different (URL, shell command, extra spaces),
                # catch it here instead of feeding garbage to plexapi.
                if [[ "$plex_token" =~ ^[A-Za-z0-9]{15,40}$ ]]; then
                    break
                fi
                warn "That doesn't look like a Plex token."
                echo -e "${DIM}Plex tokens are ~20 alphanumeric characters (like: AbCdEf1234GhIj5678Kl).${NC}"
                echo -e "${DIM}Make sure you copied only the token, not the full URL.${NC}"
                echo
            done
        fi

        # --- Pre-flight check: try to connect ---
        step "Connecting to Plex..."
        result=$(test_plex "$plex_url" "$plex_token")
        first_line=$(echo "$result" | head -1)

        case "$first_line" in
            FOLDERS)
                ok "Connected to Plex successfully"
                # Extract folder list (skip the FOLDERS header line)
                plex_folders_raw=$(echo "$result" | tail -n +2)
                break
                ;;
            ERROR_AUTH)
                err "Authentication failed. The token is not valid for this server."
                echo
                echo "What would you like to fix?"
                echo "  1) Re-enter the Plex token"
                echo "  2) Re-enter both the server address and the token"
                echo "  3) Quit installer"
                while true; do
                    ask "Choose [1/2/3]: " fix
                    case "$fix" in
                        1) plex_token=""; break ;;
                        2) plex_url=""; plex_token=""; break ;;
                        3) exit 1 ;;
                        *) echo "Please type 1, 2 or 3." ;;
                    esac
                done
                ;;
            ERROR_CONNECT)
                err "Couldn't reach the Plex server at $plex_url"
                echo "  Possible causes: wrong IP/port, server not running, firewall."
                echo
                echo "What would you like to fix?"
                echo "  1) Re-enter the server address"
                echo "  2) Re-enter both the server address and the token"
                echo "  3) Quit installer"
                while true; do
                    ask "Choose [1/2/3]: " fix
                    case "$fix" in
                        1) plex_url=""; break ;;
                        2) plex_url=""; plex_token=""; break ;;
                        3) exit 1 ;;
                        *) echo "Please type 1, 2 or 3." ;;
                    esac
                done
                ;;
            ERROR_NO_MOVIES)
                err "Connected to Plex, but no movie libraries were found."
                echo "  Make sure you have at least one movie library set up in Plex."
                exit 1
                ;;
            *)
                err "Unexpected response from Plex: $first_line"
                exit 1
                ;;
        esac
    done

    # --- Folder mapping ---
    mapfile -t plex_folders <<< "$plex_folders_raw"
    folder_count=${#plex_folders[@]}

    # Ask once: same machine or different
    echo
    echo "Where is Plex running?"
    echo "  1) On this machine"
    echo "  2) On another machine"
    while true; do
        ask "Choose [1/2]: " same_machine
        if [ "$same_machine" = "1" ] || [ "$same_machine" = "2" ]; then
            break
        fi
        echo "Please type 1 or 2."
    done

    # Show what was found
    echo
    ok "Found $folder_count movie folder(s) in Plex:"
    for i in "${!plex_folders[@]}"; do
        echo -e "  $((i+1))) ${BLUE}${plex_folders[$i]}${NC}"
    done

    declare -a local_paths
    declare -a final_plex_paths

    if [ "$same_machine" = "1" ]; then
        # Auto-map: local path = plex path for everything
        echo
        info "Mapping all folders to their Plex paths..."
        for i in "${!plex_folders[@]}"; do
            p_path="${plex_folders[$i]}"
            final_plex_paths+=("$p_path")
            local_paths+=("$p_path")
            if [ ! -d "$p_path" ]; then
                warn "Plex path '$p_path' does not exist on this machine."
                warn "(this is unusual if Plex runs here -- check that the path is reachable)"
            fi
        done
        ok "All $folder_count folder(s) mapped"
    else
        # Ask per folder, with explicit Skip option
        echo
        echo "Plex runs on another machine, so each folder you use has to be mounted on"
        echo "this computer (for example a NAS share). Go through each folder and choose"
        echo "how to handle it."

        for i in "${!plex_folders[@]}"; do
            p_path="${plex_folders[$i]}"
            echo
            echo -e "${BOLD}Folder $((i+1)) of $folder_count:${NC} ${BLUE}$p_path${NC}"

            # One round = choose (use / skip), then ask for the path. Giving no
            # path, or declining an unreachable one, starts the round again, so
            # the user is never forced to type a path (and never loses the
            # whole installation by pressing Ctrl+C to get out).
            while true; do
                echo "  1) Use this folder"
                echo "  2) Skip"
                while true; do
                    ask "  Choose [1/2]: " action
                    if [ "$action" = "1" ] || [ "$action" = "2" ]; then
                        break
                    fi
                    echo "  Please type 1 or 2."
                done

                if [ "$action" = "2" ]; then
                    info "Folder $((i+1)) skipped"
                    continue 2
                fi

                no_path=0
                while true; do
                    ask "  Where is this folder mounted on this computer? Path (0 to go back): " l_path
                    l_path=$(trim "$l_path")
                    if [ -z "$l_path" ] || [ "$l_path" = "0" ]; then
                        no_path=1
                        break
                    fi
                    # Expand ~ to user home
                    l_path="${l_path/#\~/$HOME}"
                    # Absolute path check -- must start with /
                    if [[ "$l_path" != /* ]]; then
                        echo "  That doesn't look like a folder path. It should start with '/' or '~/'."
                        continue
                    fi
                    break
                done
                if [ "$no_path" = "1" ]; then
                    echo "  No path given. Choose 1 to enter a path, or 2 to skip this folder."
                    echo
                    continue
                fi

                [[ "$l_path" != */ ]] && l_path="${l_path}/"

                use_path=1
                if [ ! -d "$l_path" ]; then
                    warn "Path '$l_path' does not exist on this computer."
                    echo "  This is fine if the folder will be mounted later (e.g. NAS not connected yet)."
                    while true; do
                        ask "  Use it anyway? (y/n): " yn
                        case "$yn" in
                            y|Y|yes|YES) break ;;
                            n|N|no|NO) use_path=0; break ;;
                            *) echo "  Please type 'y' or 'n'." ;;
                        esac
                    done
                fi
                if [ "$use_path" = "0" ]; then
                    echo "  Not used. Choose 1 to enter another path, or 2 to skip this folder."
                    echo
                    continue
                fi
                break
            done

            final_plex_paths+=("$p_path")
            local_paths+=("$l_path")
            ok "Mapped: $p_path  ->  $l_path"
        done
    fi

    if [ "${#final_plex_paths[@]}" -eq 0 ]; then
        warn "No folders mapped yet."
        echo "  You can add them later from Settings → Folder mappings."
        echo
    fi

    cp config.yml.example "$CONF_FILE"
    sed -i "s|http://YOUR-PLEX-SERVER:32400|$plex_url|" "$CONF_FILE"

    python3 - "$CONF_FILE" "${final_plex_paths[@]}" "----" "${local_paths[@]}" <<'PYEOF'
import sys
from pathlib import Path

config = Path(sys.argv[1])
args = sys.argv[2:]
sep = args.index("----")
plex_paths = args[:sep]
local_paths = args[sep + 1:]
assert len(plex_paths) == len(local_paths)

text = config.read_text()
lines = text.split("\n")
new_lines: list[str] = []
skip_until_non_indent = False
for line in lines:
    if line.startswith("paths:"):
        if not plex_paths:
            # No folders configured -- write an explicit empty list.
            # A bare "paths:" with nothing under it parses as YAML null,
            # which fails validation (paths is a required list field).
            new_lines.append("paths: []")
            skip_until_non_indent = True
            continue
        new_lines.append(line)
        for p, l in zip(plex_paths, local_paths):
            new_lines.append(f"  - plex: {p}")
            new_lines.append(f"    local: {l}")
        skip_until_non_indent = True
        continue
    if skip_until_non_indent:
        if line.startswith("  - plex:") or line.startswith("    local:"):
            continue
        skip_until_non_indent = False
    new_lines.append(line)

config.write_text("\n".join(new_lines))
PYEOF

    (umask 077; echo "PLEX_TOKEN=$plex_token" > "$ENV_FILE")
    chmod 600 "$ENV_FILE"

    ok "Configuration saved to $CONF_DIR (${#final_plex_paths[@]} folder mapping(s))"
fi

# --- 4. Quick verification ---
# Every module of the app is loaded once, so a broken or incomplete install
# shows up here and not at the first menu choice. (Not the test suite: users
# get no test tools, see step 2.)
step "Verifying installation..."
verify_log=$(mktemp)
if uv run python -c "import importlib, pkgutil, auto_chapter as p
for m in pkgutil.walk_packages(p.__path__, 'auto_chapter.'): importlib.import_module(m.name)" \
    >"$verify_log" 2>&1; then
    ok "All checks passed"
    rm -f "$verify_log"
else
    # Python's own lines are needed to find the cause: never cut them short.
    err "The app could not be loaded. Last lines of the error:"
    tail -n 15 "$verify_log" | sed 's/^/  /'
    echo -e "  ${DIM}Full output: $verify_log${NC}"
    echo "Fix the cause shown above, then run this script again."
    exit 1
fi

# --- 5. Launcher + PATH installation ---
LAUNCHER="$PROJECT_DIR/auto-chapter"
cat > "$LAUNCHER" <<LAUNCHEOF
#!/usr/bin/env bash
cd "$PROJECT_DIR" && exec uv run auto-chapter "\$@"
LAUNCHEOF
chmod +x "$LAUNCHER"

# Install a symlink into ~/.local/bin -- the standard user-PATH location on Linux.
# This works across shells (bash, zsh, fish) and non-interactive contexts
# (scripts, cron), unlike a shell alias.
mkdir -p "$HOME/.local/bin"
BIN_LINK="$HOME/.local/bin/auto-chapter"
ln -sf "$LAUNCHER" "$BIN_LINK"

# Clean up legacy alias from earlier installs (they take precedence over PATH
# lookup in interactive shells, so leaving them behind would mask the symlink).
SHELL_RC=""
case "$SHELL" in
    */bash) SHELL_RC="$HOME/.bashrc" ;;
    */zsh)  SHELL_RC="$HOME/.zshrc"  ;;
    */fish) SHELL_RC="$HOME/.config/fish/config.fish" ;;
esac
if [ -n "$SHELL_RC" ] && [ -f "$SHELL_RC" ]; then
    if grep -q "^alias auto-chapter=" "$SHELL_RC" 2>/dev/null; then
        sed -i.bak '/^alias auto-chapter=/d' "$SHELL_RC"
        rm -f "$SHELL_RC.bak"
    fi
fi

# Is ~/.local/bin already on PATH? If not, we tell the user how to fix it.
NEEDS_PATH_HELP=false
case ":$PATH:" in
    *":$HOME/.local/bin:"*) : ;;
    *) NEEDS_PATH_HELP=true ;;
esac

step "Installation complete! (v$VERSION)"
echo

if [ "$NEEDS_PATH_HELP" = "true" ]; then
    warn "$HOME/.local/bin is not on your PATH yet."
    echo
    echo "Add this line to your shell config to fix that:"
    if [[ "$SHELL_RC" == *fish* ]]; then
        echo -e "  ${BOLD}set -Ux PATH \$HOME/.local/bin \$PATH${NC}"
    else
        echo -e "  ${BOLD}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
    fi
    echo -e "${DIM}(then open a new terminal, or reload your shell)${NC}"
    echo
    echo "Until then you can also run it with the full path:"
    echo -e "  ${DIM}$BIN_LINK${NC}"
else
    echo -e "To start auto-chapter, just type:  ${GREEN}${BOLD}auto-chapter${NC}"
    echo -e "${DIM}(works from any directory, any shell)${NC}"
fi
echo

# Detect if we were launched by a terminal in "hold" mode (e.g. Dolphin's
# "Run in Terminal" uses `konsole --hold -e ./install.sh`). In that case
# the terminal has no shell to return to, so exec'ing the launcher would
# leave a dead window after auto-chapter exits. Solution: run (not exec)
# the launcher, then drop the user into a real shell so `auto-chapter`
# works again in the same window.
in_holding_terminal() {
    local pid=$PPID
    for _ in 1 2 3; do
        [ -z "$pid" ] && return 1
        [ "$pid" = "0" ] && return 1
        local cmd
        cmd=$(ps -o command= -p "$pid" 2>/dev/null) || return 1
        if [[ "$cmd" == *"konsole"*"--hold"* ]]; then
            return 0
        fi
        pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    done
    return 1
}

drop_to_shell_with_hint() {
    echo
    echo -e "To run again: ${BOLD}auto-chapter${NC}"
    exec "${SHELL:-/bin/bash}" -l
}

while true; do
    # Not ask(): the installation is already complete here, so no input simply
    # means "don't start it" (same as 'n'), not "installation stopped".
    if ! read -e -rp "Start it now? (y/n): " start_now && [ -z "$start_now" ]; then
        echo
        start_now=n
    fi
    case "$start_now" in
        y|Y|yes|YES)
            echo
            # Once we hand control to auto-chapter, Ctrl+C should be handled
            # by Python only. Ignoring SIGINT here means:
            # (a) the installer's INT trap won't fire "Installation cancelled."
            #     after a normal quit (bash's deferred-trap behavior would
            #     otherwise trigger it if any SIGINT was received earlier);
            # (b) Ctrl+C during auto-chapter still reaches Python (its own
            #     handler catches it), while bash just ignores it -- so the
            #     installer process doesn't die and orphan the launcher.
            trap '' INT
            if in_holding_terminal; then
                "$LAUNCHER"
                drop_to_shell_with_hint
            else
                exec "$LAUNCHER"
            fi
            ;;
        n|N|no|NO)
            echo
            if in_holding_terminal; then
                echo -e "${GREEN}Installation complete.${NC}"
                drop_to_shell_with_hint
            else
                echo -e "${GREEN}Installation complete.${NC} Run ${BOLD}auto-chapter${NC} in a new terminal whenever you're ready."
                exit 0
            fi
            ;;
        *)
            echo -e "${YELLOW}Please type 'y' or 'n'.${NC}"
            ;;
    esac
done
