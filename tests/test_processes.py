import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Timer, current_thread
from threading import enumerate as running_threads
from typing import Literal

import pytest

from checksmith import processes
from checksmith.processes import _new_capture, run_process


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


@pytest.fixture
def children(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[subprocess.Popen[bytes]]]:
    started: list[subprocess.Popen[bytes]] = []
    watchdogs: list[Timer] = []
    expired: list[int] = []
    popen = subprocess.Popen

    def start(
        argv: tuple[str, ...],
        *,
        cwd: str,
        stdin: int,
        stdout: int,
        stderr: int,
        shell: Literal[False],
        text: Literal[False],
        bufsize: int,
        close_fds: bool,
    ) -> subprocess.Popen[bytes]:
        child = popen(
            argv,
            cwd=cwd,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            shell=shell,
            text=text,
            bufsize=bufsize,
            close_fds=close_fds,
        )
        started.append(child)

        def expire() -> None:
            expired.append(child.pid)
            child.kill()

        watchdog = Timer(5.0, expire)
        watchdog.daemon = True
        watchdog.start()
        watchdogs.append(watchdog)
        return child

    monkeypatch.setattr(processes.subprocess, "Popen", start)
    try:
        yield started
    finally:
        for watchdog in watchdogs:
            watchdog.cancel()
            watchdog.join()
        for child in started:
            child.kill()
            child.wait(timeout=5)
        assert not expired, "A test subprocess exceeded its safety deadline"
        assert not any(
            thread.name.startswith("checksmith-") for thread in running_threads()
        )


def test_process_preserves_arguments_cwd_stdin_and_complete_output(
    tmp_path: Path,
    children: list[subprocess.Popen[bytes]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="checksmith.processes")
    arguments = ("two words", "quote'", "$(literal)", "*", "")
    source = (
        "import json, os, sys\n"
        "print(json.dumps([os.getcwd(), sys.stdin.read(), sys.argv[1:]]))\n"
        "os.write(2, b'warning\\r\\nunfinished')\n"
        "sys.exit(7)\n"
    )
    argv = (sys.executable, "-c", source, *arguments)

    result = run_process(
        check_id="literal",
        argv=argv,
        cwd=tmp_path,
        heartbeat_interval_seconds=10.0,
    )

    assert result.args == argv
    assert result.returncode == 7
    assert json.loads(result.stdout) == [str(tmp_path), "", list(arguments)]
    assert result.stderr == "warning\nunfinished"
    child = children[0]
    assert child.returncode == 7
    assert child.stdout is not None and child.stdout.closed
    assert child.stderr is not None and child.stderr.closed
    assert f"started program={sys.executable} pid={child.pid}" in caplog.text
    assert f"process exited pid={child.pid} code=7 elapsed=" in caplog.text
    assert "stderr: warning" in caplog.text
    assert "stderr: unfinished" in caplog.text
    assert "$(literal)" not in caplog.text
    assert "still running" not in caplog.text


def test_stderr_without_a_newline_is_logged_before_the_process_can_exit(
    tmp_path: Path,
    children: list[subprocess.Popen[bytes]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="checksmith.processes")
    release = tmp_path / "release"
    source = (
        "import os, time\n"
        "from pathlib import Path\n"
        "os.write(2, b'waiting for acknowledgement')\n"
        "while not Path('release').exists(): time.sleep(0.01)\n"
        "os.write(1, b'complete')\n"
    )

    def acknowledge(record: logging.LogRecord) -> None:
        if "stderr: waiting for acknowledgement" in record.getMessage():
            assert children[0].poll() is None
            assert current_thread().name == "MainThread"
            release.touch()

    with observe_logs(acknowledge):
        result = run_process(
            check_id="live",
            argv=(sys.executable, "-c", source),
            cwd=tmp_path,
            heartbeat_interval_seconds=10.0,
        )

    assert release.exists()
    assert result.returncode == 0
    assert result.stdout == "complete"
    assert result.stderr == "waiting for acknowledgement"


@pytest.mark.parametrize(
    ("output", "close_pipes"),
    [(b"", False), (b"private-output", False), (b"", True)],
)
def test_heartbeats_report_activity_until_the_process_finishes(
    tmp_path: Path,
    children: list[subprocess.Popen[bytes]],
    caplog: pytest.LogCaptureFixture,
    output: bytes,
    close_pipes: bool,
) -> None:
    caplog.set_level(logging.DEBUG, logger="checksmith.processes")
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
        result = run_process(
            check_id="waiting",
            argv=(sys.executable, "-c", source),
            cwd=tmp_path,
            heartbeat_interval_seconds=0.05,
        )

    assert result.returncode == 0
    assert result.stdout == output.decode("utf-8")
    assert len(heartbeats) >= 2
    assert f"pid={children[0].pid} elapsed=" in heartbeats[-1]
    assert "stderr=0 bytes" in heartbeats[-1]
    assert ("last_output=none" in heartbeats[-1]) is (not output)
    assert "private-output" not in caplog.text
    exit_index = next(
        index for index, text in enumerate(caplog.messages) if "process exited" in text
    )
    assert "still running" not in "\n".join(caplog.messages[exit_index + 1 :])


def test_both_output_pipes_are_drained_beyond_their_capacity(
    tmp_path: Path,
    children: list[subprocess.Popen[bytes]],
) -> None:
    source = (
        "import os\n"
        "for _ in range(32):\n"
        "    os.write(2, b'e' * 32768)\n"
        "    os.write(1, b'o' * 32768)\n"
    )

    result = run_process(
        check_id="large",
        argv=(sys.executable, "-c", source),
        cwd=tmp_path,
        heartbeat_interval_seconds=10.0,
    )

    assert result.returncode == 0
    assert result.stdout == "o" * 1048576
    assert result.stderr == "e" * 1048576


def test_chunk_boundaries_preserve_utf8_and_normalize_newlines() -> None:
    capture = _new_capture()
    chunks = (b"\xe2", b"\x82", b"\xac\r", b"\nline\r", b"tail", b"")

    text = "".join(capture.consume(chunk) for chunk in chunks)

    assert text == "€\nline\ntail"
    assert capture.byte_count == len(b"".join(chunks))
    assert capture.closed


@pytest.mark.parametrize("stream", [1, 2])
@pytest.mark.parametrize("output", [b"\xff", b"\xe2"])
def test_invalid_utf8_fails_immediately_and_reaps_the_child(
    tmp_path: Path,
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
        run_process(
            check_id="encoding",
            argv=(sys.executable, "-c", source),
            cwd=tmp_path,
            heartbeat_interval_seconds=10.0,
        )

    assert children[0].returncode is not None
    assert children[0].stdout is not None and children[0].stdout.closed
    assert children[0].stderr is not None and children[0].stderr.closed


def test_reader_errors_propagate_and_reap_the_child(
    tmp_path: Path,
    children: list[subprocess.Popen[bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = OSError("cannot read stderr")
    read = os.read

    def fail_stderr(fd: int, size: int) -> bytes:
        if current_thread().name == "checksmith-broken-stderr":
            raise failure
        return read(fd, size)

    monkeypatch.setattr(processes.os, "read", fail_stderr)

    with pytest.raises(OSError) as raised:
        run_process(
            check_id="broken",
            argv=(sys.executable, "-c", "import time; time.sleep(30)"),
            cwd=tmp_path,
            heartbeat_interval_seconds=10.0,
        )

    assert raised.value is failure
    assert children[0].returncode is not None


def test_interruption_reaps_the_child_and_closes_its_pipes(
    tmp_path: Path,
    children: list[subprocess.Popen[bytes]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="checksmith.processes")

    def interrupt(record: logging.LogRecord) -> None:
        if "still running" in record.getMessage():
            raise KeyboardInterrupt

    with observe_logs(interrupt), pytest.raises(KeyboardInterrupt):
        run_process(
            check_id="interrupted",
            argv=(sys.executable, "-c", "import time; time.sleep(30)"),
            cwd=tmp_path,
            heartbeat_interval_seconds=0.05,
        )

    assert children[0].returncode is not None
    assert children[0].stdout is not None and children[0].stdout.closed
    assert children[0].stderr is not None and children[0].stderr.closed


@pytest.mark.parametrize("interrupt", [False, True])
def test_inherited_pipes_are_reported_as_output_collection_and_can_be_cancelled(
    tmp_path: Path,
    children: list[subprocess.Popen[bytes]],
    caplog: pytest.LogCaptureFixture,
    interrupt: bool,
) -> None:
    caplog.set_level(logging.DEBUG, logger="checksmith.processes")
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
                run_process(
                    check_id="inherited",
                    argv=(sys.executable, "-c", source),
                    cwd=tmp_path,
                    heartbeat_interval_seconds=0.05,
                )
        else:
            result = run_process(
                check_id="inherited",
                argv=(sys.executable, "-c", source),
                cwd=tmp_path,
                heartbeat_interval_seconds=0.05,
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_blocking = os.set_blocking
    calls: list[int] = []

    def fail_second_pipe(fd: int, blocking: bool) -> None:
        calls.append(fd)
        if len(calls) == 2:
            raise OSError("cannot configure pipe")
        set_blocking(fd, blocking)

    monkeypatch.setattr(processes.os, "set_blocking", fail_second_pipe)

    with pytest.raises(OSError, match="cannot configure pipe"):
        run_process(
            check_id="setup",
            argv=(sys.executable, "-c", "import time; time.sleep(30)"),
            cwd=tmp_path,
            heartbeat_interval_seconds=10.0,
        )

    assert children[0].returncode is not None


def test_failed_start_does_not_claim_a_process_started(
    tmp_path: Path,
    children: list[subprocess.Popen[bytes]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="checksmith.processes")

    with pytest.raises(FileNotFoundError):
        run_process(
            check_id="missing",
            argv=(str(tmp_path / "missing-program"),),
            cwd=tmp_path,
            heartbeat_interval_seconds=10.0,
        )

    assert children == []
    assert caplog.messages == []


@pytest.mark.parametrize("interval", [0.0, -1.0, float("inf"), float("nan")])
def test_invalid_reporting_intervals_fail_before_starting(
    tmp_path: Path,
    children: list[subprocess.Popen[bytes]],
    interval: float,
) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        run_process(
            check_id="invalid",
            argv=(sys.executable, "-c", "pass"),
            cwd=tmp_path,
            heartbeat_interval_seconds=interval,
        )

    assert children == []
