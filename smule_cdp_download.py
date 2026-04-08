import asyncio
import base64
import contextlib
import os
import tempfile

from logger import log, log_mem

class CDPDownloadError(Exception):
pass

async def download_in_browser_cdp(extract: dict, media_url: str, mode: str) -> str:
page = extract.get("page")
context = extract.get("context")

```
if not page or not context:
    raise RuntimeError("Browser page/context not available")

suffix = ".m4a" if mode == "audio" else ".mp4"
fd, temp_path = tempfile.mkstemp(prefix="smule_cdp_", suffix=suffix)
os.close(fd)

probe_page = None
cdp = None
out_file = None

target_request_id = None
stream_started = asyncio.Event()
stream_finished = asyncio.Event()
stream_error = None

async def _start_stream(request_id: str, response_url: str, status: int):
    nonlocal target_request_id, out_file, stream_error

    if target_request_id is not None:
        return

    target_request_id = request_id
    log(f"[CDP TARGET] request_id={request_id} status={status}")

    try:
        out_file = open(temp_path, "wb")

        result = await cdp.send("Network.streamResourceContent", {"requestId": request_id})

        buffered_data = result.get("bufferedData")
        if buffered_data:
            chunk = base64.b64decode(buffered_data)
            out_file.write(chunk)

        stream_started.set()
    except Exception as e:
        stream_error = e
        stream_started.set()
        stream_finished.set()

def _on_response_received(event):
    try:
        response = event.get("response", {})
        url = response.get("url", "")
        request_id = event.get("requestId")

        if url.startswith(media_url[:60]):
            asyncio.create_task(_start_stream(request_id, url, response.get("status")))
    except Exception as e:
        log(f"[CDP RESPONSE ERROR] {e}")

def _on_data_received(event):
    nonlocal stream_error

    try:
        if event.get("requestId") != target_request_id:
            return

        data_b64 = event.get("data")
        if not data_b64:
            return

        chunk = base64.b64decode(data_b64)
        out_file.write(chunk)
    except Exception as e:
        stream_error = e
        stream_finished.set()

def _on_loading_finished(event):
    if event.get("requestId") == target_request_id:
        stream_finished.set()

try:
    probe_page = await context.new_page()
    cdp = await context.new_cdp_session(probe_page)

    cdp.on("Network.responseReceived", _on_response_received)
    cdp.on("Network.dataReceived", _on_data_received)
    cdp.on("Network.loadingFinished", _on_loading_finished)

    await cdp.send("Network.enable")

    await probe_page.goto(
        media_url,
        referer=page.url,
        wait_until="commit",
        timeout=60000,
    )

    await asyncio.wait_for(stream_started.wait(), timeout=20)
    await asyncio.wait_for(stream_finished.wait(), timeout=180)

    if out_file:
        out_file.close()

    if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
        raise CDPDownloadError("Empty file")

    return temp_path

finally:
    with contextlib.suppress(Exception):
        if cdp:
            await cdp.detach()
    with contextlib.suppress(Exception):
        if probe_page:
            await probe_page.close()
```
