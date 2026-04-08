import asyncio
import sys

from smule_extract_diag_variant import extract_smule
from smule_download import pick_smule_media
from diag_smule_browser_session_stream import download_with_browser_session_stream


URL = "https://www.smule.com/recording/billie-eilish-khalid-lovely/1642378645_5198603972"


async def main():
    print("=== EXTRACT ===")
    extract = await extract_smule(URL, keep_browser_open=True)

    if not extract or not extract.get("ok"):
        print(f"extract failed: {extract}")
        return

    print(f"proxy_used={extract.get('proxy')}")

    mode, media_url = pick_smule_media(extract)
    print(f"mode={mode}")
    print(f"media_url={media_url}")

    if not mode or not media_url:
        print("No media_url found")
        return

    page = extract.get("page")

    if not page:
        print("No page in extract")
        return

    print("\n=== DOWNLOAD (browser session stream) ===")

    try:
        path = await download_with_browser_session_stream(page, media_url, mode)
        print(f"\nOK: {path}")
    except Exception as e:
        print(f"\nFAIL: {type(e).__name__}: {e}")

    print("\n=== DONE ===")


if __name__ == "__main__":
    asyncio.run(main())