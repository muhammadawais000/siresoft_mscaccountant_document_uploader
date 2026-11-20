# Running on the server with Docker (GPU Ollama on the host)

For a server that already has Docker and Ollama (with the NVIDIA driver) installed.
PostgreSQL, Redis, the web app (gunicorn) and the worker (Celery) run as containers that
restart on their own after a crash or a reboot. Ollama stays on the host so it uses the GPU.

```
Browser ──> :8003 web (gunicorn) ──┐
                                  ├─> db (postgres), redis      [containers]
            worker (celery) ──────┘
                 └──> host.docker.internal:11434  Ollama on the host (GPU)
```

The stack is its own compose project (`msc-intake-server`): its own network and volumes,
no published database ports. Other projects on the server are not touched.

## 1. One-time: let the containers reach Ollama

Ollama must listen on all interfaces, not only `127.0.0.1`:

```bash
sudo systemctl edit ollama
# add, between the comment lines:
#   [Service]
#   Environment="OLLAMA_HOST=0.0.0.0:11434"
sudo systemctl restart ollama
ss -ltnp | grep 11434          # should show 0.0.0.0:11434 or *:11434
```

Port 11434 must stay closed in the firewall (it is not in the open ports list), so only
this server and its containers can use Ollama.

## 2. Settings

```bash
cd ~/siresoft_mscaccountant_document_uploader
cp deploy/server.env.example .env.server
openssl rand -base64 48        # use as DJANGO_SECRET_KEY
openssl rand -hex 16           # use as POSTGRES_PASSWORD
vi .env.server                 # SERVER-IP -> the server's IP (hostname -I), keys above
```

## 3. Start

```bash
docker compose -f docker-compose.server.yml --env-file .env.server up -d --build
docker compose -f docker-compose.server.yml --env-file .env.server ps
docker compose -f docker-compose.server.yml --env-file .env.server exec web python manage.py createsuperuser
docker compose -f docker-compose.server.yml --env-file .env.server exec worker python manage.py check_ollama --probe
```

Open `http://SERVER-IP:8003`. (With `make` installed: `make server-up`, `make server-ps`,
`make server-logs`, `make server-manage cmd=createsuperuser`.)

Do not also start the local stack (`docker compose up` without `-f`): it would try to
use port 8003 too.

## 4. Check that extraction runs on the GPU

Upload a document, and while it is processing:

```bash
ollama ps                       # PROCESSOR column: "100% GPU"
watch -n1 nvidia-smi            # GPU-Util goes up, memory used by ollama
```

`CPU/GPU` split (e.g. `30%/70%`) means the model plus its context did not fit in VRAM:
lower `OLLAMA_NUM_CTX` (16384 → 8192) in `.env.server` and run `up -d` again.
Other projects using the same Ollama (bigger models) can push this model out of VRAM;
`ollama ps` shows what is loaded.

## Everyday

```bash
C="docker compose -f docker-compose.server.yml --env-file .env.server"
$C logs -f --tail=100 web worker      # logs
$C restart web worker                 # after editing .env.server: use "up -d" instead
git pull && $C up -d --build          # update to the latest code
$C down                               # stop (data volumes stay)
```

Backups: the `msc-intake-server_pgdata` and `msc-intake-server_private_media` volumes
belong together; back them up at the same time.

Docker itself must start at boot: `systemctl is-enabled docker` → `enabled`.
