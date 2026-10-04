"""Real slow HTTP bodies must not extend a wall-clock request deadline."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import multiprocessing
import threading
import time

import pytest

from quote_image_generator.deadline import Deadline, request
from quote_image_generator.get_image import wait_for_image, ComfyUIError


@contextmanager
def slow_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            try:
                # Each chunk arrives before a read timeout, but the total is long.
                for _ in range(100):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_slow_body_is_terminated_at_overall_deadline():
    with slow_server() as url:
        before = set(p.pid for p in multiprocessing.active_children())
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            request("GET", url, deadline=Deadline(0.6))
        assert time.monotonic() - started < 1.6
        assert set(p.pid for p in multiprocessing.active_children()) == before


def test_request_cancellation_stops_child():
    with slow_server() as url:
        stop = threading.Event()
        timer = threading.Timer(0.4, stop.set)
        timer.start()
        try:
            with pytest.raises(InterruptedError):
                request("GET", url, deadline=Deadline(5), stop_event=stop)
            assert not multiprocessing.active_children()
        finally:
            timer.cancel()
            timer.join()


def test_poll_sleep_uses_remaining_time():
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {}

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        wait_for_image(
            object(),
            "http://fixture",
            "id",
            timeout_seconds=0.03,
            poll_interval=5,
            get_request=lambda *_: Response(),
        )
    assert time.monotonic() - started < 0.3


def test_execution_error_does_not_wait_until_timeout():
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": {"status": {"status_str": "error"}}}

    with pytest.raises(ComfyUIError, match="execution failed"):
        wait_for_image(
            object(), "http://fixture", "id", get_request=lambda *_: Response()
        )
