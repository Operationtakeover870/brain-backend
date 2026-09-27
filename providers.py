"""
BRAIN Backend — provider integrations.

Set provider credentials as environment variables on Render. Secrets stay on the
server; the browser only calls the BRAIN endpoints.
"""

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


class ProviderNotConfigured(Exception):
    pass


class BaseProvider:
    name = "base"

    def run(self, payload: dict):
        raise NotImplementedError


# ---------------------------------------------------------------- TTS
VOICES = {
    "smooth": "avocado_v2:MAI_03",
    "warm": "avocado_v2:MAI_01",
}
DEFAULT_VOICE = VOICES["smooth"]


class TTSProvider(BaseProvider):
    name = "tts"

    def run(self, payload: dict) -> dict:
        if not shutil.which("tts"):
            raise ProviderNotConfigured(
                "Voice engine isn't installed on this host. Install BRAIN's `tts` CLI to enable speech."
            )
        text = (payload.get("text") or "").strip()
        if not text:
            raise ValueError("`text` is required")
        if len(text) > 2000:
            raise ValueError("`text` too long (max 2000 chars)")

        voice_key = (payload.get("voice") or "smooth").lower()
        voice_id = VOICES.get(voice_key, voice_key)
        if voice_id not in VOICES.values() and not voice_id.startswith("avocado_v2:"):
            raise ValueError("unknown voice — try `smooth` or `warm`")
        try:
            speed = int(payload.get("speed", 100))
        except (TypeError, ValueError):
            raise ValueError("`speed` must be a number")
        if not 50 <= speed <= 200:
            raise ValueError("`speed` must be between 50 and 200")

        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp_path = tmp.name
        tmp.close()
        try:
            proc = subprocess.run(
                ["tts", "speak", "--text", text, "--voice", voice_id, "--speed", str(speed), "--output", tmp_path],
                capture_output=True, text=True, timeout=180,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr[-500:] or "voice engine failed")
            with open(tmp_path, "rb") as output:
                audio = output.read()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return {"content_type": "audio/mpeg", "data": audio}


# ----------------------------------------------------------- Image / video / chat
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
        body = json.dumps({"model": "gpt-image-1", "prompt": prompt, "size": size}).encode()
        request = urllib.request.Request(
            "https://api.openai.com/v1/images/generations", data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                result = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"image provider returned {exc.code}: {exc.read().decode(errors='replace')[-300:]}")
        except Exception as exc:
            raise RuntimeError(f"image provider error: {exc}")
        image = result.get("data", [{}])[0].get("b64_json")
        if not image:
            raise RuntimeError("image provider returned no image data")
        return {"content_type": "image/png", "data": base64.b64decode(image)}


class ReplicateVideoProvider(BaseProvider):
    """Replicate prediction runner for a versioned video model.

    Required Render environment variables:
      REPLICATE_API_TOKEN
      REPLICATE_MODEL_VERSION

    Optional REPLICATE_VIDEO_INPUT_JSON is merged into the prediction input,
    making model-specific fixed inputs configurable without changing code.
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

        input_data = {"prompt": prompt}
        extras = os.environ.get("REPLICATE_VIDEO_INPUT_JSON", "{}")
        try:
            parsed_extras = json.loads(extras)
            if not isinstance(parsed_extras, dict):
                raise ValueError
            input_data.update(parsed_extras)
        except (json.JSONDecodeError, ValueError):
            raise ProviderNotConfigured("REPLICATE_VIDEO_INPUT_JSON must be a JSON object when set.")

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
                content_type = response.headers.get_content_type() or "video/mp4"
                video = response.read()
        except Exception as exc:
            raise RuntimeError(f"could not download generated video: {exc}")
        return {"content_type": content_type, "data": video}


class ChatProvider(BaseProvider):
    name = "chat"

    def run(self, payload: dict) -> dict:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ProviderNotConfigured("Set OPENAI_API_KEY on Render to enable chat.")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("`messages` is required (a list of {role, content})")
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        body = json.dumps({"model": os.environ.get("CHAT_MODEL", "gpt-4o-mini"), "messages": messages}).encode()
        request = urllib.request.Request(
            f"{base}/chat/completions", data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                result = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"chat provider returned {exc.code}: {exc.read().decode(errors='replace')[-300:]}")
        except Exception as exc:
            raise RuntimeError(f"chat provider error: {exc}")
        try:
            reply = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("chat provider returned no reply")
        return {"json": {"reply": reply}}


PROVIDERS = {
    "tts": TTSProvider(),
    "image": OpenAIImageProvider(),
    "video": ReplicateVideoProvider(),
    "chat": ChatProvider(),
}
