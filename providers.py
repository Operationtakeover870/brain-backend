"""
BRAIN Backend — providers.

Each capability (speak, image, video, chat) is a provider with a uniform
interface. Providers that need a third-party API key raise
ProviderNotConfigured until the key is set. Add new providers by
subclassing BaseProvider and registering in PROVIDERS.
"""

import json
import os
import shutil
import subprocess
import tempfile
import urllib.request


class ProviderNotConfigured(Exception):
    pass


class BaseProvider:
    name = "base"

    def run(self, payload: dict):
        raise NotImplementedError


# ---------------------------------------------------------------- TTS (live)
# Uses the local `tts` CLI. Voices are the Meta AI catalog voices.
# ----------------------------------------------------------------

VOICES = {
    "smooth": "avocado_v2:MAI_03",  # default, Warm/Smooth set
    "warm": "avocado_v2:MAI_01",
}

DEFAULT_VOICE = VOICES["smooth"]


class TTSProvider(BaseProvider):
    name = "tts"

    def run(self, payload: dict) -> dict:
        if not shutil.which("tts"):
            raise ProviderNotConfigured(
                "Voice engine isn't installed on this host — the Speak tab "
                "only works where the backend runs with BRAIN's voice engine."
            )
        text = (payload.get("text") or "").strip()
        if not text:
            raise ValueError("`text` is required")
        if len(text) > 2000:
            raise ValueError("`text` too long (max 2000 chars)")

        voice_key = (payload.get("voice") or "smooth").lower()
        voice_id = VOICES.get(voice_key, voice_key)
        # Allow a raw catalog id straight through; anything else must be known.
        if voice_id not in VOICES.values() and not voice_id.startswith("avocado_v2:"):
            raise ValueError(f"unknown voice `{payload.get('voice')}` — try 'smooth' or 'warm'")

        speed = payload.get("speed", 100)
        try:
            speed = int(speed)
        except (TypeError, ValueError):
            raise ValueError("`speed` must be a number")

        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp_path = tmp.name
        tmp.close()
        try:
            cmd = [
                "tts", "speak",
                "--text", text,
                "--voice", voice_id,
                "--speed", str(speed),
                "--output", tmp_path,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr[-500:])
            with open(tmp_path, "rb") as f:
                audio = f.read()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return {"content_type": "audio/mpeg", "data": audio, "voice": voice_id}


# ------------------------------------------------- Image / video / chat
# Bring-your-own-key providers. Set the env var, restart the server, done.
# -------------------------------------------------

class OpenAIImageProvider(BaseProvider):
    """Image generation via OpenAI's Images API. Needs OPENAI_API_KEY."""
    name = "openai-image"

    def run(self, payload: dict) -> dict:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ProviderNotConfigured(
                "Set OPENAI_API_KEY env var to enable image generation."
            )
        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("`prompt` is required")
        size = payload.get("size", "1024x1024")
        body = json.dumps({"model": "gpt-image-1", "prompt": prompt, "size": size}).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/images/generations",
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode())
        except Exception as e:
            raise RuntimeError(f"image provider error: {e}")
        # gpt-image-1 returns b64_json by default
        b64 = result["data"][0].get("b64_json")
        if not b64:
            raise RuntimeError("image provider returned no image data")
        import base64
        return {"content_type": "image/png", "data": base64.b64decode(b64)}


class ReplicateVideoProvider(BaseProvider):
    """Video generation via Replicate. Needs REPLICATE_API_TOKEN."""
    name = "replicate-video"

    def run(self, payload: dict) -> dict:
        token = os.environ.get("REPLICATE_API_TOKEN")
        if not token:
            raise ProviderNotConfigured(
                "Set REPLICATE_API_TOKEN env var to enable video generation."
            )
        raise ProviderNotConfigured(
            "Video provider scaffold ready — pick a Replicate video model, "
            "set REPLICATE_MODEL_VERSION, then implement the prediction poll loop."
        )


class ChatProvider(BaseProvider):
    """Chat completions. Needs OPENAI_API_KEY (or point it at any OpenAI-style API)."""
    name = "chat"

    def run(self, payload: dict) -> dict:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ProviderNotConfigured(
                "Set OPENAI_API_KEY env var to enable chat."
            )
        messages = payload.get("messages")
        if not messages:
            raise ValueError("`messages` is required (list of {role, content})")
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        body = json.dumps({
            "model": os.environ.get("CHAT_MODEL", "gpt-4o-mini"),
            "messages": messages,
        }).encode()
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode())
        except Exception as e:
            raise RuntimeError(f"chat provider error: {e}")
        return {"json": {"reply": result["choices"][0]["message"]["content"]}}


PROVIDERS = {
    "tts": TTSProvider(),
    "image": OpenAIImageProvider(),
    "video": ReplicateVideoProvider(),
    "chat": ChatProvider(),
}
