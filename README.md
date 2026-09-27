# BRAIN Backend

BRAIN is an independent creative studio and API that runs entirely from a Render web service. Its UI is served from `/`; provider keys stay only in Render environment variables.

## Built-in tools

| Tool | Endpoint | Cloud provider |
|---|---|---|
| Image generation | `POST /v1/images/generate` | OpenAI image model |
| Video generation | `POST /v1/videos/generate` | Replicate model you configure |
| Speech | `POST /v1/speak` | OpenAI text-to-speech model |
| Chat | `POST /v1/chat` | OpenAI or compatible API |
| Health | `GET /health` | BRAIN |

## Deploy without a computer

1. Open the [upgrade pull request](https://github.com/Operationtakeover870/brain-backend/pull/1) on GitHub and merge it.
2. In Render, open your BRAIN service and choose **Manual Deploy → Deploy latest commit**. If automatic deploys are enabled, merging is enough.
3. In **Environment**, add the secret values shown in `.env.example`:
   - `OPENAI_API_KEY` enables image generation, speech, and chat.
   - `REPLICATE_API_TOKEN` and `REPLICATE_MODEL_VERSION` enable video.
4. Wait for the deployment to finish, then open your Render service URL in any browser.

The included `render.yaml` makes it possible to create a new service from the repository with Render's Blueprint flow as well. The service starts with `python3 server.py` and uses Render's `PORT` automatically.

## Video setup

Replicate models have different required inputs. Add the model version ID as `REPLICATE_MODEL_VERSION`. If the chosen model needs fixed settings, add them in `REPLICATE_VIDEO_INPUT_JSON`, for example:

```json
{"num_frames": 81, "fps": 16}
```

Do not put `prompt` in that JSON—the Studio supplies the user’s prompt for every generation.

## Notes

- This implementation is cloud-native: speech no longer requires a local `tts` program or a personal computer.
- API provider bills are separate from Render. BRAIN does not use Krea generation credits.
- Keep all real keys in Render’s secret environment settings, never in the repo or browser.
