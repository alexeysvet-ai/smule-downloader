import os
from aiohttp import web

from logger import log, log_mem
from smule_service import extract_smule_with_proxy, pick_media, download_in_browser, close_extract


HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "10000"))
REQUEST_PATH = "/download"

# Hardcoded for R&D:
SMULE_URL = "https://www.smule.com/c/2603336553_5199676986"
PROXY = "http://gnktxrqy:munhcy6msboc@72.1.136.146:7037"


async def handle_download(request: web.Request) -> web.Response:
    log("[HTTP] /download start")
    log_mem("http:start")

    extract = None
    file_path = None

    try:
        log(f"[INPUT] url={SMULE_URL}")
        log(f"[INPUT] proxy={PROXY}")

        extract = await extract_smule_with_proxy(SMULE_URL, PROXY)
        log_mem("http:after_extract")

        if not extract or not extract.get("ok"):
            return web.json_response(
                {"ok": False, "stage": "extract", "reason": extract.get("reason") if extract else "no_extract"},
                status=500,
            )

        perf = extract.get("perf") or {}
        mode, media_url = pick_media(perf)

        log(f"[PICK] mode={mode}")
        log(f"[PICK] media_url={media_url}")
        log_mem("http:after_pick_media")

        if not mode or not media_url:
            return web.json_response(
                {
                    "ok": False,
                    "stage": "pick_media",
                    "perf_type": perf.get("perf_type"),
                    "perf_status": perf.get("perf_status"),
                },
                status=500,
            )

        file_path = await download_in_browser(extract, media_url, mode)
        log_mem("http:after_download")

        size = os.path.getsize(file_path)
        return web.json_response(
            {
                "ok": True,
                "mode": mode,
                "file_path": file_path,
                "file_size_bytes": size,
                "proxy": extract.get("proxy"),
            }
        )

    except Exception as e:
        log(f"[HTTP ERROR] {type(e).__name__}: {e}")
        log_mem("http:error")
        return web.json_response(
            {"ok": False, "stage": "download", "error": f"{type(e).__name__}: {e}"},
            status=500,
        )

    finally:
        if extract:
            await close_extract(extract)

        # For R&D keep file on disk for inspection.
        log_mem("http:finally")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get(REQUEST_PATH, handle_download)
    return app


if __name__ == "__main__":
    log(f"[BOOT] host={HOST} port={PORT} path={REQUEST_PATH}")
    log_mem("boot")
    web.run_app(create_app(), host=HOST, port=PORT)
