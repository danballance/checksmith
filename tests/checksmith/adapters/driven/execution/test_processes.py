import json
import logging
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Timer, current_thread
from threading import enumerate as running_threads

import pytest

from checksmith.adapters.driven.execution import processes
from checksmith.adapters.driven.execution.processes import (
    LocalProcessRuntime,
    ProcessRuntime,
    StreamName,
    SubprocessExecutor,
    _CapturedStream,
)
from checksmith.domain.errors import ProcessOutputError


class ObservedOutput:
    def __init__(self, callback: Callable[[str], None]) -> None:
        self.callback = callback

    def write(self, text: str) -> None:
        self.callback(text)


class LogObserver(logging.Handler):
    def __init__(self, callback: Callable[[logging.LogRecord], None]) -> None:
        super().__init__(level=logging.DEBUG)
        self.callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        self.callback(record)


@contextmanager
def observe_logs(callback: Callable[[logging.LogRecord], None]) -> Iterator[None]:
    observer = LogObserver(callback)
    processes.logger.addHandler(observer)
    try:
        yield
    finally:
        processes.logger.removeHandler(observer)
        observer.close()


class DelegatingRuntime:
    def __init__(self, runtime: ProcessRuntime) -> None:
        self._runtime = runtime

    def start(self, *, argv: tuple[str, ...], cwd: Path) -> subprocess.Popen[bytes]:
        return self._runtime.start(argv=argv, cwd=cwd)

    def read(self, *, fd: int, size: int) -> bytes:
        return self._runtime.read(fd=fd, size=size)

    def set_nonblocking(self, *, fd: int) -> None:
        self._runtime.set_nonblocking(fd=fd)

    def monotonic(self) -> float:
        return self._runtime.monotonic()


class WatchedRuntime(DelegatingRuntime):
    def __init__(self, runtime: ProcessRuntime) -> None:
        super().__init__(runtime)
        self.started: list[subprocess.Popen[bytes]] = []
        self._watchdogs: list[Timer] = []
        self._expired: list[int] = []

    def start(self, *, argv: tuple[str, ...], cwd: Path) -> subprocess.Popen[bytes]:
        child = super().start(argv=argv, cwd=cwd)
        self.started.append(child)
        watchdog = Timer(5.0, self._expire, args=(child,))
        watchdog.daemon = True
        watchdog.start()
        self._watchdogs.append(watchdog)
        return child

    def _expire(self, child: subprocess.Popen[bytes]) -> None:
        self._expired.append(child.pid)
        child.kill()

    def close(self) -> None:
        for watchdog in self._watchdogs:
            watchdog.cancel()
            watchdog.join()
        for child in self.started:
            child.kill()
            child.wait(timeout=5)
        assert not self._expired, "A test subprocess exceeded its safety deadline"
        assert not any(
            thread.name.startswith("checksmith-") for thread in running_threads()
        )


class SingleByteReadRuntime(DelegatingRuntime):
    def read(self, *, fd: int, size: int) -> bytes:
        return super().read(fd=fd, size=1)


class ReadFailingRuntime(DelegatingRuntime):
    def __init__(self, runtime: ProcessRuntime, error: OSError) -> None:
        super().__init__(runtime)
        self._error = error

    def read(self, *, fd: int, size: int) -> bytes:
        if current_thread().name == "checksmith-broken-stderr":
            raise self._error
        return super().read(fd=fd, size=size)


class SetupFailingRuntime(DelegatingRuntime):
    def __init__(self, runtime: ProcessRuntime) -> None:
        super().__init__(runtime)
        self._calls = 0

    def set_nonblocking(self, *, fd: int) -> None:
        self._calls += 1
        if self._calls == 2:
            raise OSError("cannot configure pipe")
        super().set_nonblocking(fd=fd)


@pytest.fixture
def runtime() -> Iterator[WatchedRuntime]:
    runtime = WatchedRuntime(LocalProcessRuntime())
    try:
        yield runtime
    finally:
        runtime.close()


@pytest.fixture
def children(runtime: WatchedRuntime) -> list[subprocess.Popen[bytes]]:
    return runtime.started


@pytest.fixture
def executor(runtime: WatchedRuntime) -> SubprocessExecutor:
    return SubprocessExecutor(runtime)


def test_process_preserves_arguments_cwd_stdin_and_complete_output(
    tmp_path: Path,
    executor: SubprocessExecutor,
    children: list[subprocess.Popen[bytes]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="checksmith.adapters.driven.execution.processes")
    arguments = ("two words", "quote'", "$(literal)", "*", "")
    source = (
        "import json, os, sys\n"
        "print(json.dumps([os.getcwd(), sys.stdin.read(), sys.argv[1:]]))\n"
        "os.write(2, b'warning\\r\\nunfinished')\n"
        "sys.exit(7)\n"
    )
    argv = (sys.executable, "-c", source, *arguments)

    result = executor.run(
        check_id="literal",
        argv=argv,
        cwd=tmp_path,
        heartbeat_interval_seconds=10.0,
        output=None,
    )

    assert result.args == argv
    assert result.returncode == 7
    assert json.loads(result.stdout) == [str(tmp_path), "", list(arguments)]
    assert result.stderr == "warning\nunfinished"
    child = children[0]
    assert child.returncode == 7
    assert child.stdout is not None and child.stdout.closed
    assert child.stderr is not None and child.stderr.closed
    start_message = next(
        message for message in caplog.messages if "started program=" in message
    )
    assert f"started program={sys.executable} pid={child.pid}" in start_message
    assert f"process exited pid={child.pid} code=7 elapsed=" in caplog.text
    assert f"literal pid={child.pid} stdout: {result.stdout.strip()}" in caplog.messages
    assert "stderr: warning" in caplog.text
    assert "stderr: unfinished" in caplog.text
    assert "$(literal)" not in start_message
    assert "still running" not in caplog.text


@pytest.mark.parametrize(("stream", "fd"), [("stdout", 1), ("stderr", 2)])
def test_output_without_a_newline_is_logged_before_the_process_can_exit(
    tmp_path: Path,
    executor: SubprocessExecutor,
    children: list[subprocess.Popen[bytes]],
    caplog: pytest.LogCaptureFixture,
    stream: StreamName,
    fd: int,
) -> None:
    caplog.set_level(logging.DEBUG, logger="checksmith.adapters.driven.execution.processes")
    release = tmp_path / "release"
    source = (
        "import os, time\n"
        "from pathlib import Path\n"
        f"os.write({fd}, b'waiting for acknowledgement')\n"
        "while not Path('release').exists(): time.sleep(0.01)\n"
        f"os.write({3 - fd}, b'complete')\n"
    )

    def acknowledge(record: logging.LogRecord) -> None:
        if f"{stream}: waiting for acknowledgement" in record.getMessage():
            assert children[0].poll() is None
            assert current_thread().name == "MainThread"
            release.touch()

    with observe_logs(acknowledge):
        result = executor.run(
            check_id="live",
            argv=(sys.executable, "-c", source),
            cwd=tmp_path,
            heartbeat_interval_seconds=10.0,
            output=None,
        )

    assert release.exists()
    assert result.returncode == 0
    assert result.stdout == (
        "waiting for acknowledgement" if stream == "stdout" else "complete"
    )
    assert result.stderr == (
        "waiting for acknowledgement" if stream == "stderr" else "complete"
    )


@pytest.mark.parametrize(
    ("output", "close_pipes"),
    [(b"", False), (b"private-output", False), (b"", True)],
)
def test_heartbeats_report_activity_until_the_process_finishes(
    tmp_path: Path,
    executor: SubprocessExecutor,
    children: list[subprocess.Popen[bytes]],
    caplog: pytest.LogCaptureFixture,
    output: bytes,
    close_pipes: bool,
) -> None:
    caplog.set_level(logging.DEBUG, logger="checksmith.adapters.driven.execution.processes")
    release = tmp_path / "release"
    heartbeats: list[str] = []
    source = (
        "import os, time\n"
        "from pathlib import Path\n"
        f"os.write(1, {output!r})\n"
        + ("os.close(1)\nos.close(2)\n" if close_pipes else "")
        + "while not Path('release').exists(): time.sleep(0.01)\n"
    )

    def observe(record: logging.LogRecord) -> None:
        message = record.getMessage()
        if "still running" not in message:
            return
        heartbeats.append(message)
        assert children[0].poll() is None
        if len(heartbeats) >= 2 and f"stdout={len(output)} bytes" in message:
            release.touch()

    with observe_logs(observe):
        result = executor.run(
            check_id="waiting",
            argv=(sys.executable, "-c", source),
            cwd=tmp_path,
            heartbeat_interval_seconds=0.05,
            output=None,
        )

    assert result.returncode == 0
    assert result.stdout == output.decode("utf-8")
    assert len(heartbeats) >= 2
    assert f"pid={children[0].pid} elapsed=" in heartbeats[-1]
    assert "stderr=0 bytes" in heartbeats[-1]
    assert ("last_output=none" in heartbeats[-1]) is (not output)
    assert ("stdout: private-output" in caplog.text) is bool(output)
    assert all("private-output" not in heartbeat for heartbeat in heartbeats)
    exit_index = next(
        index for index, text in enumerate(caplog.messages) if "process exited" in text
    )
    assert "still running" not in "\n".join(caplog.messages[exit_index + 1 :])


def test_both_output_pipes_are_drained_beyond_their_capacity(
    tmp_path: Path,
    executor: SubprocessExecutor,
    children: list[subprocess.Popen[bytes]],
) -> None:
    source = (
        "import os\n"
        "for _ in range(32):\n"
        "    os.write(2, b'e' * 32768)\n"
        "    os.write(1, b'o' * 32768)\n"
    )

    forwarded: list[str] = []

    result = executor.run(
        check_id="large",
        argv=(sys.executable, "-c", source),
        cwd=tmp_path,
        heartbeat_interval_seconds=10.0,
        output=ObservedOutput(forwarded.append),
    )

    assert result.returncode == 0
    assert result.stdout == "o" * 1048576
    assert result.stderr == "e" * 1048576

    combined = "".join(forwarded)
    assert combined.count("o") == 1048576
    assert combined.count("e") == 1048576


def test_chunk_boundaries_preserve_utf8_and_normalize_newlines() -> None:
    capture = _CapturedStream.create()
    chunks = (b"\xe2", b"\x82", b"\xac\r", b"\nline\r", b"tail", b"")

    text = "".join(capture.consume(chunk)[0] for chunk in chunks)

    assert text == "€\r\nline\rtail"
    assert "".join(capture.fragments) == "€\nline\ntail"
    assert capture.byte_count == len(b"".join(chunks))
    assert capture.closed


@pytest.mark.parametrize("stream", [1, 2])
@pytest.mark.parametrize("output", [b"\xff", b"\xe2"])
def test_invalid_utf8_fails_immediately_and_reaps_the_child(
    tmp_path: Path,
    executor: SubprocessExecutor,
    children: list[subprocess.Popen[bytes]],
    stream: int,
    output: bytes,
) -> None:
    source = (
        "import os, time\n"
        f"os.write({stream}, {output!r})\n"
        f"os.close({stream})\n"
        "time.sleep(30)\n"
    )

    with pytest.raises(UnicodeDecodeError):
        executor.run(
            check_id="encoding",
            argv=(sys.executable, "-c", source),
            cwd=tmp_path,
            heartbeat_interval_seconds=10.0,
            output=None,
        )

    assert children[0].returncode is not None
    assert children[0].stdout is not None and children[0].stdout.closed
    assert children[0].stderr is not None and children[0].stderr.closed


def test_reader_errors_propagate_and_reap_the_child(
    tmp_path: Path,
    children: list[subprocess.Popen[bytes]],
    runtime: WatchedRuntime,
) -> None:
    failure = OSError("cannot read stderr")
    executor = SubprocessExecutor(ReadFailingRuntime(runtime, failure))

    with pytest.raises(OSError) as raised:
        executor.run(
            check_id="broken",
            argv=(sys.executable, "-c", "import time; time.sleep(30)"),
            cwd=tmp_path,
            heartbeat_interval_seconds=10.0,
            output=None,
        )

    assert raised.value is failure
    assert children[0].returncode is not None


def test_interruption_reaps_the_child_and_closes_its_pipes(
    tmp_path: Path,
    executor: SubprocessExecutor,
    children: list[subprocess.Popen[bytes]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="checksmith.adapters.driven.execution.processes")

    def interrupt(record: logging.LogRecord) -> None:
        if "still running" in record.getMessage():
            raise KeyboardInterrupt

    with observe_logs(interrupt), pytest.raises(KeyboardInterrupt):
        executor.run(
            check_id="interrupted",
            argv=(sys.executable, "-c", "import time; time.sleep(30)"),
            cwd=tmp_path,
            heartbeat_interval_seconds=0.05,
            output=None,
        )

    assert children[0].returncode is not None
    assert children[0].stdout is not None and children[0].stdout.closed
    assert children[0].stderr is not None and children[0].stderr.closed


@pytest.mark.parametrize("interrupt", [False, True])
def test_inherited_pipes_are_reported_as_output_collection_and_can_be_cancelled(
    tmp_path: Path,
    executor: SubprocessExecutor,
    children: list[subprocess.Popen[bytes]],
    caplog: pytest.LogCaptureFixture,
    interrupt: bool,
) -> None:
    caplog.set_level(logging.DEBUG, logger="checksmith.adapters.driven.execution.processes")
    release = tmp_path / "release"
    observed: list[str] = []
    descendant = (
        "import os, time\n"
        "from pathlib import Path\n"
        "deadline = time.monotonic() + 3\n"
        "while not Path('release').exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        "os.write(2, b'descendant finished')\n"
    )
    source = (
        "import subprocess, sys\n"
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}])\n"
        "print('parent finished')\n"
    )

    def observe(record: logging.LogRecord) -> None:
        observed.append(record.getMessage())
        if "collecting output" in record.getMessage():
            assert children[0].poll() == 0
            release.touch()
            if interrupt:
                raise KeyboardInterrupt

    with observe_logs(observe):
        if interrupt:
            with pytest.raises(KeyboardInterrupt):
                executor.run(
                    check_id="inherited",
                    argv=(sys.executable, "-c", source),
                    cwd=tmp_path,
                    heartbeat_interval_seconds=0.05,
                    output=None,
                )
        else:
            result = executor.run(
                check_id="inherited",
                argv=(sys.executable, "-c", source),
                cwd=tmp_path,
                heartbeat_interval_seconds=0.05,
                output=None,
            )
            assert result.returncode == 0
            assert result.stdout == "parent finished\n"
            assert result.stderr == "descendant finished"

    assert release.exists()
    assert children[0].stdout is not None and children[0].stdout.closed
    assert children[0].stderr is not None and children[0].stderr.closed
    exit_index = next(
        index for index, text in enumerate(observed) if "process exited" in text
    )
    assert "collecting output" in "\n".join(observed[exit_index + 1 :])
    assert "still running" not in "\n".join(observed[exit_index + 1 :])


def test_pipe_setup_failure_stops_already_started_readers(
    tmp_path: Path,
    children: list[subprocess.Popen[bytes]],
    runtime: WatchedRuntime,
) -> None:
    executor = SubprocessExecutor(SetupFailingRuntime(runtime))

    with pytest.raises(OSError, match="cannot configure pipe"):
        executor.run(
            check_id="setup",
            argv=(sys.executable, "-c", "import time; time.sleep(30)"),
            cwd=tmp_path,
            heartbeat_interval_seconds=10.0,
            output=None,
        )

    assert children[0].returncode is not None


def test_failed_start_does_not_claim_a_process_started(
    tmp_path: Path,
    executor: SubprocessExecutor,
    children: list[subprocess.Popen[bytes]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="checksmith.adapters.driven.execution.processes")

    with pytest.raises(FileNotFoundError):
        executor.run(
            check_id="missing",
            argv=(str(tmp_path / "missing-program"),),
            cwd=tmp_path,
            heartbeat_interval_seconds=10.0,
            output=None,
        )

    assert children == []
    assert caplog.messages == []


@pytest.mark.parametrize("interval", [0.0, -1.0, float("inf"), float("nan")])
def test_invalid_reporting_intervals_fail_before_starting(
    tmp_path: Path,
    executor: SubprocessExecutor,
    children: list[subprocess.Popen[bytes]],
    interval: float,
) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        executor.run(
            check_id="invalid",
            argv=(sys.executable, "-c", "pass"),
            cwd=tmp_path,
            heartbeat_interval_seconds=interval,
            output=None,
        )

    assert children == []


def test_executor_reuse_keeps_output_and_reader_state_per_invocation(
    tmp_path: Path,
    executor: SubprocessExecutor,
    children: list[subprocess.Popen[bytes]],
) -> None:
    first = executor.run(
        check_id="reused",
        argv=(sys.executable, "-c", "import sys; print('first'); sys.exit(3)"),
        cwd=tmp_path,
        heartbeat_interval_seconds=10.0,
        output=None,
    )
    second = executor.run(
        check_id="reused",
        argv=(sys.executable, "-c", "import sys; print('second', file=sys.stderr)"),
        cwd=tmp_path,
        heartbeat_interval_seconds=10.0,
        output=None,
    )

    assert first.returncode == 3
    assert first.stdout == "first\n"
    assert first.stderr == ""
    assert second.returncode == 0
    assert second.stdout == ""
    assert second.stderr == "second\n"
    assert len(children) == 2
    assert all(child.stdout is not None and child.stdout.closed for child in children)
    assert all(child.stderr is not None and child.stderr.closed for child in children)


@pytest.mark.parametrize("fd", [1, 2])
def test_output_is_forwarded_before_the_process_can_exit(
    tmp_path: Path,
    executor: SubprocessExecutor,
    children: list[subprocess.Popen[bytes]],
    caplog: pytest.LogCaptureFixture,
    fd: int,
) -> None:
    caplog.set_level(logging.INFO, logger="checksmith.adapters.driven.execution.processes")
    release = tmp_path / "release"
    forwarded: list[str] = []
    source = (
        "import os, time\n"
        "from pathlib import Path\n"
        f"os.write({fd}, b'waiting for acknowledgement')\n"
        "while not Path('release').exists(): time.sleep(0.01)\n"
        f"os.write({3 - fd}, b'complete')\n"
    )

    def acknowledge(text: str) -> None:
        forwarded.append(text)
        assert current_thread().name == "MainThread"
        if "".join(forwarded) == "waiting for acknowledgement":
            assert children[0].poll() is None
            release.touch()

    result = executor.run(
        check_id="forwarded",
        argv=(sys.executable, "-c", source),
        cwd=tmp_path,
        heartbeat_interval_seconds=10.0,
        output=ObservedOutput(acknowledge),
    )

    assert release.exists()
    assert result.returncode == 0
    assert "".join(forwarded) == "waiting for acknowledgementcomplete"
    assert result.stdout == ("waiting for acknowledgement" if fd == 1 else "complete")
    assert result.stderr == ("waiting for acknowledgement" if fd == 2 else "complete")
    assert not caplog.records


@pytest.mark.parametrize("fd", [1, 2])
def test_forwarding_preserves_split_utf8_ansi_and_immediate_carriage_returns(
    tmp_path: Path,
    runtime: WatchedRuntime,
    children: list[subprocess.Popen[bytes]],
    fd: int,
) -> None:
    executor = SubprocessExecutor(SingleByteReadRuntime(runtime))
    forwarded: list[str] = []
    release = tmp_path / "release"
    source = (
        "import os, time\n"
        "from pathlib import Path\n"
        f"os.write({fd}, '€\\r'.encode())\n"
        "while not Path('release').exists(): time.sleep(0.01)\n"
        f"os.write({fd}, b'\\n\\x1b[32mcomplete\\x1b[0m\\rtail')\n"
    )

    def acknowledge(text: str) -> None:
        forwarded.append(text)
        if "".join(forwarded) == "€\r":
            assert children[0].poll() is None
            release.touch()

    result = executor.run(
        check_id="raw",
        argv=(sys.executable, "-c", source),
        cwd=tmp_path,
        heartbeat_interval_seconds=10.0,
        output=ObservedOutput(acknowledge),
    )

    assert release.exists()
    assert result.returncode == 0
    assert "".join(forwarded) == "€\r\n\x1b[32mcomplete\x1b[0m\rtail"
    assert all(len(text) == 1 for text in forwarded)
    capture = result.stdout if fd == 1 else result.stderr
    assert capture == "€\n\x1b[32mcomplete\x1b[0m\ntail"


@pytest.mark.parametrize(
    "failure",
    [
        BrokenPipeError("destination closed"),
        UnicodeEncodeError("ascii", "€", 0, 1, "unsupported character"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte"),
        ValueError("destination closed"),
    ],
)
def test_forwarding_failure_aborts_and_reaps_the_child(
    tmp_path: Path,
    executor: SubprocessExecutor,
    children: list[subprocess.Popen[bytes]],
    failure: Exception,
) -> None:
    def fail(text: str) -> None:
        raise failure

    with pytest.raises(ProcessOutputError) as raised:
        executor.run(
            check_id="forwarding-failed",
            argv=(
                sys.executable,
                "-c",
                "import os, time; os.write(1, b'hi'); time.sleep(30)",
            ),
            cwd=tmp_path,
            heartbeat_interval_seconds=10.0,
            output=ObservedOutput(fail),
        )

    assert raised.value.__cause__ is failure
    assert raised.value.check_id == "forwarding-failed"
    assert raised.value.problem == str(failure)
    assert "could not forward process output" in str(raised.value)
    assert children[0].returncode is not None
    assert children[0].stdout is not None and children[0].stdout.closed
    assert children[0].stderr is not None and children[0].stderr.closed


def test_forwarding_interruption_reaps_the_child(
    tmp_path: Path,
    executor: SubprocessExecutor,
    children: list[subprocess.Popen[bytes]],
) -> None:
    def interrupt(text: str) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        executor.run(
            check_id="forwarding-interrupted",
            argv=(
                sys.executable,
                "-c",
                "import os, time; os.write(1, b'hi'); time.sleep(30)",
            ),
            cwd=tmp_path,
            heartbeat_interval_seconds=10.0,
            output=ObservedOutput(interrupt),
        )

    assert children[0].returncode is not None
    assert children[0].stdout is not None and children[0].stdout.closed
    assert children[0].stderr is not None and children[0].stderr.closed


def test_output_sink_is_not_reused_by_a_later_invocation(
    tmp_path: Path,
    executor: SubprocessExecutor,
) -> None:
    forwarded: list[str] = []
    executor.run(
        check_id="forwarding-enabled",
        argv=(sys.executable, "-c", "print('first')"),
        cwd=tmp_path,
        heartbeat_interval_seconds=10.0,
        output=ObservedOutput(forwarded.append),
    )
    result = executor.run(
        check_id="forwarding-disabled",
        argv=(sys.executable, "-c", "print('second')"),
        cwd=tmp_path,
        heartbeat_interval_seconds=10.0,
        output=None,
    )

    assert "".join(forwarded) == "first\n"
    assert result.stdout == "second\n"
