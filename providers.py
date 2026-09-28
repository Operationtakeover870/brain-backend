"""Cloud-backed providers for BRAIN Studio.

All secrets are read only from environment variables on the server. The browser
never receives a provider key.
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request


class ProviderNotConfigured(Exception):
    pass


class BaseProvider:
    name = "base"

    def run(self, payload: dict):
        raise NotImplementedError


def openai_request(path, api_key, body, timeout=180):
    """Send a JSON request to the configured OpenAI-compatible endpoint."""
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response, response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[-500:]
        raise RuntimeError(f"provider returned {exc.code}: {detail}")
    except Exception as exc:
        raise RuntimeError(f"provider request failed: {exc}")


# ---------------------------------------------------------------- Speech
# Use OpenAI's cloud speech endpoint so BRAIN works on Render without a local
# command-line voice engine. Model choice remains configurable server-side.
VOICES = {
    "smooth": "nova",
    "warm": "alloy",
}


class OpenAITTSProvider(BaseProvider):
    name = "openai-tts"

    def run(self, payload: dict) -> dict:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ProviderNotConfigured("Set OPENAI_API_KEY on Render to enable speech.")
        text = (payload.get("text") or "").strip()
        if not text:
            raise ValueError("`text` is required")
        if len(text) > 2000:
            raise ValueError("`text` too long (max 2000 characters)")
        voice_key = (payload.get("voice") or "smooth").lower()
        voice = VOICES.get(voice_key, voice_key)
        try:
            speed = int(payload.get("speed", 100))
        except (TypeError, ValueError):
            raise ValueError("`speed` must be a number")
        if not 50 <= speed <= 200:
            raise ValueError("`speed` must be between 50 and 200")
        response, audio = openai_request(
            "/audio/speech", api_key,
            {
                "model": os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
                "input": text,
                "voice": voice,
                "speed": speed / 100,
                "response_format": "mp3",
            },
        )
        return {"content_type": response.headers.get_content_type() or "audio/mpeg", "data": audio}


# -------------------------------------------------------------- Images
class OpenAIImageProvider(BaseProvider):
    name = "openai-image"

    def run(self, payload: dict) -> dict:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ProviderNotConfigured("Set OPENAI_API_KEY on Render to enable image generation.")
        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("`prompt` is required")
        size = payload.get("size", "1024x1024")
        if size not in {"1024x1024", "1792x1024", "1024x1792"}:
            raise ValueError("unsupported image size")
        _, raw = openai_request(
            "/images/generations", api_key,
            {"model": os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1"), "prompt": prompt, "size": size},
        )
        try:
            image = json.loads(raw.decode())["data"][0]["b64_json"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            raise RuntimeError("image provider returned no image data")
        return {"content_type": "image/png", "data": base64.b64decode(image)}


# --------------------------------------------------------------- Video
class ReplicateVideoProvider(BaseProvider):
    """Run a versioned Replicate video model and wait for the output.

    `REPLICATE_VIDEO_INPUT_JSON` is merged with each prompt so model-specific
    fixed fields can be configured on Render rather than hard-coded here.
    """
    name = "replicate-video"
    api = "https://api.replicate.com/v1"

    @staticmethod
    def _request(url, token, method="GET", data=None):
        headers = {"Authorization": f"Token {token}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[-500:]
            raise RuntimeError(f"Replicate returned {exc.code}: {detail}")
        except Exception as exc:
            raise RuntimeError(f"Replicate request failed: {exc}")

    def run(self, payload: dict) -> dict:
        token = os.environ.get("REPLICATE_API_TOKEN")
        version = os.environ.get("REPLICATE_MODEL_VERSION")
        if not token:
            raise ProviderNotConfigured("Set REPLICATE_API_TOKEN on Render to enable video generation.")
        if not version:
            raise ProviderNotConfigured("Set REPLICATE_MODEL_VERSION on Render to select a video model.")
        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("`prompt` is required")
        try:
            extras = json.loads(os.environ.get("REPLICATE_VIDEO_INPUT_JSON", "{}"))
            if not isinstance(extras, dict):
                raise ValueError
        except (ValueError, json.JSONDecodeError):
            raise ProviderNotConfigured("REPLICATE_VIDEO_INPUT_JSON must be a JSON object when set.")
        input_data = {**extras, "prompt": prompt}
        prediction = self._request(
            f"{self.api}/predictions", token, method="POST",
            data=json.dumps({"version": version, "input": input_data}).encode(),
        )
        prediction_id = prediction.get("id")
        if not prediction_id:
            raise RuntimeError("Replicate did not return a prediction id")
        deadline = time.monotonic() + 600
        while prediction.get("status") not in {"succeeded", "failed", "canceled"}:
            if time.monotonic() > deadline:
                raise RuntimeError("video generation timed out after 10 minutes")
            time.sleep(2)
            prediction = self._request(f"{self.api}/predictions/{prediction_id}", token)
        if prediction.get("status") != "succeeded":
            raise RuntimeError(prediction.get("error") or "video generation did not complete")
        output = prediction.get("output")
        if isinstance(output, list):
            output = output[-1] if output else None
        if isinstance(output, dict):
            output = output.get("url") or output.get("video")
        if not isinstance(output, str) or not output.startswith("http"):
            raise RuntimeError("Replicate returned no downloadable video output")
        try:
            with urllib.request.urlopen(output, timeout=180) as response:
                return {
                    "content_type": response.headers.get_content_type() or "video/mp4",
                    "data": response.read(),
                }
        except Exception as exc:
            raise RuntimeError(f"could not download generated video: {exc}")


# ----------------------------------------------------------------- Chat
class ChatProvider(BaseProvider):
    name = "openai-chat"

    def run(self, payload: dict) -> dict:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ProviderNotConfigured("Set OPENAI_API_KEY on Render to enable chat.")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("`messages` is required (a list of {role, content})")
        _, raw = openai_request(
            "/chat/completions", api_key,
            {"model": os.environ.get("CHAT_MODEL", "gpt-4o-mini"), "messages": messages},
            timeout=120,
        )
        try:
            reply = json.loads(raw.decode())["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            raise RuntimeError("chat provider returned no reply")
        return {"json": {"reply": reply}}


PROVIDERS = {
    "tts": OpenAITTSProvider(),
    "image": OpenAIImageProvider(),
    "video": ReplicateVideoProvider(),
    "chat": ChatProvider(),
}
