"""Google Cloud Speech-to-Text V2 provider for short offline-meeting notes."""

from __future__ import annotations

import json
import sys
from pathlib import Path
import base64

import httpx

from loguru import logger

from nanobot.config.google_loader import GoogleSpeechConfig


class GoogleSpeechProvider:
    """Transcribe short audio clips with Speech-to-Text V2 Recognize."""

    def __init__(self, config: GoogleSpeechConfig):
        self.config = config
        self._client = None

    @staticmethod
    def _load_credential_payload(path: Path) -> dict:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(
                f"Failed to read Google credential file from disk: {path}"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Google credential file must contain a JSON object")
        return payload

    def _build_client(self):
        try:
            from google.api_core.client_options import ClientOptions
            from google.cloud.speech_v2 import SpeechAsyncClient
            from google.oauth2 import service_account
        except ImportError as exc:
            raise RuntimeError(
                "google-cloud-speech is not installed in the active Python environment "
                f"({sys.executable}). For local runs, activate the project .venv and run "
                "\"pip install -e .\" again."
            ) from exc

        credential_payload = self._load_credential_payload(
            self.config.credential_json_path
        )
        credentials = service_account.Credentials.from_service_account_info(
            credential_payload,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return SpeechAsyncClient(
            credentials=credentials,
            client_options=ClientOptions(api_endpoint=self.config.api_endpoint),
        )

    @property
    def client(self):
        if self._client is None:
            try:
                self._client = self._build_client()
            except RuntimeError:
                raise
            except Exception as exc:
                raise RuntimeError(
                    "Failed to initialize Google Speech-to-Text client from the credential file"
                ) from exc
        return self._client

    async def transcribe(self, audio_bytes: bytes) -> str:
        """Return plain transcript text for a short in-memory audio clip."""
        if not audio_bytes:
            return ""

        # If proxy is configured, call external Interview proxy service instead of Google client
        if getattr(self.config, "proxy_url", None):
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    url = self.config.proxy_url.rstrip("/") + "/recognize"
                    payload = {
                        "audio_base64": base64.b64encode(audio_bytes).decode(),
                        "language": self.config.language_code,
                    }
                    headers = {"Accept": "application/json"}
                    if getattr(self.config, "proxy_api_key", None):
                        headers["Authorization"] = f"Bearer {self.config.proxy_api_key}"
                    resp = await client.post(url, json=payload, headers=headers)
            except Exception as exc:
                logger.exception("Interview proxy request failed")
                raise RuntimeError("Interview proxy transcription failed") from exc

            if resp.status_code >= 400:
                raise RuntimeError(f"Interview proxy returned error ({resp.status_code}): {resp.text[:300]}")

            try:
                body = resp.json()
            except Exception:
                # Accept plain text body
                return (resp.text or "").strip()

            # Accept several possible shapes
            if isinstance(body, dict):
                text = body.get("transcript") or body.get("text") or body.get("result")
                if isinstance(text, str):
                    return text.strip()
                # If 'results' array present, join
                if isinstance(body.get("results"), list):
                    parts = [str(item).strip() for item in body.get("results") if item]
                    return " ".join(parts).strip()
            if isinstance(body, str):
                return body.strip()
            return ""

        try:
            from google.cloud.speech_v2.types import cloud_speech
        except ImportError as exc:
            raise RuntimeError(
                "google-cloud-speech is not installed in the active Python environment "
                f"({sys.executable}). For local runs, activate the project .venv and run "
                "\"pip install -e .\" again."
            ) from exc

        config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=[self.config.language_code],
            model=self.config.model,
        )
        request = cloud_speech.RecognizeRequest(
            recognizer=self.config.recognizer,
            config=config,
            content=audio_bytes,
        )

        try:
            response = await self.client.recognize(request=request, timeout=60.0)
        except Exception as exc:
            logger.exception("Google Speech-to-Text V2 recognize failed")
            raise RuntimeError("Google Speech-to-Text transcription failed") from exc

        transcripts: list[str] = []
        for result in response.results:
            if not result.alternatives:
                continue
            transcript = str(result.alternatives[0].transcript or "").strip()
            if transcript:
                transcripts.append(transcript)

        return " ".join(transcripts).strip()
