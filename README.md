# Nanobot WhatsApp

Local WhatsApp operator workspace with:

- a backend on `3456`
- a frontend on `8080` in Docker
- project-local runtime data in this repo

## Stop The Current Docker Stack

If you want to stop the containers started from this repo, run this in the project root:

```bash
docker compose down
```

That is the exact command to stop the current Docker setup.

## Docker Setup

Docker is the recommended way to run this project.

### Prerequisites

- Docker Desktop or another Docker engine with Compose v2
- macOS / Linux: `python3` must be available
- Windows: `py -3` must be available
- Chrome/Chromium is required on the host for Docker WhatsApp history sync

### 1. Clone The Repo

```bash
git clone https://github.com/Wilsonnijc-bot/Claw-Insurance.git
cd Claw-Insurance
```

### 2. Create Config Files

```bash
cp config.example.json config.json
cp google.example.json google.json
cp supabase.example.json supabase.json
```

Then:

- edit `config.json` and add the real API key / model settings you need
- keep `google.json` only if you use Google STT
- keep `supabase.json` only if you use Supabase features
- if you use Google STT, put the real credential JSON inside `secrets/`

### 3. Build And Start

```bash
python3 -m nanobot docker-up
```

That command does the host checks, installs or reuses the host CDP helper, and then runs:

```bash
docker compose up -d --build
```

Platform commands:

- macOS: `./docker-up` or `python3 -m nanobot docker-up`
- Linux / Huawei Linux: `python3 -m nanobot docker-up`
- Windows / Huawei Windows: `py -3 -m nanobot docker-up`

### 4. Open The App

Open:

```text
http://localhost:8080
```

Then log in and complete WhatsApp login / QR steps if needed.

## Daily Docker Use

Use these commands from the project root.

```bash
python3 -m nanobot docker-up       # macOS/Linux build and start everything
py -3 -m nanobot docker-up         # Windows build and start everything
./docker-up                        # macOS compatibility wrapper
docker compose up -d               # start without rebuilding
docker compose ps                  # show running services
docker compose logs -f             # follow all logs
docker compose logs -f nanobot-gateway
docker compose restart nanobot-gateway
docker compose down                # stop everything
```

Useful notes:

- macOS: use `./docker-up` or `python3 -m nanobot docker-up`
- Linux / Huawei Linux: use `python3 -m nanobot docker-up`
- Windows / Huawei Windows: use `py -3 -m nanobot docker-up`
- `docker compose down` stops the current stack
- `docker compose down` does not delete your project files in this repo

## Services

- `nanobot-gateway`: backend API on `http://localhost:3456`
- `nanobot-frontend`: web UI on `http://localhost:8080`
- `nanobot-cli`: optional CLI profile

## Python Commands

If you do not want Docker, these are the main local Python commands:

```bash
./bootstrap
source .venv/bin/activate
python -m nanobot setup
python -m nanobot status
whatsapp-web-nanobot-ui
python -m nanobot stop-dev
```

Keep this mental model:

- Docker: recommended for normal use
- Python commands: fallback for local development or manual setup
