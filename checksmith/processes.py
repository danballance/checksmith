import codecs
import logging
import math
import os
import subprocess
from functools import partial
from io import IncrementalNewlineDecoder
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic
from typing import IO, Final, Literal

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS: Final = 0.05
READ_SIZE: Final = 65536

type StreamName = Literal["stdout", "stderr"]


class _OutputChunk(BaseModel):
    stream: StreamName
    data: bytes


class _ReadError(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    error: OSError


type _OutputEvent = _OutputChunk | _ReadError


class _CapturedStream(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    decoder: IncrementalNewlineDecoder
    fragments: list[str]
    byte_count: int
    closed: bool

    def consume(self, data: bytes) -> str:
        self.byte_count += len(data)
        self.closed = not data
        text = self.decoder.decode(data, final=self.closed)
        self.fragments.append(text)
        return text


def _new_capture() -> _CapturedStream:
    return _CapturedStream(
        decoder=IncrementalNewlineDecoder(
            codecs.getincrementaldecoder("utf-8")("strict"), translate=True
        ),
        fragments=[],
        byte_count=0,
        closed=False,
    )


def _read_stream(
    *,
    stream: StreamName,
    pipe: IO[bytes],
    events: Queue[_OutputEvent],
    stopping: Event,
) -> None:
    while not stopping.is_set():
        try:
            data = os.read(pipe.fileno(), READ_SIZE)
        except BlockingIOError:
            stopping.wait(POLL_INTERVAL_SECONDS)
            continue
        except OSError as error:
            events.put(_ReadError(error=error))
            return
        events.put(_OutputChunk(stream=stream, data=data))
        if not data:
            return


def _collect_output(
    *,
    process: subprocess.Popen[bytes],
    argv: tuple[str, ...],
    check_id: str,
    events: Queue[_OutputEvent],
    started_at: float,
    heartbeat_interval_seconds: float,
) -> subprocess.CompletedProcess[str]:
    stdout = _new_capture()
    stderr = _new_capture()
    last_output_at: float | None = None
    next_heartbeat = started_at + heartbeat_interval_seconds
    exit_reported = False

    while True:
        returncode = process.poll()
        now = monotonic()
        if returncode is not None and not exit_reported:
            logger.debug(
                "%s process exited pid=%d code=%d elapsed=%.3fs",
                check_id,
                process.pid,
                returncode,
                now - started_at,
            )
            exit_reported = True
        if returncode is not None and stdout.closed and stderr.closed:
            return subprocess.CompletedProcess(
                args=argv,
                returncode=returncode,
                stdout="".join(stdout.fragments),
                stderr="".join(stderr.fragments),
            )
        if now >= next_heartbeat:
            last_output = (
                "none" if last_output_at is None else f"{now - last_output_at:.1f}s ago"
            )
            logger.debug(
                "%s %s pid=%d elapsed=%.1fs stdout=%d bytes stderr=%d bytes "
                "last_output=%s",
                check_id,
                "still running" if returncode is None else "collecting output",
                process.pid,
                now - started_at,
                stdout.byte_count,
                stderr.byte_count,
                last_output,
            )
            next_heartbeat = now + heartbeat_interval_seconds
        try:
            event = events.get(timeout=min(POLL_INTERVAL_SECONDS, next_heartbeat - now))
        except Empty:
            continue
        if isinstance(event, _ReadError):
            raise event.error
        if event.data:
            last_output_at = monotonic()
        capture = stdout if event.stream == "stdout" else stderr
        text = capture.consume(event.data)
        if event.stream == "stderr":
            for line in text.splitlines():
                logger.debug("%s pid=%d stderr: %s", check_id, process.pid, line)


def run_process(
    *,
    check_id: str,
    argv: tuple[str, ...],
    cwd: Path,
    heartbeat_interval_seconds: float,
) -> subprocess.CompletedProcess[str]:
    if not math.isfinite(heartbeat_interval_seconds) or heartbeat_interval_seconds <= 0:
        raise ValueError("heartbeat_interval_seconds must be finite and positive")
    started_at = monotonic()
    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        text=False,
        bufsize=0,
        close_fds=True,
    )
    stopping = Event()
    readers: list[Thread] = []
    events: Queue[_OutputEvent] = Queue()
    try:
        logger.debug(
            "%s started program=%s pid=%d cwd=%s",
            check_id,
            argv[0],
            process.pid,
            cwd,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        pipes: tuple[tuple[StreamName, IO[bytes]], ...] = (
            ("stdout", process.stdout),
            ("stderr", process.stderr),
        )
        for stream, pipe in pipes:
            os.set_blocking(pipe.fileno(), False)
            reader = Thread(
                target=partial(
                    _read_stream,
                    stream=stream,
                    pipe=pipe,
                    events=events,
                    stopping=stopping,
                ),
                name=f"checksmith-{check_id}-{stream}",
                daemon=False,
            )
            reader.start()
            readers.append(reader)
        return _collect_output(
            process=process,
            argv=argv,
            check_id=check_id,
            events=events,
            started_at=started_at,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        stopping.set()
        for reader in readers:
            reader.join()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
