# BRAIN Backend

Your own API/backend — Krea-style creative endpoints, running on your machine.
Zero dependencies: pure Python stdlib. No `pip install` anything.

## Run it

```sh
cd ~/workspace/brain-backend
python3 server.py
# live on http://localhost:8765
```

Open **http://localhost:8765** in a browser — the BRAIN Studio web app is
served right from the backend. Four tabs: Speak, Image, Video, Chat.
No separate frontend to deploy: wherever the backend goes, the app goes.

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"ok": true, ...}` |
| GET | `/v1/voices` | — | available TTS voices |
| POST | `/v1/speak` | `{"text": "...", "voice": "smooth", "speed": 100}` | MP3 audio |
| POST | `/v1/images/generate` | `{"prompt": "...", "size": "1024x1024"}` | PNG image |
| POST | `/v1/videos/generate` | `{"prompt": "..."}` | 501 until configured |
| POST | `/v1/chat` | `{"messages": [{"role": "user", "content": "..."}]}` | `{"reply": "..."}` |

Try it:

```sh
curl localhost:8765/health

curl -X POST localhost:8765/v1/speak \
  -H 'Content-Type: application/json' \
  -d '{"text": "Wassup twinn, the backend is live."}' \
  --output test.mp3 && ffplay test.mp3
```

## What works right now

- **`/v1/speak`** — fully working. Uses the local TTS engine, Smooth voice by default.
- **`/v1/voices`**, **`/health`** — working.

## What needs an API key (bring your own)

- **`/v1/images/generate`** — needs `OPENAI_API_KEY`. Returns HTTP 501 with setup
  instructions until the key is set.
- **`/v1/chat`** — needs `OPENAI_API_KEY` (or any OpenAI-style endpoint via
  `OPENAI_BASE_URL`).
- **`/v1/videos/generate`** — scaffolded. Needs `REPLICATE_API_TOKEN` plus
  picking a video model — the poll loop is marked in `providers.py`.

Copy `.env.example` to `.env`, fill in keys, and export them before starting:

```sh
set -a; source .env; set +a
python3 server.py
```

## Put it on the internet (so your phone can reach it)

Right now it only listens on your own machine (`127.0.0.1`). To reach it from
your phone or share it:

1. Easiest: deploy to a free host — Render, Railway, or Fly.io all take a
   Python server with a `requirements.txt` (empty here) and a start command
   of `python3 server.py`.
2. Quick test: tools like `ngrok` or Cloudflare Tunnel give your localhost a
   public URL in one command: `ngrok http 8765`.

## Add your own endpoints

1. Subclass `BaseProvider` in `providers.py`, implement `run(payload)`.
2. Register it in `PROVIDERS`.
3. Add the route in `server.py` (`do_POST`).

That's the whole architecture. Experiment away.
