import asyncio
import os
import tempfile
from typing import Optional

import aiohttp
from playwright.async_api import async_playwright
import base64
from logger import log, log_mem
from smule_cdp_download import download_in_browser_cdp
from logger import log_mem_full



DOWNLOAD_TIMEOUT_SEC = 180
PAGE_GOTO_TIMEOUT_MS = 60000
AFTER_GOTO_WAIT_MS = 3000
AFTER_COOKIE_WAIT_MS = 5000
SMULE_SECRET_KEY = "TT18WlV5TXVeLXFXYn1WTF5qSmR9TXYpOHklYlFXWGY+SUZCRGNKPiU0emcyQ2l8dGVsamBkVlpA"

def decode_smule_url(url_encoded: str | None) -> str | None:
    if not url_encoded or not url_encoded.startswith("e:"):
        return url_encoded

    def register_char_pool(value: str) -> str:
        return base64.b64decode(value + "=" * (-len(value) % 4)).decode("latin1")

    secret_pool = register_char_pool(SMULE_SECRET_KEY)
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

def build_proxy_config(proxy: str) -> dict:
    raw = proxy.strip()

    if "://" not in raw:
        raw = f"http://{raw}"

    scheme, rest = raw.split("://", 1)

    if "@" in rest:
        auth, hostport = rest.split("@", 1)
        username, password = auth.split(":", 1)
        return {
            "server": f"{scheme}://{hostport}",
            "username": username,
            "password": password,
        }

    return {"server": f"{scheme}://{rest}"}


async def extract_smule_with_proxy(url: str, proxy: str) -> dict:
    proxy_cfg = build_proxy_config(proxy)

    playwright = None
    browser = None
    context = None
    page = None

    log_mem("extract:start")

    try:
        playwright = await async_playwright().start()
        log_mem("extract:after_playwright_start")

        browser = await playwright.chromium.launch(
            headless=True,
            proxy=proxy_cfg,
        )
        log_mem("extract:after_browser_launch")

        context = await browser.new_context(accept_downloads=True)
        log_mem("extract:after_new_context")

        page = await context.new_page()
        log_mem("extract:after_new_page")

        media_urls = set()

        def on_request(req):
            try:
                u = req.url
                if ".m4a" in u or ".mp4" in u or ".m3u8" in u:
                    media_urls.add(u)
                    log(f"[REQ MEDIA] {u}")
            except Exception as e:
                log(f"[REQ MEDIA ERROR] {e}")

        page.on("request", on_request)

        log(f"[GOTO] {url}")
        await page.goto(url, wait_until="load", timeout=PAGE_GOTO_TIMEOUT_MS)
        log_mem("extract:after_goto")

        await page.wait_for_function(
            "() => !!window?.DataStore?.Pages?.Recording?.performance",
            timeout=PAGE_GOTO_TIMEOUT_MS,
        )
        log_mem("extract:after_wait_perf_ready")

        await page.wait_for_timeout(AFTER_GOTO_WAIT_MS)
        log_mem("extract:after_wait_1")

        try:
            await page.click("text=Accept Cookies", timeout=3000)
            log("[COOKIE BANNER] clicked")
        except Exception as e:
            log(f"[COOKIE BANNER] skip error={e}")

        await page.wait_for_timeout(AFTER_COOKIE_WAIT_MS)
        log_mem("extract:after_wait_2")

        perf = await page.evaluate(
            """
            () => {
              const p = window?.DataStore?.Pages?.Recording?.performance || null;
              if (!p) return null;
              return {
                title: p.title ?? null,
                artist: p.artist ?? null,
                perf_type: p.type ?? null,
                perf_status: p.perf_status ?? null,
                media_url: p.media_url ?? null,
                video_media_url: p.video_media_url ?? null,
                video_media_mp4_url: p.video_media_mp4_url ?? null
              };
            }
            """
        )
        log_mem("extract:after_page_evaluate")

        return {
            "ok": True,
            "proxy": proxy,
            "perf": perf,
            "media": list(media_urls),
            "page": page,
            "context": context,
            "browser": browser,
            "playwright": playwright,
        }

    except Exception as e:
        log(f"[EXTRACT ERROR] {type(e).__name__}: {e}")
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass
        return {"ok": False, "reason": str(e)}


def pick_media(extract: dict) -> tuple[Optional[str], Optional[str]]:
    perf = extract.get("perf") or {}
    media = extract.get("media") or []

    perf_type = perf.get("perf_type")

    direct_audio = decode_smule_url(perf.get("media_url"))
    direct_video_mp4 = decode_smule_url(perf.get("video_media_mp4_url"))
    direct_video = decode_smule_url(perf.get("video_media_url"))

    log(f"[DECODE] perf_type={perf_type}")
    log(f"[DECODE] media_url={direct_audio}")
    log(f"[DECODE] video_media_url={direct_video}")
    log(f"[DECODE] video_media_mp4_url={direct_video_mp4}")

    media_m4a = None
    media_mp4 = None

    for url in media:
        if not media_m4a and ".m4a" in url:
            media_m4a = url
        if not media_mp4 and ".mp4" in url:
            media_mp4 = url

    log(f"[MEDIA CANDIDATES] media_mp4={media_mp4}")
    log(f"[MEDIA CANDIDATES] media_m4a={media_m4a}")

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

async def download_in_browser_via_cdp(extract: dict, media_url: str, mode: str) -> str:
    return await download_in_browser_cdp(extract, media_url, mode)

async def download_in_browser_via_fetch_stream(extract: dict, media_url: str, mode: str) -> str:
    page = extract.get("page")
    if not page:
        raise RuntimeError("Browser page not available")

    suffix = ".m4a" if mode == "audio" else ".mp4"
    fd, temp_path = tempfile.mkstemp(prefix="smule_fetch_stream_", suffix=suffix)
    os.close(fd)

    callback_name = f"oai_write_chunk_{abs(hash(temp_path))}"
    stream_done = asyncio.Event()
    stream_error: str | None = None
    total_bytes = 0

    def _write_chunk(chunk_b64: str):
        nonlocal total_bytes
        chunk = base64.b64decode(chunk_b64)
        with open(temp_path, "ab") as f:
            f.write(chunk)
        total_bytes += len(chunk)

    def _stream_done():
        stream_done.set()

    def _stream_fail(message: str):
        nonlocal stream_error
        stream_error = message
        stream_done.set()

    await page.expose_function(callback_name, _write_chunk)
    await page.expose_function(f"{callback_name}_done", _stream_done)
    await page.expose_function(f"{callback_name}_fail", _stream_fail)

    try:
        log(f"[FETCH STREAM] mode={mode} media_url={media_url}")
        log_mem("fetch_stream:before_eval")

        await page.evaluate(
            """
            async ({ mediaUrl, callbackName }) => {
              try {
                const resp = await fetch(mediaUrl, {
                  method: "GET",
                  credentials: "include",
                  mode: "cors",
                });

                if (!resp.ok) {
                  await window[callbackName + "_fail"](`HTTP ${resp.status}`);
                  return;
                }

                if (!resp.body) {
                  await window[callbackName + "_fail"]("Response body is empty");
                  return;
                }

                const reader = resp.body.getReader();

                while (true) {
                  const { done, value } = await reader.read();
                  if (done) break;
                  if (!value || !value.length) continue;

                  let binary = "";
                  for (let i = 0; i < value.length; i++) {
                    binary += String.fromCharCode(value[i]);
                  }

                  await window[callbackName](btoa(binary));
                }

                await window[callbackName + "_done"]();
              } catch (e) {
                await window[callbackName + "_fail"](String(e));
              }
            }
            """,
            {"mediaUrl": media_url, "callbackName": callback_name},
        )

        await asyncio.wait_for(stream_done.wait(), timeout=180)
        log_mem("fetch_stream:after_eval")

        if stream_error:
            raise RuntimeError(stream_error)

        size = os.path.getsize(temp_path)
        log(f"[FETCH STREAM SAVED] path={temp_path} size={size} total_bytes={total_bytes}")
        log_mem("fetch_stream:after_saved")

        if size == 0:
            raise RuntimeError("Downloaded file is empty")

        return temp_path

    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise

async def download_via_aiohttp_stream(extract: dict, media_url: str, mode: str) -> str:
    context = extract.get("context")
    page = extract.get("page")
    proxy = extract.get("proxy")

async def download_via_aiohttp_stream(extract: dict, media_url: str, mode: str) -> str:
    context = extract.get("context")
    page = extract.get("page")
    proxy = extract.get("proxy")

    if not context or not page:
        raise RuntimeError("Browser context/page not available")

    suffix = ".m4a" if mode == "audio" else ".mp4"
    fd, temp_path = tempfile.mkstemp(prefix="smule_http_", suffix=suffix)
    os.close(fd)

    try:
        cookies = await context.cookies()
        cookie_header = "; ".join(f'{c["name"]}={c["value"]}' for c in cookies)

        user_agent = await page.evaluate("() => navigator.userAgent")

        user_agent = await page.evaluate("() => navigator.userAgent")

        headers = {
            "User-Agent": user_agent,
            "Accept": "*/*",
            "Accept-Encoding": "identity;q=1,*;q=0",
            "Referer": "https://www.smule.com/",
            "Sec-Fetch-Dest": "video",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
            "Range": "bytes=0-",
            "Cookie": cookie_header,
        }

        log(f"[HTTP STREAM] mode={mode} media_url={media_url}")
        log(f"[HTTP STREAM] proxy={proxy}")
        log_mem("http_stream:before_get")

        timeout = aiohttp.ClientTimeout(total=180)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                media_url,
                headers=headers,
                proxy=proxy,
            ) as resp:
                log(f"[HTTP STREAM] status={resp.status}")
                log(f"[HTTP STREAM] content_type={resp.headers.get('Content-Type')}")
                log(f"[HTTP STREAM] content_length={resp.headers.get('Content-Length')}")
                log_mem("http_stream:after_get")

                resp.raise_for_status()

                with open(temp_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)

        size = os.path.getsize(temp_path)
        log(f"[HTTP STREAM SAVED] path={temp_path} size={size}")
        log_mem("http_stream:after_saved")

        if size == 0:
            raise RuntimeError("Downloaded file is empty")

        return temp_path

    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise

async def download_in_browser(extract: dict, media_url: str, mode: str) -> str:
    page = extract.get("page")
    if not page:
        raise RuntimeError("Browser page not available")

    suffix = ".m4a" if mode == "audio" else ".mp4"
    fd, temp_path = tempfile.mkstemp(prefix="smule_browser_", suffix=suffix)
    os.close(fd)

    try:
        log(f"[BROWSER DOWNLOAD TRY] mode={mode} media_url={media_url}")
        log_mem("browser_download:before_expect")

        async with page.expect_download(timeout=DOWNLOAD_TIMEOUT_SEC * 1000) as download_info:
            await page.evaluate(
                """
                (url) => {
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = "";
                  a.rel = "noopener";
                  document.body.appendChild(a);
                  a.click();
                  a.remove();
                }
                """,
                media_url,
            )

        download = await download_info.value
        log(f"[BROWSER DOWNLOAD] suggested={download.suggested_filename}")
        log_mem("browser_download:after_expect")

        await download.save_as(temp_path)

        size = os.path.getsize(temp_path)
        log(f"[BROWSER DOWNLOAD SAVED] path={temp_path} size={size}")
        log_mem("browser_download:after_saved")

        if size == 0:
            raise RuntimeError("Downloaded file is empty")

        return temp_path

    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise

async def close_extract(extract: dict):
    page = extract.get("page")
    context = extract.get("context")
    browser = extract.get("browser")
    playwright = extract.get("playwright")

    log_mem("close:start")

    try:
        if page is not None:
            await page.close()
            log_mem("close:after_page_close")
    except Exception as e:
        log(f"[CLOSE PAGE ERROR] {e}")

    try:
        if context is not None:
            await context.close()
            log_mem("close:after_context_close")
    except Exception as e:
        log(f"[CLOSE CONTEXT ERROR] {e}")

    try:
        if browser is not None:
            await browser.close()
            log_mem("close:after_browser_close")
    except Exception as e:
        log(f"[CLOSE BROWSER ERROR] {e}")

    try:
        if playwright is not None:
            await playwright.stop()
            log_mem("close:after_playwright_stop")
    except Exception as e:
        log(f"[CLOSE PLAYWRIGHT ERROR] {e}")

from curl_cffi.requests import AsyncSession


async def download_via_curl_cffi_stream(extract: dict, media_url: str, mode: str) -> str:
    context = extract.get("context")
    page = extract.get("page")
    proxy = extract.get("proxy")

    if not context or not page:
        raise RuntimeError("Browser context/page not available")

    suffix = ".m4a" if mode == "audio" else ".mp4"
    fd, temp_path = tempfile.mkstemp(prefix="smule_curl_", suffix=suffix)
    os.close(fd)

    try:
        # cookies → dict
        cookies_list = await context.cookies()
        cookies = {c["name"]: c["value"] for c in cookies_list}

        # user-agent из браузера
        user_agent = await page.evaluate("() => navigator.userAgent")

        headers = {
            "User-Agent": user_agent,
            "Referer": "https://www.smule.com/",
        }

        log(f"[CURL STREAM] mode={mode} media_url={media_url}")
        log(f"[CURL STREAM] proxy={proxy}")
        log_mem("curl_stream:before_get")

        async with AsyncSession(impersonate="chrome120") as session:
            resp = await session.get(
                media_url,
                headers=headers,
                cookies=cookies,
                proxies={"https": proxy} if proxy else None,
                stream=True,
            )

            log(f"[CURL STREAM] status={resp.status_code}")
            log_mem("curl_stream:after_get")

            resp.raise_for_status()

            with open(temp_path, "wb") as f:
                async for chunk in resp.aiter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)

        size = os.path.getsize(temp_path)
        log(f"[CURL STREAM SAVED] path={temp_path} size={size}")
        log_mem("curl_stream:after_saved")

        if size == 0:
            raise RuntimeError("Downloaded file is empty")

        return temp_path

    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise