import asyncio
import base64
import contextlib
import os
import tempfile

from logger import log, log_mem


class CDPDownloadError(Exception):
    pass


async def download_in_browser_cdp(extract: dict, media_url: str, mode: str) -> str:
    log("[CDP ENTRY] enter download_in_browser_cdp")
    log_mem("cdp:start")

    page = extract.get("page")
    context = extract.get("context")

    log(f"[CDP ENTRY] page_exists={page is not None} context_exists={context is not None}")

    if not page or not context:
        raise RuntimeError("Browser page/context not available")

    suffix = ".m4a" if mode == "audio" else ".mp4"
    fd, temp_path = tempfile.mkstemp(prefix="smule_cdp_", suffix=suffix)
    os.close(fd)

    log(f"[CDP TMPFILE] path={temp_path} suffix={suffix}")
    log_mem("cdp:after_tmpfile")

    probe_page = None
    cdp = None
    out_file = None

    target_request_id = None
    target_request_ids = set()
    stream_started = asyncio.Event()
    stream_finished = asyncio.Event()
    stream_error = None
    cdp_tasks = set()

    async def _start_stream(request_id: str, response_url: str, status: int):
        nonlocal target_request_id, target_request_ids, out_file, stream_error

        log(f"[CDP START_STREAM ENTRY] request_id={request_id} status={status} url={response_url}")
        log_mem("cdp:start_stream_entry")

        if request_id in target_request_ids:
            log(f"[CDP START_STREAM SKIP] duplicate_request_id={request_id}")
            return

        target_request_ids.add(request_id)

        if target_request_id is None:
            target_request_id = request_id

        log(f"[CDP TARGET] request_id={request_id} status={status} url={response_url}")
        log_mem("cdp:start_stream_target_set")

        try:
            out_file = open(temp_path, "wb")
            log("[CDP FILE] opened")
            log_mem("cdp:file_opened")

            log("[CDP STREAM CALL] before Network.streamResourceContent")
            log_mem("cdp:before_stream_resource_content")
            result = await cdp.send("Network.streamResourceContent", {"requestId": request_id})
            log("[CDP STREAM CALL] after Network.streamResourceContent")
            log_mem("cdp:after_stream_resource_content")

            buffered_data = result.get("bufferedData")
            if buffered_data:
                log(f"[CDP BUFFERED] base64_len={len(buffered_data)}")
                chunk = base64.b64decode(buffered_data)
                out_file.write(chunk)
                log(f"[CDP BUFFERED] bytes={len(chunk)} total={out_file.tell()}")
                log_mem("cdp:after_buffered_write")
            else:
                log("[CDP BUFFERED] empty")

            stream_started.set()
            log("[CDP STREAM STATE] stream_started.set()")
            log_mem("cdp:after_stream_started_set")
        except Exception as e:
            log(f"[CDP STREAM ERROR] {type(e).__name__}: {e}")
            log_mem("cdp:stream_error")
            stream_error = e
            stream_started.set()
            stream_finished.set()

    def _log_target_event(prefix: str, event: dict):
        request_id = event.get("requestId")
        if target_request_id is not None and request_id == target_request_id:
            log(f"[{prefix}] request_id={request_id} event={event}")

    def _on_request_will_be_sent(event):
        try:
            url = event.get("request", {}).get("url", "")
            request_id = event.get("requestId")
            if url.split("?")[0] == media_url.split("?")[0]:
                log(f"[CDP REQUEST] request_id={request_id} url={url}")
                log_mem("cdp:request_match")
        except Exception as e:
            log(f"[CDP REQUEST ERROR] {e}")

    def _on_response_received(event):
        try:
            response = event.get("response", {})
            url = response.get("url", "")
            request_id = event.get("requestId")
            status = response.get("status")
            mime_type = response.get("mimeType")

            if url.split("?")[0] == media_url.split("?")[0]:
                log(f"[CDP MATCH] request_id={request_id} status={status} mime={mime_type} url={url}")
                log_mem("cdp:response_match")
                log(f"[CDP TASK CREATE] request_id={request_id}")

                task = asyncio.create_task(
                    _start_stream(request_id, url, status)
                )
                cdp_tasks.add(task)

                def _on_task_done(t):
                    cdp_tasks.discard(t)
                    try:
                        exc = t.exception()
                        if exc:
                            log(f"[CDP TASK ERROR] {type(exc).__name__}: {exc}")
                        else:
                            log("[CDP TASK DONE] ok")
                        log_mem("cdp:task_done_callback")
                    except Exception as cb_e:
                        log(f"[CDP TASK CALLBACK ERROR] {cb_e}")

                task.add_done_callback(_on_task_done)
                log_mem("cdp:task_created")
        except Exception as e:
            log(f"[CDP RESPONSE ERROR] {e}")

    def _on_data_received(event):
        nonlocal stream_error

        try:
            request_id = event.get("requestId")
            if request_id not in target_request_ids:
                return

            data_b64 = event.get("data")
            encoded_data_length = event.get("encodedDataLength")
            data_length = event.get("dataLength")

            log(f"[CDP DATA EVENT] request_id={request_id} data_len={data_length} encoded_len={encoded_data_length}")
            log_mem("cdp:data_event")

            if not data_b64:
                log("[CDP CHUNK] empty")
                return

            chunk = base64.b64decode(data_b64)
            out_file.write(chunk)
            log(f"[CDP CHUNK] size={len(chunk)} total={out_file.tell()}")
            log_mem("cdp:after_chunk")
        except Exception as e:
            log(f"[CDP DATA ERROR] {type(e).__name__}: {e}")
            log_mem("cdp:data_error")
            stream_error = e
            stream_finished.set()

    def _on_loading_finished(event):
        _log_target_event("CDP FINISH EVENT", event)
        if event.get("requestId") == target_request_id:
            log("[CDP FINISHED] target request finished")
            log_mem("cdp:finished")
            stream_finished.set()

    def _on_loading_failed(event):
        nonlocal stream_error
        _log_target_event("CDP FAIL EVENT", event)
        if target_request_id is None or event.get("requestId") == target_request_id:
            log(f"[CDP FAILED] event={event}")
            log_mem("cdp:failed")
            stream_error = CDPDownloadError(
                f"loadingFailed errorText={event.get('errorText')} canceled={event.get('canceled')}"
            )
            stream_finished.set()

    try:
        log("[CDP SETUP] before new_page")
        log_mem("cdp:before_new_page")
        probe_page = await context.new_page()
        log("[CDP SETUP] after new_page")
        log_mem("cdp:after_new_page")

        log("[CDP SETUP] before new_cdp_session")
        log_mem("cdp:before_cdp_session")
        cdp = await context.new_cdp_session(probe_page)
        log("[CDP SETUP] after new_cdp_session")
        log_mem("cdp:after_cdp_session")

        cdp.on("Network.requestWillBeSent", _on_request_will_be_sent)
        cdp.on("Network.responseReceived", _on_response_received)
        cdp.on("Network.dataReceived", _on_data_received)
        cdp.on("Network.loadingFinished", _on_loading_finished)
        cdp.on("Network.loadingFailed", _on_loading_failed)
        log("[CDP SETUP] handlers attached")
        log_mem("cdp:handlers_attached")

        log("[CDP SETUP] before Network.enable")
        log_mem("cdp:before_network_enable")
        await cdp.send("Network.enable")
        log("[CDP SETUP] after Network.enable")
        log_mem("cdp:after_network_enable")

        log(f"[CDP GOTO] media_url={media_url}")
        log_mem("cdp:before_goto")
        await probe_page.goto(
            media_url,
            referer=page.url,
            wait_until="commit",
            timeout=60000,
        )
        log("[CDP GOTO] after goto")
        log_mem("cdp:after_goto")

        log("[CDP WAIT] before stream_started.wait")
        log_mem("cdp:before_wait_stream_started")
        await asyncio.wait_for(stream_started.wait(), timeout=20)
        log("[CDP WAIT] after stream_started.wait")
        log_mem("cdp:after_stream_started")

        if stream_error:
            log(f"[CDP WAIT] stream_error after started: {type(stream_error).__name__}: {stream_error}")
            raise stream_error

        log("[CDP WAIT] before stream_finished.wait")
        log_mem("cdp:before_wait_stream_finished")
        try:
            await asyncio.wait_for(stream_finished.wait(), timeout=180)
        except asyncio.TimeoutError:
            size = os.path.getsize(temp_path) if os.path.exists(temp_path) else -1
            log(f"[CDP TIMEOUT] file_size={size} target_request_id={target_request_id}")
            log_mem("cdp:timeout_waiting_finish")
            raise

        log("[CDP WAIT] after stream_finished.wait")
        log_mem("cdp:after_stream_finished")

        if stream_error:
            log(f"[CDP WAIT] stream_error after finished: {type(stream_error).__name__}: {stream_error}")
            raise stream_error

        if out_file:
            out_file.close()
            out_file = None
            log("[CDP FILE] closed")
            log_mem("cdp:file_closed")

        if not os.path.exists(temp_path):
            raise CDPDownloadError("File not created")

        size = os.path.getsize(temp_path)
        log(f"[CDP SAVED] path={temp_path} size={size}")
        log_mem("cdp:done")

        if size == 0:
            raise CDPDownloadError("Empty file")

        return temp_path

    finally:
        log("[CDP CLEANUP] start")
        log_mem("cdp:cleanup_start")

        for task in list(cdp_tasks):
            task.cancel()
            log("[CDP CLEANUP] canceled task")

        with contextlib.suppress(Exception):
            if out_file and not out_file.closed:
                out_file.close()
                log("[CDP CLEANUP] file closed")

        with contextlib.suppress(Exception):
            if cdp:
                await cdp.detach()
                log("[CDP CLEANUP] cdp detached")

        with contextlib.suppress(Exception):
            if probe_page:
                await probe_page.close()
                log("[CDP CLEANUP] probe page closed")

        log_mem("cdp:cleanup_end")
