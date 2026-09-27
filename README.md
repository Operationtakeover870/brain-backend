# BRAIN Backend

An independent creative API and web studio served from one Python process. Deploy it to Render and open the service URL: the BRAIN Studio interface is served from `/` alongside the API.

## What it does

| Tool | Route | Status |
|---|---|---|
| Health | `GET /health` | Ready |
| Voices | `GET /v1/voices` | Ready |
| Speech | `POST /v1/speak` | Requires the `tts` CLI on the host |
| Images | `POST /v1/images/generate` | Uses OpenAI `gpt-image-1` |
| Video | `POST /v1/videos/generate` | Uses a configured Replicate video model |
| Chat | `POST /v1/chat` | Uses OpenAI or an OpenAI-compatible endpoint |

## Run locally

```sh
python3 server.py
# http://localhost:8765
```

For Render, use this start command:

```sh
python3 server.py
```

The server reads Render's `PORT` environment variable automatically.

## Environment variables

Add these in **Render → Service → Environment**. Never put provider keys in `index.html` or commit them to GitHub.

| Variable | Used by | Required |
|---|---|---|
| `OPENAI_API_KEY` | Images and chat | For image/chat |
| `OPENAI_BASE_URL` | Chat | Optional; defaults to `https://api.openai.com/v1` |
| `CHAT_MODEL` | Chat | Optional; defaults to `gpt-4o-mini` |
| `REPLICATE_API_TOKEN` | Video | For video |
| `REPLICATE_MODEL_VERSION` | Video | For video; the Replicate model version ID |
| `REPLICATE_VIDEO_INPUT_JSON` | Video | Optional JSON object of fixed model-specific inputs |

For example, `REPLICATE_VIDEO_INPUT_JSON` might contain a model's required fixed settings:

```json
{"num_frames": 81, "fps": 16}
```

The browser sends only the user's video `prompt`; the backend merges it with this server-side configuration, creates the Replicate prediction, polls it for up to 10 minutes, downloads the result, and returns an MP4 to the Studio.

## API examples

```sh
curl https://YOUR-RENDER-SERVICE.onrender.com/health

curl -X POST https://YOUR-RENDER-SERVICE.onrender.com/v1/images/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"a moonlit chrome helmet in dune grass","size":"1024x1024"}' \
  --output brain.png
```

## Notes

- The Studio calls relative URLs, so it works from the same Render service without exposing provider credentials to the browser.
- The `tts` command must be installed in the Render image for speech to work. Images, video, and chat use their configured cloud providers instead.
- Replicate video models vary in their accepted inputs. Put required static inputs in `REPLICATE_VIDEO_INPUT_JSON`; leave `prompt` out of that JSON because the Studio supplies it per generation.
