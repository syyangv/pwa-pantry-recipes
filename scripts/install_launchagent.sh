#!/usr/bin/env bash
# install_launchagent.sh — prepare the launchd deploy path for pwa-pantry-recipes.
#
# *** DEPLOYMENT IS NOT YET AUTHORIZED. ***
# AGENTS.md § *Deploy* records it as such and docs/runbook/deployment.md is the
# plan. This script therefore defaults to the ONLY stage that is safe without
# authorization: it renders the template, substitutes the machine-specific
# values, and lints the result. It touches no directory outside the repo, starts
# no process, and exposes nothing.
#
# Stages, and what each one needs:
#
#   (default)  render + plutil -lint    no authorization, no side effects
#   --apply    APP_DATA_DIR 0700, the log directory, and the plist copied into
#              ~/Library/LaunchAgents. Still starts NOTHING: the agent is copied
#              but not bootstrapped, so `launchctl list` does not change.
#   --bootstrap `bootout` then `bootstrap`, so the job actually runs. THIS is
#              the step that starts a service which writes cooking logs into the
#              real vault, and it is the reason the other two stages exist.
#
#   --teardown bootout + remove the installed plist. The rollback.
#
# Every stage is idempotent. --bootstrap is refused outright when the converge
# gate is not going to be able to run afterwards (no venv python), and it is
# refused outright without the Tailscale Serve ingress already answering,
# because PUBLIC_ORIGIN below names that origin and a backend whose public
# origin does not resolve answers every mutation 403 origin_not_allowed.
#
# Usage:
#   ./scripts/install_launchagent.sh                          # render + lint
#   ./scripts/install_launchagent.sh --apply
#   ./scripts/install_launchagent.sh --apply --bootstrap
#   ./scripts/install_launchagent.sh --teardown
#
# Configuration, by environment variable (no .env parser in this app — see the
# plist header). Every one has a placeholder default; `--require-real-values`
# refuses to install while any is still a placeholder, so a half-configured
# agent cannot be bootstrapped by accident.
#
#   VENV_DIR                default <repo>/.venv  (the plist builds <dir>/bin/python)
#   REPO_ROOT               default <repo>
#   USER_HOME               default $HOME
#   APP_DATA_DIR            default $HOME/.local/share/pwa-pantry-recipes
#   OBSIDIAN_VAULT_PATH     default /Users/syang/obsidian/syang
#   PANTRY_ITEMS_DB         default $HOME/projects/wholefoods-to-pantry/assets/pantry_items.db
#   PANTRY_NOTE_RELATIVE    default Logistics/库存/Pantry.md
#   RECIPES_ROOT            default Hobbies/做饭/Recipes
#   DAILY_NOTES_ROOT        default 日记
#   DAILY_NOTES_YEAR_POLICY default <empty>
#   PUBLIC_ORIGIN           default https://home-macbook-air.tailcd6e49.ts.net:8452
#   TAILSCALE_OWNER_LOGIN   default __PLACEHOLDER__
#   DEV_IDENTITY            default __PLACEHOLDER__
#   OBSIDIAN_READ_ONLY      default true
#   APP_PORT                default 8007
#   SERVE_PORT              default 8452
#   LABEL                   default com.syang.pwa-pantry-recipes
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# USER_HOME first: everything below that names a path under $HOME is derived
# from it, and `set -u` refuses to expand a variable that has not been assigned
# yet. Getting this order wrong is a confusing "unbound variable" on line 60.
USER_HOME="${USER_HOME:-$HOME}"
REPO_ROOT="${REPO_ROOT:-$repo_root}"

TEMPLATE="$repo_root/scripts/pwa-pantry-recipes.example.plist"
LABEL="${LABEL:-com.syang.pwa-pantry-recipes}"
INSTALLED="$USER_HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$USER_HOME/Library/Logs/pwa-pantry-recipes"
VENV_DIR="${VENV_DIR:-$repo_root/.venv}"
VENV_PYTHON="$VENV_DIR/bin/python"
APP_PORT="${APP_PORT:-8007}"
SERVE_PORT="${SERVE_PORT:-8452}"

APP_DATA_DIR="${APP_DATA_DIR:-$HOME/.local/share/pwa-pantry-recipes}"
OBSIDIAN_VAULT_PATH="${OBSIDIAN_VAULT_PATH:-$HOME/obsidian/syang}"
PANTRY_ITEMS_DB="${PANTRY_ITEMS_DB:-$HOME/projects/wholefoods-to-pantry/assets/pantry_items.db}"
PANTRY_NOTE_RELATIVE="${PANTRY_NOTE_RELATIVE:-Logistics/库存/Pantry.md}"
RECIPES_ROOT="${RECIPES_ROOT:-Hobbies/做饭/Recipes}"
DAILY_NOTES_ROOT="${DAILY_NOTES_ROOT:-日记}"
DAILY_NOTES_YEAR_POLICY="${DAILY_NOTES_YEAR_POLICY:-}"
PUBLIC_ORIGIN="${PUBLIC_ORIGIN:-https://home-macbook-air.tailcd6e49.ts.net:$SERVE_PORT}"
TAILSCALE_OWNER_LOGIN="${TAILSCALE_OWNER_LOGIN:-__PLACEHOLDER__}"
DEV_IDENTITY="${DEV_IDENTITY:-__PLACEHOLDER__}"
OBSIDIAN_READ_ONLY="${OBSIDIAN_READ_ONLY:-true}"

mode="render"
rendered=""
fail=0

usage() { sed -n '2,52p' "$0" | sed 's/^# \{0,1\}//'; }

for arg in "$@"; do
    case "$arg" in
        --render-only) mode="render" ;;
        --apply) mode="apply" ;;
        --bootstrap) mode="bootstrap" ;;
        --teardown) mode="teardown" ;;
        -h | --help) usage; exit 0 ;;
        *) echo "unknown argument: $arg (try --help)" >&2; exit 2 ;;
    esac
done

# --- 1. preflight, read-only, always ---------------------------------------

echo "==> 1/4 preflight"

if [ ! -f "$TEMPLATE" ]; then
    echo "    template missing: $TEMPLATE" >&2
    exit 1
fi
if [ ! -x "$VENV_PYTHON" ]; then
    echo "    venv python missing: $VENV_PYTHON" >&2
    echo "    create it first:  python3 -m venv .venv && .venv/bin/python -m pip install -e '.[test,dev]'" >&2
    exit 1
fi
echo "    venv python   $VENV_PYTHON"

# The listener check exists in the gate for a running service; here it is the
# pre-flight question "is something already on 8007?", which is a different
# answer and is worth asking before anything is installed.
PORT_MANAGER="$HOME/.agent/skills/port-manager/scripts/port_manager.py"
if [ -f "$PORT_MANAGER" ]; then
    echo "    port $APP_PORT  $(python3 "$PORT_MANAGER" inspect "$APP_PORT" 2>&1 | head -n 1)"
else
    echo "    port $APP_PORT  (port_manager.py not found; cannot audit — the gate needs it too)"
fi

if [ "$TAILSCALE_OWNER_LOGIN" = "__PLACEHOLDER__" ] || [ "$DEV_IDENTITY" = "__PLACEHOLDER__" ]; then
    echo "    NOTE: TAILSCALE_OWNER_LOGIN / DEV_IDENTITY are still __PLACEHOLDER__." >&2
    echo "          Every /api/* request will be answered 401 until they are set." >&2
    if [ "$mode" != "render" ]; then
        echo "    refusing to $mode with a placeholder identity." >&2
        exit 1
    fi
fi
echo "    public origin $PUBLIC_ORIGIN   (app binds loopback:$APP_PORT)"
echo "    read-only     $OBSIDIAN_READ_ONLY"

# --- 2. render --------------------------------------------------------------

echo "==> 2/4 render $LABEL.plist"
# mktemp on macOS requires the XXXXXX template to END the name, so a `.plist`
# suffix after it fails with "File exists". A directory plus a fixed name inside
# it is the portable form, and it keeps the extension `plutil` and `install`
# want.
render_dir="$(mktemp -d "${TMPDIR:-/tmp}/pwa-pantry-recipes-render.XXXXXX")"
rendered="$render_dir/$LABEL.plist"
# The shell is the mechanism here (this is not a plist read), and `|` is the
# delimiter because every value above is a path.
sed \
    -e "s|__VENV_DIR__|$VENV_DIR|g" \
    -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
    -e "s|__USER_HOME__|$USER_HOME|g" \
    -e "s|__OBSIDIAN_VAULT_PATH__|$OBSIDIAN_VAULT_PATH|g" \
    -e "s|__APP_DATA_DIR__|$APP_DATA_DIR|g" \
    -e "s|__PUBLIC_ORIGIN__|$PUBLIC_ORIGIN|g" \
    -e "s|__TAILSCALE_OWNER_LOGIN__|$TAILSCALE_OWNER_LOGIN|g" \
    -e "s|__DEV_IDENTITY__|$DEV_IDENTITY|g" \
    -e "s|__PANTRY_ITEMS_DB__|$PANTRY_ITEMS_DB|g" \
    -e "s|__PANTRY_NOTE_RELATIVE__|$PANTRY_NOTE_RELATIVE|g" \
    -e "s|__RECIPES_ROOT__|$RECIPES_ROOT|g" \
    -e "s|__DAILY_NOTES_ROOT__|$DAILY_NOTES_ROOT|g" \
    -e "s|__DAILY_NOTES_YEAR_POLICY__|$DAILY_NOTES_YEAR_POLICY|g" \
    -e "s|__OBSIDIAN_READ_ONLY__|$OBSIDIAN_READ_ONLY|g" \
    "$TEMPLATE" > "$rendered"

# __UPPER_CASE__ is the template's own documentation token in the header comment
# and __PLACEHOLDER__ is the value this script substitutes for an identity that
# has not been chosen yet (preflight 1 refuses to install with those). Neither is
# an unsubstituted placeholder, so neither is matched.
remaining="$(grep -o '__[A-Z0-9_]*__' "$rendered" | sort -u | grep -vE '^__(UPPER_CASE|PLACEHOLDER)__$' || true)"
if [ -n "$remaining" ]; then
    echo "    UNSUBSTITUTED placeholders remain:" >&2
    echo "$remaining" | sed 's/^/      /' >&2
    echo "    a plist with a literal __FOO__ in a value boots unconfigured and fails closed." >&2
    exit 1
fi

# --- 3. lint, and prove the settings that silently matter -------------------

echo "==> 3/4 lint"
plutil -lint "$rendered"

# A plist that parses is not a plist that boots. These four are the settings
# whose absence is invisible in a diff and fatal (or silently degraded) at
# runtime, so they are read back out of the RENDERED file rather than trusted.
check_plist() {
    local key="$1" want="$2" got
    got="$(/usr/libexec/PlistBuddy -c "Print $key" "$rendered" 2>/dev/null || true)"
    if [ "$got" != "$want" ]; then
        echo "    $key is '$got', expected '$want'" >&2
        fail=1
    fi
}
check_plist ":Label" "$LABEL"
check_plist ":SoftResourceLimits:NumberOfFiles" "8192"
check_plist ":ThrottleInterval" "2"
check_plist ":EnvironmentVariables:BIND_HOST" "127.0.0.1"
# The bind address appears twice and the second one is the one uvicorn uses.
# PlistBuddy indents array elements by four spaces, so the match is a
# whitespace-tolerant whole-line one rather than `grep -qx`.
args_dump="$(/usr/libexec/PlistBuddy -c "Print :ProgramArguments" "$rendered" 2>/dev/null || true)"
if ! printf '%s\n' "$args_dump" | grep -qE '^[[:space:]]*127\.0\.0\.1[[:space:]]*$'; then
    echo "    :ProgramArguments does not pass --host 127.0.0.1" >&2
    fail=1
fi
if printf '%s\n' "$args_dump" | grep -qE '^[[:space:]]*(0\.0\.0\.0|\*)[[:space:]]*$'; then
    echo "    :ProgramArguments binds 0.0.0.0. Never. The Tailscale proxy is the only" >&2
    echo "    network-facing surface; a 0.0.0.0 bind puts the app on every interface." >&2
    fail=1
fi
if [ "$fail" -ne 0 ]; then
    echo "    refusing to go further." >&2
    exit 1
fi
echo "    Label / NumberOfFiles 8192 / ThrottleInterval 2 / loopback bind all present"

# --- 4. act -----------------------------------------------------------------

case "$mode" in
    render)
        echo "==> render only. Nothing was installed, started, or exposed."
        echo "    rendered plist kept at: $rendered"
        # The delimiter is QUOTED, so the block below is literal text: a backtick,
        # a `$(...)` or a `\` in operator-facing prose is printed, never executed.
        # An unquoted `<<NEXT` made this block run `port-manager audit --json` as
        # a command, so every render printed "port-manager: command not found" to
        # stderr and silently swallowed the sentence naming the audit — the
        # rendered line read "Always with  BEFORE and AFTER", with the audit
        # deleted from the instructions it was part of.
        #
        # The one value that must expand is passed to printf as an ARGUMENT.
        # A variable in an argument position is not re-scanned for backticks, so
        # this keeps the path (the operator needs the file this run produced)
        # without reopening the hole the quoted delimiter just closed.
        printf '\n  Next steps, each requiring its own decision:\n'
        printf '    1. Review the rendered plist:  less %s\n' "$rendered"
        cat <<'NEXT'
    2. Configure Tailscale Serve ingress (docs/runbook/deployment.md § 2):
         tailscale serve --bg --https=8452 http://127.0.0.1:8007
       Always with `port-manager audit --json` BEFORE and AFTER, and never as a
       bare `tailscale serve <target>`.
    3. Validate from a participant identity (a phone off this host's network)
       BEFORE the LaunchAgent exists — a foreground dev server is enough.
    4. ./scripts/install_launchagent.sh --apply
    5. ./scripts/install_launchagent.sh --apply --bootstrap
    6. .venv/bin/python scripts/converge_gate.py --local-origin ... --deployed-origin ...
NEXT
        ;;
    apply)
        echo "==> applying"
        # install -m 700, not mkdir + chmod: there is a window between the two
        # in which the directory is world-readable, and APP_DATA_DIR holds the
        # idempotency ledgers and the vault-recovery snapshots.
        if [ ! -d "$APP_DATA_DIR" ]; then
            install -d -m 700 "$APP_DATA_DIR"
            echo "    created $APP_DATA_DIR (0700)"
        else
            chmod 700 "$APP_DATA_DIR"
            echo "    $APP_DATA_DIR exists, mode $(stat -f '%Lp' "$APP_DATA_DIR")"
        fi
        install -d -m 755 "$LOG_DIR"
        echo "    log directory $LOG_DIR"
        # launchd's agents directory is a fixed path, not something the user
        # configures, and it does not exist on a machine that has never run one.
        # install -m 644 below then fails with a bare "No such file or
        # directory" that names neither this line nor the directory.
        install -d -m 755 "$USER_HOME/Library/LaunchAgents"
        install -m 644 "$rendered" "$INSTALLED"
        plutil -lint "$INSTALLED" >/dev/null
        echo "    installed $INSTALLED"
        echo
        echo "    NOT bootstrapped. 'launchctl list' is unchanged and nothing is running."
        echo "    To start it:  ./scripts/install_launchagent.sh --apply --bootstrap"
        ;;
    bootstrap)
        echo "==> bootstrapping $LABEL"
        # A plist edit is only picked up by bootout+bootstrap. `kickstart` re-reads
        # the binary and ignores the plist, which is how a NumberOfFiles fix
        # silently stays uninstalled.
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        # bootout returns before launchd has finished unloading the job, and a
        # bootstrap fired into that window fails with "Input/output error" and
        # leaves the service DOWN (seen 2026-09-29). Wait for the label to go,
        # then retry a bounded number of times rather than trusting the wait.
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break
            sleep 1
        done
        attempt=1
        until launchctl bootstrap "gui/$(id -u)" "$INSTALLED"; do
            if [ "$attempt" -ge 5 ]; then
                echo "    bootstrap failed $attempt times; the service is DOWN." >&2
                exit 1
            fi
            echo "    bootstrap attempt $attempt failed (launchd unload race); retrying" >&2
            attempt=$((attempt + 1))
            sleep 2
        done
        sleep 2
        # `state = running` proves a process is alive. The socket is the claim.
        if [ -f "$PORT_MANAGER" ]; then
            python3 "$PORT_MANAGER" inspect "$APP_PORT" | head -n 3
        fi
        launchctl print "gui/$(id -u)/$LABEL" | grep -A2 "resource limits" || true
        # Quoted delimiter for the same reason as the render stage above, and it
        # was the second instance of the same latent bug: nothing in an
        # operator-facing block is allowed to be able to run a command. The five
        # values that must expand go through printf as arguments, and the two
        # `\\` line-continuations are printf escapes, so they render as the
        # single backslashes a copy-pasteable shell continuation needs.
        printf '\n  The service is up on loopback %s. It is NOT reachable from off this\n' \
            "$APP_PORT"
        printf '  machine unless a Tailscale Serve route already proxies :%s to it.\n' \
            "$SERVE_PORT"
        cat <<'NEXT'

  Now run the gate. It will not exit 0 until every condition is proven:
NEXT
        printf '    .venv/bin/python scripts/converge_gate.py \\\n'
        printf '      --local-origin http://127.0.0.1:%s \\\n' "$APP_PORT"
        printf '      --deployed-origin %s \\\n' "$PUBLIC_ORIGIN"
        printf '      --vault %s\n' "$OBSIDIAN_VAULT_PATH"
        cat <<'NEXT'

  Only after it exits 0 may the Home Screen PWA be reinstalled. iOS caches
  manifest metadata longer than page content, so an icon change may need
  remove-and-re-add.
NEXT
        ;;
    teardown)
        echo "==> tearing down $LABEL"
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || echo "    (was not loaded)"
        rm -f "$INSTALLED"
        echo "    removed $INSTALLED. APP_DATA_DIR and $LOG_DIR were left alone;"
        echo "    $APP_DATA_DIR holds idempotency ledgers and vault-recovery"
        echo "    snapshots, so removing it is a separate, deliberate act."
        ;;
esac
