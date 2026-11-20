# Running on a CentOS server (systemd)

Two services, started together by one target:

| Unit | What it runs |
|---|---|
| `msc-intake-web.service` | gunicorn on `127.0.0.1:8000` (pages, API, static files via WhiteNoise) |
| `msc-intake-worker.service` | Celery worker (extraction through Ollama) |
| `msc-intake.target` | starts / stops / restarts both, and starts them at boot |

Files are in [`deploy/centos/`](../deploy/centos/). Tested layout: CentOS Stream 9 / RHEL 9 /
Rocky / Alma 9 (anything with `dnf` and systemd).

| Path | Contents |
|---|---|
| `/opt/msc-intake/app` | this repository (git checkout) |
| `/opt/msc-intake/venv` | Python 3.12 virtualenv (made by `uv`) |
| `/etc/msc-intake/intake.env` | settings and secrets (root:msc-intake, mode 640) |
| `/var/lib/msc-intake/private_media` | uploaded documents — **back up with the database** |

## 1. PostgreSQL, Redis, Ollama

Skip what already runs elsewhere; then point `intake.env` at it.

```bash
# PostgreSQL 16
sudo dnf module enable -y postgresql:16
sudo dnf install -y postgresql-server
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql
sudo -u postgres psql -c "CREATE USER intake WITH PASSWORD 'strong-password';"
sudo -u postgres psql -c "CREATE DATABASE intake OWNER intake;"
# Allow password logins from localhost: in /var/lib/pgsql/data/pg_hba.conf change
# "ident" to "scram-sha-256" on the 127.0.0.1 and ::1 lines, then:
sudo systemctl restart postgresql

# Redis (on newer releases the package is "valkey"; the service name changes with it)
sudo dnf install -y redis && sudo systemctl enable --now redis

# Ollama + the vision model
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5vl:7b
```

## 2. Install the app

```bash
sudo mkdir -p /opt/msc-intake
sudo git clone git@github.com:muhammadawais000/siresoft_mscaccountant_document_uploader.git /opt/msc-intake/app
cd /opt/msc-intake/app
sudo deploy/centos/install.sh        # 1st run: creates /etc/msc-intake/intake.env and stops
sudo vi /etc/msc-intake/intake.env   # hosts, DATABASE_URL password, OLLAMA_* model
sudo deploy/centos/install.sh        # 2nd run: deps, migrate, collectstatic, start services
```

Create the first login:

```bash
sudo -u msc-intake bash -c 'set -a; source /etc/msc-intake/intake.env; cd /opt/msc-intake/app; \
  /opt/msc-intake/venv/bin/python manage.py createsuperuser'
```

## 3. Nginx, firewall, SELinux

```bash
sudo dnf install -y nginx
sudo cp deploy/centos/nginx-msc-intake.conf /etc/nginx/conf.d/msc-intake.conf
sudo vi /etc/nginx/conf.d/msc-intake.conf          # set server_name
sudo setsebool -P httpd_can_network_connect 1       # SELinux: let nginx reach gunicorn
sudo systemctl enable --now nginx
sudo firewall-cmd --permanent --add-service=http --add-service=https && sudo firewall-cmd --reload
# HTTPS
sudo dnf install -y certbot python3-certbot-nginx && sudo certbot --nginx -d intake.example.com
```

**Testing over plain http first** (by IP, before HTTPS): set `DJANGO_SECURE_SSL_REDIRECT=false`,
`DJANGO_SECURE_COOKIES=false`, `DJANGO_SECURE_HSTS_SECONDS=0` and add the IP to
`DJANGO_ALLOWED_HOSTS` / `DJANGO_CSRF_TRUSTED_ORIGINS` (`http://<ip>`). Set them back once
the certificate is in place — otherwise the login cookie travels unencrypted.

## Everyday commands

```bash
sudo systemctl status  msc-intake-web msc-intake-worker
sudo systemctl restart msc-intake.target            # after editing intake.env
journalctl -u msc-intake-web -u msc-intake-worker -f   # live logs

# Update to the latest code
cd /opt/msc-intake/app && sudo -u msc-intake git pull && sudo deploy/centos/install.sh
```

The worker gets up to 5 minutes to finish a running extraction on stop; a task cut off
anyway is re-queued (`acks_late`). Both services restart automatically if they crash.
