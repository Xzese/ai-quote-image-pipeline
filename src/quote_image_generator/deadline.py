"""Hard wall-clock HTTP deadlines, including DNS and slow response bodies.

A disposable child owns each request so expiry can stop blocking native I/O.
No background request survives a timeout or cancellation.
"""

import multiprocessing
from pathlib import Path
import tempfile
import time

import requests


class Deadline:
    def __init__(self, seconds):
        if seconds <= 0:
            raise ValueError("Deadline must be positive.")
        self.end = time.monotonic() + seconds

    def remaining(self):
        value = self.end - time.monotonic()
        if value <= 0:
            raise TimeoutError("ComfyUI overall deadline exceeded.")
        return value


def _request_worker(result_path, method, url, kwargs):
    try:
        with requests.Session() as session:
            with session.request(method, url, **kwargs) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise requests.RequestException("Unexpected HTTP status.")
                Path(result_path).write_bytes(response.content)
    except Exception:
        # Provider URLs and bodies can include secrets. Only transmit a flag.
        Path(result_path + ".error").touch()


def request(method, url, *, deadline, stop_event=None, **kwargs):
    remaining = deadline.remaining()
    kwargs["timeout"] = (min(5, remaining), min(30, remaining))
    kwargs["allow_redirects"] = False
    # A file avoids a large response blocking Pipe.send/recv past the deadline.
    with tempfile.TemporaryDirectory(prefix="comfy-response-") as directory:
        path = str(Path(directory) / "response")
        process = multiprocessing.get_context("spawn").Process(
            target=_request_worker, args=(path, method, url, kwargs)
        )
        process.start()
        try:
            while process.is_alive():
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("Rendering cancelled.")
                process.join(min(0.05, deadline.remaining()))
            deadline.remaining()
            if (
                process.exitcode != 0
                or Path(path + ".error").exists()
                or not Path(path).exists()
            ):
                raise requests.RequestException("ComfyUI request failed.")
            response = requests.Response()
            response.status_code = 200
            response._content = Path(path).read_bytes()
            deadline.remaining()
            return response
        finally:
            if process.is_alive():
                process.terminate()
                process.join(0.2)
                if process.is_alive():
                    process.kill()
                    process.join()
            process.close()
