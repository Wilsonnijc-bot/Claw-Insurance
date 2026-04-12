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
- `python` / `python3` must be available because `python -m nanobot docker-up` runs the host CDP helper preflight
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
python -m nanobot docker-up
```

This command does the host checks and then runs:

```bash
docker compose up -d --build
```

On macOS and Linux, `./docker-up` remains as a compatibility wrapper around the same Python command.

### 4. Open The App

Open:

```text
http://localhost:8080
```

Then log in and complete WhatsApp login / QR steps if needed.

## Daily Docker Use

Use these commands from the project root.

```bash
python -m nanobot docker-up        # build and start everything
./docker-up                        # macOS/Linux compatibility wrapper
docker compose up -d               # start without rebuilding
docker compose ps                  # show running services
docker compose logs -f             # follow all logs
docker compose logs -f nanobot-gateway
docker compose restart nanobot-gateway
docker compose down                # stop everything
```

Useful notes:

- `python -m nanobot docker-up` is the official start command
- `./docker-up` remains available on macOS/Linux
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
