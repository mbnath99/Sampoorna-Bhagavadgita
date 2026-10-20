"""
Sarvam AI (Bulbul) text-to-speech for the Gita reels.

Needs the secret SARVAM_API_KEY (from dashboard.sarvam.ai). Each piece of text is
converted once and cached in .tts_cache/ so re-runs don't spend credits again.
"""
import base64, hashlib, io, json, os, re, time, wave
import numpy as np
import requests

URL = "https://api.sarvam.ai/text-to-speech"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".tts_cache")
SR = 44100

class TTSError(RuntimeError):
    pass

def available():
    return bool(os.environ.get("SARVAM_API_KEY", "").strip())

def clean_for_speech(text):
    """Remove marks the voice should not read (dandas, verse numbers, quotes, brackets)."""
    t = re.sub(r"[।॥|]+", ", ", text)
    t = re.sub(r"\d+\.\d+(\s*[-–]\s*\d+)?", " ", t)
    t = re.sub(r"[\"“”‘’'()\[\]]", " ", t)
    t = re.sub(r"\s*,\s*(,\s*)+", ", ", t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s+,", ",", t)
    return t.strip(" ,")

def _chunks(text, limit):
    """Split long text at sentence ends so each request stays under the character limit."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?।॥,])\s+", text):
        if len(cur) + len(sent) + 1 > limit and cur:
            parts.append(cur); cur = sent
        else:
            cur = (cur + " " + sent).strip()
    if cur: parts.append(cur)
    return parts

def _wav_to_samples(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes)) as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64) / 32768
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != SR:                                  # resample to the video's audio rate
        idx = np.linspace(0, len(x) - 1, int(len(x) * SR / sr))
        x = np.interp(idx, np.arange(len(x)), x)
    return x

def _request(text, cfg):
    key = os.environ["SARVAM_API_KEY"].strip()
    body = {"text": text, "target_language_code": cfg.get("language", "te-IN"),
            "speaker": cfg.get("speaker", "tarun_te_narration"), "model": cfg.get("model", "bulbul:v4-flash"),
            "pace": cfg.get("pace", 0.95), "speech_sample_rate": 24000}
    for attempt in range(5):
        r = requests.post(URL, headers={"api-subscription-key": key, "Content-Type": "application/json"},
                          data=json.dumps(body), timeout=120)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt); continue
        if r.status_code in (400, 422) and "target_language_code" in body and "language" in r.text.lower():
            body["language_code"] = body.pop("target_language_code"); continue   # API renamed the field
        if r.status_code == 403:
            raise TTSError("Sarvam rejected the API key (403). Check the SARVAM_API_KEY secret.")
        if r.status_code in (400, 422):
            raise TTSError(f"Sarvam refused the request ({r.status_code}): {r.text[:300]}. "
                           "If it mentions the speaker or model, change voice.speaker / voice.model in config.json.")
        if r.status_code == 402 or "credit" in r.text.lower() and r.status_code >= 400:
            raise TTSError("Sarvam account is out of credits. Top up at dashboard.sarvam.ai.")
        r.raise_for_status()
        return base64.b64decode(r.json()["audios"][0])
    raise TTSError("Sarvam kept returning busy/rate-limit errors. Try the run again later.")

def speak(text, cfg):
    """Text -> mono float samples at 44.1 kHz. Cached by text + voice settings."""
    text = clean_for_speech(text)
    if not text:
        return np.zeros(0)
    os.makedirs(CACHE, exist_ok=True)
    sig = json.dumps([text, cfg.get("speaker"), cfg.get("model"), cfg.get("pace"), cfg.get("language")], ensure_ascii=False)
    path = os.path.join(CACHE, hashlib.sha1(sig.encode("utf-8")).hexdigest()[:20] + ".npy")
    if os.path.exists(path):
        return np.load(path)
    limit = cfg.get("max_chars", 2000)
    pieces = []
    for part in _chunks(text, limit):
        pieces.append(_wav_to_samples(_request(part, cfg)))
        pieces.append(np.zeros(int(0.15 * SR)))
    x = np.concatenate(pieces)
    np.save(path, x)
    return x

# ------------------------------------------------------------------ numbers in Telugu words (for the spoken intro)
_ONES = ["", "ఒకటి", "రెండు", "మూడు", "నాలుగు", "ఐదు", "ఆరు", "ఏడు", "ఎనిమిది", "తొమ్మిది", "పది",
         "పదకొండు", "పన్నెండు", "పదమూడు", "పద్నాలుగు", "పదిహేను", "పదహారు", "పదిహేడు", "పద్దెనిమిది", "పందొమ్మిది"]
_TENS = {20: "ఇరవై", 30: "ముప్పై", 40: "నలభై", 50: "యాభై", 60: "అరవై", 70: "డెబ్బై", 80: "ఎనభై", 90: "తొంభై"}

def telugu_number(n):
    if n < 20: return _ONES[n]
    if n < 100:
        t, o = n // 10 * 10, n % 10
        return _TENS[t] + (" " + _ONES[o] if o else "")
    h, r = divmod(n, 100)
    head = "వంద" if h == 1 else _ONES[h] + " వందల"
    return head + (" " + telugu_number(r) if r else "")
