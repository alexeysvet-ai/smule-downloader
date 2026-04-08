# === smule_download.py ===
# BUILD: 20260407-01-SMULE-DOWNLOAD

import base64
import os
import re
import tempfile
import aiohttp
from config import SMULE_SECRET_KEY as SECRET_KEY

def decode_smule_url(url_encoded: str | None) -> str | None:
    if not url_encoded or not url_encoded.startswith("e:"):
        return url_encoded

    def register_char_pool(value: str) -> str:
        return base64.b64decode(value + "=" * (-len(value) % 4)).decode("latin1")

    secret_pool = register_char_pool(SECRET_KEY)
    public_pool = register_char_pool(url_encoded[2:])
    state = list(range(256))
    h = 0
    for b in range(256):
        h = (h + state[b] + ord(secret_pool[b % len(secret_pool)])) % 256
        state[b], state[h] = state[h], state[b]
    out, b, h = [], 0, 0
    for ch in public_pool:
        b = (b + 1) % 256
        h = (h + state[b]) % 256
        state[b], state[h] = state[h], state[b]
        out.append(chr(ord(ch) ^ state[(state[b] + state[h]) % 256]))
    return "".join(out)


def pick_smule_media(extract: dict) -> tuple[str | None, str | None]:
    perf = extract.get("perf") or {}
    media = extract.get("media") or []

    perf_type = perf.get("perf_type")

    direct_audio = decode_smule_url(perf.get("media_url"))
    direct_video_mp4 = decode_smule_url(perf.get("video_media_mp4_url"))
    direct_video = decode_smule_url(perf.get("video_media_url"))

    media_m4a = None
    media_mp4 = None

    for url in media:
        if not media_m4a and ".m4a" in url:
            media_m4a = url
        if not media_mp4 and ".mp4" in url:
            media_mp4 = url

    if perf_type in ("video", "visualizer"):
        if direct_video_mp4:
            return "video", direct_video_mp4
        if direct_video and ".mp4" in direct_video:
            return "video", direct_video
        if media_mp4:
            return "video", media_mp4
        if direct_audio:
            return "audio", direct_audio
        if media_m4a:
            return "audio", media_m4a
        return None, None

    if direct_audio:
        return "audio", direct_audio
    if media_m4a:
        return "audio", media_m4a
    if media_mp4:
        return "video", media_mp4

    return None, None


def build_smule_title(extract: dict) -> str:
    perf = extract.get("perf") or {}

    title = perf.get("title") or "smule"
    artist = perf.get("artist") or ""

    base = title
    if artist:
        base = f"{artist} - {title}"

    base = re.sub(r'[\\/*?:"<>|]+', "_", base)
    base = re.sub(r"\s+", " ", base).strip()

    if not base:
        base = "smule"

    return base[:120]


async def download_smule_file(media_url: str, mode: str, proxy: str | None = None) -> str:
    suffix = ".m4a" if mode == "audio" else ".mp4"

    fd, temp_path = tempfile.mkstemp(prefix="smule_", suffix=suffix)
    os.close(fd)

    timeout = aiohttp.ClientTimeout(total=300)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        print(f"[SMULE DOWNLOAD TRY] mode={mode} proxy={proxy} media_url={media_url}")

        async with session.get(media_url, proxy=proxy) as resp:
            resp.raise_for_status()

            with open(temp_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 256):
                    if chunk:
                        f.write(chunk)

    return temp_path


def build_final_path(temp_path: str, title: str, mode: str) -> str:
    ext = "m4a" if mode == "audio" else "mp4"
    final_path = f"/tmp/{title}.{ext}"

    try:
        if os.path.exists(final_path):
            os.remove(final_path)
        os.rename(temp_path, final_path)
        return final_path
    except Exception:
        return temp_path