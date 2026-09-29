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
from typing import IO, Final, Protocol

from pydantic import BaseModel, ConfigDict

from checksmith.application.ports.execution import ProcessOutput, StreamName
from checksmith.domain.errors import ProcessOutputError

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS: Final = 0.05
READ_SIZE: Final = 65536


class _OutputChunk(BaseModel):
    stream: StreamName
    data: bytes


class _ReadError(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    error: OSError


type _OutputEvent = _OutputChunk | _ReadError


class _CapturedStream(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    decoder: codecs.IncrementalDecoder
    newline_decoder: IncrementalNewlineDecoder
    fragments: list[str]
    byte_count: int
    closed: bool

    def consume(self, data: bytes) -> tuple[str, str]:
        self.byte_count += len(data)
        self.closed = not data
        raw = self.decoder.decode(data, final=self.closed)
        normalized = self.newline_decoder.decode(raw, final=self.closed)
        self.fragments.append(normalized)
        return raw, normalized

    @classmethod
    def create(cls) -> _CapturedStream:
        return cls(
            decoder=codecs.getincrementaldecoder("utf-8")("strict"),
            newline_decoder=IncrementalNewlineDecoder(None, translate=True),
            fragments=[],
            byte_count=0,
            closed=False,
        )


class ProcessExecutor(Protocol):
    def run(
        self,
        *,
        check_id: str,
        argv: tuple[str, ...],
        cwd: Path,
        heartbeat_interval_seconds: float,
        output: ProcessOutput | None,
    ) -> subprocess.CompletedProcess[str]: ...


class ProcessRuntime(Protocol):
    def start(self, *, argv: tuple[str, ...], cwd: Path) -> subprocess.Popen[bytes]: ...

    def read(self, *, fd: int, size: int) -> bytes: ...

    def set_nonblocking(self, *, fd: int) -> None: ...

    def monotonic(self) -> float: ...


class LocalProcessRuntime:
    def start(self, *, argv: tuple[str, ...], cwd: Path) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
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

    def read(self, *, fd: int, size: int) -> bytes:
        return os.read(fd, size)

    def set_nonblocking(self, *, fd: int) -> None:
        os.set_blocking(fd, False)

    def monotonic(self) -> float:
        return monotonic()


class SubprocessExecutor:
    def __init__(self, runtime: ProcessRuntime) -> None:
        self._runtime = runtime

    def _read_stream(
        self,
        *,
        stream: StreamName,
        pipe: IO[bytes],
        events: Queue[_OutputEvent],
        stopping: Event,
    ) -> None:
        while not stopping.is_set():
            try:
                data = self._runtime.read(fd=pipe.fileno(), size=READ_SIZE)
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
        self,
        *,
        process: subprocess.Popen[bytes],
        argv: tuple[str, ...],
        check_id: str,
        events: Queue[_OutputEvent],
        started_at: float,
        heartbeat_interval_seconds: float,
        output: ProcessOutput | None,
    ) -> subprocess.CompletedProcess[str]:
        stdout = _CapturedStream.create()
        stderr = _CapturedStream.create()
        last_output_at: float | None = None
        next_heartbeat = started_at + heartbeat_interval_seconds
        exit_reported = False

        while True:
            returncode = process.poll()
            now = self._runtime.monotonic()
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
                    "none"
                    if last_output_at is None
                    else f"{now - last_output_at:.1f}s ago"
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
                event = events.get(
                    timeout=min(POLL_INTERVAL_SECONDS, next_heartbeat - now)
                )
            except Empty:
                continue
            if isinstance(event, _ReadError):
                raise event.error
            if event.data:
                last_output_at = self._runtime.monotonic()
            capture = stdout if event.stream == "stdout" else stderr
            raw, text = capture.consume(event.data)
            if output is not None and raw:
                try:
                    output.write(raw)
                except Exception as error:
                    raise ProcessOutputError(
                        check_id=check_id, problem=str(error)
                    ) from error
            if logger.isEnabledFor(logging.DEBUG):
                for line in text.splitlines():
                    logger.debug(
                        "%s pid=%d %s: %s", check_id, process.pid, event.stream, line
                    )

    def run(
        self,
        *,
        check_id: str,
        argv: tuple[str, ...],
        cwd: Path,
        heartbeat_interval_seconds: float,
        output: ProcessOutput | None,
    ) -> subprocess.CompletedProcess[str]:
        if (
            not math.isfinite(heartbeat_interval_seconds)
            or heartbeat_interval_seconds <= 0
        ):
            raise ValueError("heartbeat_interval_seconds must be finite and positive")
        started_at = self._runtime.monotonic()
        process = self._runtime.start(argv=argv, cwd=cwd)
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
                self._runtime.set_nonblocking(fd=pipe.fileno())
                reader = Thread(
                    target=partial(
                        self._read_stream,
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
            return self._collect_output(
                process=process,
                argv=argv,
                check_id=check_id,
                events=events,
                started_at=started_at,
                heartbeat_interval_seconds=heartbeat_interval_seconds,
                output=output,
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
