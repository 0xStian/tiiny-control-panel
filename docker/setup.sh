#!/usr/bin/env bash
# The Tiiny Control Panel with Docker: sets it up, starts it, and shows where to open it. Run it again any time to see
# that again (it also starts the panel if it's stopped).
#
#   From GitHub:     curl -fsSL https://raw.githubusercontent.com/0xStian/tiiny-control-panel/main/docker/setup.sh | bash
#                    (with an option: ... | bash -s -- --update)
#   From the code:   ./docker/setup.sh          (builds the image from this copy of the code)
#
#   --update     the newest version: pull (or build) it, then restart
#   --pull       use the published image, even when run from a copy of the code
#   --dir DIR    where it lives (default: this folder if it has the panel's compose.yaml, else ./tiiny-panel)
#
# It works on any Linux with Docker (x86-64 or 64-bit ARM): your user, group and time zone are detected and written to
# .env next to compose.yaml, Docker is used through sudo only if your user can't use it, and the address it shows is
# the one the panel works out for itself.
set -euo pipefail

RAW="${TIINY_PANEL_RAW:-https://raw.githubusercontent.com/0xStian/tiiny-control-panel/main}"   # a fork can point elsewhere

say() { printf '%s\n' "$*"; }
step() { printf '\n%s\n' "$*"; }
fail() { printf '\n%s\n' "$*" >&2; exit 1; }
usage() {   # the comment at the top
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
  else
    say "Options: --update, --pull, --dir DIR (with curl: ... | bash -s -- --update). More: $RAW/docker/setup.sh"
  fi
}

# Run from a copy of the code (build from it), or piped from GitHub (use the published image)?
SRC=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [ -f "$here/../Dockerfile" ] && [ -f "$here/../tiiny_ui.py" ]; then
    SRC="$(cd "$here/.." && pwd)"
  fi
fi

MODE="" UPDATE=0 DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --update) UPDATE=1 ;;
    --pull) MODE=pull ;;
    --build) MODE=build ;;
    --dir) [ $# -ge 2 ] || fail "--dir needs a folder."; DIR="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown option: $1 (see --help)" ;;
  esac
  shift
done
[ -n "$MODE" ] || { [ -n "$SRC" ] && MODE=build || MODE=pull; }
[ "$MODE" = build ] && [ -z "$SRC" ] && fail "--build needs a copy of the code: run the setup.sh that's in it."

case "$(uname -s)" in
  Linux) ;;
  *) fail "This needs Linux with Docker. Docker Desktop on macOS and Windows can't use the host's network, which the
panel needs to find your Tiiny. Use a Linux machine, or run the panel without Docker (see the README)." ;;
esac

# -- Docker and Compose ---------------------------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || fail "Docker isn't installed. Install it (https://docs.docker.com/engine/install/), then run this again."
SUDO=""
if ! docker info >/dev/null 2>&1; then
  if [ "$(id -u)" != 0 ] && command -v sudo >/dev/null 2>&1; then
    say "Your user can't use Docker directly, so this uses sudo (it may ask for your password)."
    sudo docker info >/dev/null 2>&1 || fail "Docker isn't running. Start it (for example: sudo systemctl start docker), then run this again."
    SUDO="sudo"
  else
    fail "Docker isn't running, or this user can't use it. Start Docker, or add your user to the docker group, then run this again."
  fi
fi
if $SUDO docker compose version >/dev/null 2>&1; then
  COMPOSE="$SUDO docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="$SUDO docker-compose"
else
  fail "Docker Compose isn't installed. Install it (the docker-compose-plugin package on most systems), then run this again."
fi

# -- where it lives -------------------------------------------------------------------------------------------------
if [ -z "$DIR" ]; then
  if [ -f compose.yaml ] && grep -q "tiiny-control-panel" compose.yaml; then DIR="."; else DIR="./tiiny-panel"; fi
fi
mkdir -p "$DIR/tiiny-data"
cd "$DIR"
HERE="$(pwd)"
HERE_P="$(pwd -P)"

download() {   # url file
  if command -v curl >/dev/null 2>&1; then curl -fsSL "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then wget -qO "$2" "$1"
  else fail "Neither curl nor wget is installed, so compose.yaml can't be downloaded."; fi
}
if [ ! -f compose.yaml ]; then
  if [ -n "$SRC" ]; then cp "$SRC/compose.yaml" compose.yaml; else download "$RAW/compose.yaml" compose.yaml; fi
fi
# the image and the container's name, as compose.yaml has them (a version can be pinned there, like ...:1.4)
yaml_value() { awk -v k="$1:" '$1 == k { v = $2; gsub(/[^A-Za-z0-9._:\/@-]/, "", v); print v; exit }' compose.yaml; }
IMAGE="$(yaml_value image)"; IMAGE="${IMAGE:-ghcr.io/0xstian/tiiny-control-panel:latest}"
NAME="$(yaml_value container_name)"; NAME="${NAME:-tiiny-panel}"

# -- your user, group and time zone, in .env (values already there are kept) -----------------------------------------
if [ "$(id -u)" = 0 ]; then
  if [ -n "${SUDO_UID:-}" ]; then
    uid="$SUDO_UID" gid="${SUDO_GID:-$SUDO_UID}"
  else   # root without sudo (some NAS shells): the data folder's owner, else 1000
    owner="$(stat -c '%u:%g' tiiny-data 2>/dev/null || echo 0:0)"
    if [ "$owner" != "0:0" ]; then uid="${owner%%:*}" gid="${owner##*:}"; else uid=1000 gid=1000; fi
  fi
else
  uid="$(id -u)" gid="$(id -g)"
fi
tz=""
command -v timedatectl >/dev/null 2>&1 && tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
[ -z "$tz" ] && [ -f /etc/timezone ] && tz="$(head -n 1 /etc/timezone 2>/dev/null || true)"
[ -z "$tz" ] && [ -L /etc/localtime ] && tz="$(readlink /etc/localtime | sed -n 's#.*/zoneinfo/##p')"
tz="${tz:-Etc/UTC}"
touch .env
if [ -s .env ] && [ -n "$(tail -c 1 .env)" ]; then printf '\n' >> .env; fi   # its last line had no line break
keep() { grep -q "^$1=" .env || printf '%s=%s\n' "$1" "$2" >> .env; }
keep PUID "$uid"
keep PGID "$gid"
keep TZ "$tz"
if [ "$(id -u)" = 0 ] && [ -n "${SUDO_UID:-}" ]; then   # run with sudo: the files stay yours
  chown "$uid:$gid" . compose.yaml .env tiiny-data 2>/dev/null || true
fi
port="$(awk -F= '{ k = $1; gsub(/[[:space:]]/, "", k) } k == "TIINY_UI_PORT" { v = $2 } END { gsub(/[^0-9]/, "", v); print v }' .env)"
port="${port:-8765}"

# -- another panel container somewhere else? --------------------------------------------------------------------
# (Compose's label says where it was started; its /data folder says it too, where a Compose leaves the label out)
if $SUDO docker container inspect "$NAME" >/dev/null 2>&1; then
  where="$($SUDO docker container inspect -f '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$NAME" 2>/dev/null || true)"
  data="$($SUDO docker container inspect -f '{{ range .Mounts }}{{ if eq .Destination "/data" }}{{ .Source }}{{ end }}{{ end }}' "$NAME" 2>/dev/null || true)"
  [ "$where" = "<no value>" ] && where=""
  case "$where|$data" in
    "$HERE|"* | "$HERE_P|"* | *"|$HERE/tiiny-data" | *"|$HERE_P/tiiny-data") ;;   # it's this folder's
    *)
      [ -z "$where" ] && case "$data" in */tiiny-data) where="${data%/tiiny-data}" ;; esac
      [ -n "$where" ] && fail "The panel already runs from $where. Run this in that folder (cd \"$where\"), or stop it
there first (${SUDO:+sudo }docker compose down), then run this again."
      fail "A container named $NAME already exists, made without this folder's compose.yaml. Remove it first
(${SUDO:+sudo }docker rm -f $NAME), then run this again." ;;
  esac
fi

# -- the image ------------------------------------------------------------------------------------------------------
if [ "$MODE" = build ]; then
  step "Building the panel's image from $SRC (a minute or two the first time)…"
  $SUDO docker build -q -t "$IMAGE" "$SRC" >/dev/null || fail "The image didn't build (see the messages above)."
elif [ "$UPDATE" = 1 ] || ! $SUDO docker image inspect "$IMAGE" >/dev/null 2>&1; then
  step "Downloading the panel's image…"
  $COMPOSE pull --quiet || fail "Couldn't download the image (see the messages above)."
fi

# -- start it, and wait until it answers ----------------------------------------------------------------------------
step "Starting it…"
$COMPOSE up -d >/dev/null 2>&1 || { $COMPOSE up -d || true; fail "Docker couldn't start it (see the messages above)."; }

explain() {   # why it isn't running, from its last messages
  local logs
  logs="$($SUDO docker logs --tail 60 "$NAME" 2>&1 || true)"
  printf "\nThe panel didn't start. Its last messages:\n"
  printf '%s\n' "$logs" | tail -n 12 | sed 's/^/  | /'
  case "$logs" in
    *"Couldn't listen on"*)
      say "Port $port is taken by another program (maybe the panel started without Docker). Stop that one, or use"
      say "another port: add TIINY_UI_PORT=8775 to $HERE/.env and run this again." ;;
    *"ermission denied"*)
      say "The data folder isn't writable for user $uid. Check $HERE/tiiny-data, or set PUID and PGID in .env to its owner." ;;
  esac
  exit 1
}
restarts0="$($SUDO docker container inspect -f '{{.RestartCount}}' "$NAME" 2>/dev/null || echo 0)"
if ! $SUDO docker exec "$NAME" tiiny-panel health >/dev/null 2>&1; then
  if [ -e tiiny-data/.local/tiiny/bin/tiiny ]; then
    printf 'Waiting for it to answer'
  else
    printf 'Waiting for it to answer. The first start installs Tiiny'"'"'s own tool, which takes about a minute'
  fi
  deadline=$((SECONDS + 300))
  until $SUDO docker exec "$NAME" tiiny-panel health >/dev/null 2>&1; do
    state="$($SUDO docker container inspect -f '{{.State.Status}} {{.RestartCount}}' "$NAME" 2>/dev/null || echo "missing 0")"
    if [ "${state%% *}" != running ] || [ "${state##* }" != "$restarts0" ]; then printf '\n'; explain; fi
    if [ "$SECONDS" -ge "$deadline" ]; then printf '\n'; say "It didn't answer within 5 minutes."; explain; fi
    printf '.'
    sleep 3
  done
  printf '\n'
fi

# -- where to go ----------------------------------------------------------------------------------------------------
printf '\n'
$SUDO docker exec "$NAME" tiiny-panel info || true
fw=""
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet firewalld 2>/dev/null; then
  fw="sudo firewall-cmd --add-port=$port/tcp --permanent && sudo firewall-cmd --reload"
elif grep -qs '^ENABLED=yes' /etc/ufw/ufw.conf; then   # (Ubuntu's ufw service runs even with the firewall off)
  fw="sudo ufw allow $port/tcp"
fi
if [ -n "$fw" ]; then
  printf '\nIf your phone or laptop can'"'"'t connect, let them through the firewall:\n  %s\n' "$fw"
fi
if [ -n "$SRC" ]; then
  again="$SRC/docker/setup.sh"
  newest="update the code in $SRC, then run the same command"
else
  again="curl -fsSL $RAW/docker/setup.sh | bash"
  newest="$again -s -- --update"
fi
printf '\nIt lives in %s. Run these there:\n' "$HERE"
say "  See this again:  $again"
say "  Newest version:  $newest"
