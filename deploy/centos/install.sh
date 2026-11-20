#!/usr/bin/env bash
# Install or update the app as systemd services on CentOS / RHEL / Rocky / Alma.
#
#   sudo deploy/centos/install.sh          # first install, and after every `git pull`
#
# Run it from a checkout at /opt/msc-intake/app. It is safe to run again: it never
# overwrites /etc/msc-intake/intake.env, and it restarts the services at the end.
set -euo pipefail

APP_USER=msc-intake
BASE=/opt/msc-intake
APP_DIR="$BASE/app"
VENV="$BASE/venv"
DATA_DIR=/var/lib/msc-intake
ENV_DIR=/etc/msc-intake
ENV_FILE="$ENV_DIR/intake.env"
UNIT_DIR=/etc/systemd/system
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '\n==> %s\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
if [[ "$(cd "$HERE/../.." && pwd)" != "$APP_DIR" ]]; then
    echo "Clone the repository to $APP_DIR and run the script from there." >&2
    exit 1
fi

say "System packages"
dnf install -y git file-libs tar gzip >/dev/null

say "Service user and folders"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$BASE" --shell /sbin/nologin "$APP_USER"
install -d -o "$APP_USER" -g "$APP_USER" -m 750 "$DATA_DIR" "$DATA_DIR/private_media" "$VENV" "$BASE/python"
install -d -o root -g "$APP_USER" -m 750 "$ENV_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
# git refuses to work in a repo owned by another user; allow this one for root.
git config --global --get-all safe.directory | grep -qx "$APP_DIR" \
    || git config --global --add safe.directory "$APP_DIR"

say "uv (Python package manager)"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
fi
UV="$(command -v uv)"

say "Environment file"
if [[ ! -f "$ENV_FILE" ]]; then
    secret="$(head -c 48 /dev/urandom | base64 | tr -d '/+=\n')"
    sed "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=$secret|" "$HERE/intake.env.example" > "$ENV_FILE"
    chown root:"$APP_USER" "$ENV_FILE"
    chmod 640 "$ENV_FILE"
    echo "Created $ENV_FILE. Edit hosts, DATABASE_URL and Ollama, then run this script again."
    exit 0
fi

# Runs a command as the service user, with the production environment loaded.
as_app() {
    runuser -u "$APP_USER" -- env -i HOME="$BASE" PATH="$VENV/bin:/usr/local/bin:/usr/bin:/bin" \
        UV_PROJECT_ENVIRONMENT="$VENV" UV_PYTHON_INSTALL_DIR="$BASE/python" \
        UV_CACHE_DIR="$BASE/.cache/uv" \
        bash -c "set -a; source '$ENV_FILE'; set +a; cd '$APP_DIR'; $*"
}

say "Python dependencies"
as_app "'$UV' sync --frozen --no-dev --python 3.12"

say "Checks, database migrations, static files"
as_app "python manage.py check --deploy --fail-level ERROR"
as_app "python manage.py migrate --noinput"
as_app "python manage.py collectstatic --noinput -v 0"

say "systemd services"
install -m 644 "$HERE/msc-intake.target" "$HERE/msc-intake-web.service" \
    "$HERE/msc-intake-worker.service" "$UNIT_DIR/"
systemctl daemon-reload
systemctl enable msc-intake.target msc-intake-web.service msc-intake-worker.service >/dev/null
systemctl restart msc-intake.target

say "Status"
sleep 3
systemctl --no-pager --lines=0 status msc-intake-web.service msc-intake-worker.service || true
echo
echo "Logs:    journalctl -u msc-intake-web -u msc-intake-worker -f"
echo "Ollama:  sudo -u $APP_USER bash -c 'set -a; source $ENV_FILE; cd $APP_DIR; $VENV/bin/python manage.py check_ollama'"
