import asyncio
import os
import tempfile
import psutil
from playwright.async_api import async_playwright

URL = "https://www.smule.com/recording/billie-eilish-khalid-lovely/1642378645_5198603972"

# ===== MEMORY LOGGER =====

_process = psutil.Process(os.getpid())

def log_mem(tag: str):
    try:
        rss = _process.memory_info().rss / 1024 / 1024

        children = _process.children(recursive=True)
        children_mem = 0.0

        for c in children:
            try:
                children_mem += c.memory_info().rss / 1024 / 1024
            except:
                pass

        total = rss + children_mem

        print(f"[MEM] {tag} rss={rss:.1f}MB children={children_mem:.1f}MB total={total:.1f}MB")

    except Exception as e:
        print(f"[MEM ERROR] {e}")


# ===== MAIN TEST =====

async def main():
    print("=== START ===")
    log_mem("start")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
               "--disable-dev-shm-usage",
               "--no-sandbox",
               "--disable-gpu",
               "--disable-extensions",
               "--disable-background-networking",
               "--disable-sync",
               "--disable-translate"
          ]
    )
        log_mem("after_browser_launch")

        context = await browser.new_context()
        page = await context.new_page()
        log_mem("after_new_page")

        print("[GOTO]")
        await page.goto(URL, wait_until="load", timeout=60000)
        log_mem("after_goto")

        await page.wait_for_timeout(5000)

        print("[EXTRACT MEDIA URL]")
        media_url = await page.evaluate(
            """
            () => {
              const p = window?.DataStore?.Pages?.Recording?.performance;
              const raw = p?.video_media_mp4_url || p?.media_url || null;
              if (!raw) return null;

              if (!raw.startsWith("e:")) {
                return raw;
              }

              const secretB64 = "TT18WlV5TXVeLXFXYn1WTF5qSmR9TXYpOHklYlFXWGY+SUZCRGNKPiU0emcyQ2l8dGVsamBkVlpA";

              function registerCharPool(value) {
                const padded = value + "=".repeat((4 - (value.length % 4)) % 4);
                return atob(padded);
              }

              const secretPool = registerCharPool(secretB64);
              const publicPool = registerCharPool(raw.slice(2));

              const state = Array.from({ length: 256 }, (_, i) => i);
              let h = 0;

              for (let b = 0; b < 256; b++) {
                h = (h + state[b] + secretPool.charCodeAt(b % secretPool.length)) % 256;
                [state[b], state[h]] = [state[h], state[b]];
              }

              const out = [];
              let b = 0;
              h = 0;

              for (let i = 0; i < publicPool.length; i++) {
                b = (b + 1) % 256;
                h = (h + state[b]) % 256;
                [state[b], state[h]] = [state[h], state[b]];
                out.push(
                  String.fromCharCode(
                    publicPool.charCodeAt(i) ^ state[(state[b] + state[h]) % 256]
                  )
                );
              }

              return out.join("");
            }
            """
        )

        print(f"[MEDIA URL] {media_url}")

        if not media_url:
            print("NO MEDIA URL")
            return

        log_mem("before_request")

        resp = await page.request.get(
            media_url,
            headers={
                "Referer": page.url,
                "User-Agent": await page.evaluate("() => navigator.userAgent"),
            },
        )

        print(f"[STATUS] {resp.status}")

        log_mem("before_body")

        data = await resp.body()

        log_mem("after_body")

        fd, path = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)

        with open(path, "wb") as f:
            f.write(data)

        log_mem("after_write")

        print(f"[SAVED] {path} size={len(data)}")

        await browser.close()
        log_mem("after_close")

    print("=== DONE ===")


if __name__ == "__main__":
    asyncio.run(main())