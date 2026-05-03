from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import os
import base64

app = FastAPI(title="Interview Proxy")

INTERVIEW_PROXY_API_KEY = os.environ.get("INTERVIEW_PROXY_API_KEY")
GOOGLE_CREDENTIAL_JSON_PATH = os.environ.get("GOOGLE_CREDENTIAL_JSON_PATH")

class RecognizeRequest(BaseModel):
    audio_base64: str
    language: str | None = None


@app.post("/recognize")
async def recognize(req: RecognizeRequest, request: Request):
    auth = request.headers.get("authorization", "")
    if INTERVIEW_PROXY_API_KEY:
        if not auth.startswith("Bearer ") or auth.split()[1] != INTERVIEW_PROXY_API_KEY:
            raise HTTPException(status_code=401, detail="Unauthorized")

    # If google credentials are available, attempt to call Google Speech-to-Text
    if not GOOGLE_CREDENTIAL_JSON_PATH or not os.path.exists(GOOGLE_CREDENTIAL_JSON_PATH):
        raise HTTPException(status_code=501, detail="Google credentials not configured on server")

    try:
        from google.oauth2 import service_account
        from google.cloud.speech_v2 import SpeechClient
        from google.cloud.speech_v2.types import cloud_speech
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Missing google libraries: {exc}")

    # Build client
    try:
        creds = None
        with open(GOOGLE_CREDENTIAL_JSON_PATH, "r", encoding="utf-8") as fh:
            import json
            payload = json.load(fh)
            creds = service_account.Credentials.from_service_account_info(payload)
        client = SpeechClient(credentials=creds)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to initialize Google client: {exc}")

    # Decode audio — expecting raw audio bytes (user should send proper format)
    try:
        audio_bytes = base64.b64decode(req.audio_base64)
    except Exception:
        raise HTTPException(status_code=400, detail="audio_base64 is not valid base64")

    # Build request using auto-detect decoding
    config = cloud_speech.RecognitionConfig(
        auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
        language_codes=[req.language or "en-US"],
        model="chirp_3",
    )
    request_proto = cloud_speech.RecognizeRequest(
        recognizer=f"projects/{payload.get('project_id')}/locations/global/recognizers/_",
        config=config,
        content=audio_bytes,
    )

    try:
        response = client.recognize(request=request_proto, timeout=60.0)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Google STT request failed: {exc}")

    transcripts = []
    for result in response.results:
        if not result.alternatives:
            continue
        t = str(result.alternatives[0].transcript or "").strip()
        if t:
            transcripts.append(t)

    return {"transcript": " ".join(transcripts).strip()}
