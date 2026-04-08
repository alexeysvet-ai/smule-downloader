# diag_smule_browser_download.py

import asyncio

from smule_extract_diag_variant import _extract_with_browser, extract_smule
import psutil
import os
from smule_download import pick_smule_media


# === НАСТРОЙКИ ===
URL = "https://www.smule.com/c/3041711132_5199733485"   # вставь сюда
PROXY = "http://gnktxrqy:munhcy6msboc@72.1.136.146:7037"
# =================

# =================

def log_mem(tag: str):
    process = psutil.Process(os.getpid())
    rss = process.memory_info().rss / 1024 / 1024

    children = 0
    for c in process.children(recursive=True):
        try:
            children += c.memory_info().rss
        except:
            pass

    children = children / 1024 / 1024
    total = rss + children

    print(f"[MEM] {tag} rss={rss:.1f}MB children={children:.1f}MB total={total:.1f}MB")


async def main():
    print("=== EXTRACT ===")
    log_mem("start")
    extract = await extract_smule(URL, keep_browser_open=True)
    log_mem("after_extract")

    if not extract or not extract.get("ok"):
        print("extract failed")
        return

    print(f"proxy_used={extract.get('proxy')}")

    mode, media_url = pick_smule_media(extract)
    print(f"media_url={media_url}")
    print(f"mode={mode}")


    print("\n=== TEST browser ===")
    page = extract.get("page")
    log_mem("after_page")
    if not page:
        print("no page in extract")
        return

    try:
        log_mem("before_request")
        resp = await page.request.get(media_url)
        log_mem("after_request")
        print(f"browser status={resp.status}")

        if resp.ok:
            log_mem("before_body")
            data = await resp.body()
            log_mem("after_body")
            ext = ".m4a" if mode == "audio" else ".mp4"
            out_name = f"diag_browser_download{ext}"
            with open(out_name, "wb") as f:
                f.write(data)
            print(f"browser OK, bytes={len(data)} saved_to={out_name}")
            log_mem("after_write")
        else:
            print("browser FAIL")

    except Exception as e:
        print(f"browser exception: {e}")


if __name__ == "__main__":
    asyncio.run(main())