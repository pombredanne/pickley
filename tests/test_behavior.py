import os

import pytest
from mock import mock_open, patch

from pickley import system
from pickley.context import CaptureOutput, ImplementationMap
from pickley.lock import SharedVenv, SoftLock, SoftLockException
from pickley.settings import Settings

from .conftest import INEXISTING_FILE, PROJECT, verify_abort


def test_lock(temp_base):
    folder = os.path.join(temp_base, "foo")
    with SoftLock(folder, timeout=10) as lock:
        assert lock._locked()
        with pytest.raises(SoftLockException):
            with SoftLock(folder, timeout=0.01):
                pass
        assert str(lock) == folder + ".lock"
        system.delete(str(lock))
        assert not lock._locked()

        with patch("pickley.system.virtualenv_path", return_value=None):
            assert "Can't determine path to virtualenv.py" in verify_abort(SharedVenv, lock, None)


@patch("pickley.system.run_program", return_value="pex==1.0")
@patch("pickley.system.file_younger", return_value=True)
def test_ensure_freeze(_, __, temp_base):
    # Test edge case for _installed_module()
    with SoftLock(temp_base) as lock:
        fake_pex = os.path.join(temp_base, "bin/pex")
        system.touch(fake_pex)
        system.make_executable(fake_pex)
        v = SharedVenv(lock, None)
        assert v._installed_module("pex")


def test_flattened():
    assert len(system.flattened(None)) == 0
    assert len(system.flattened("")) == 0
    assert system.flattened("a b") == ["a b"]
    assert system.flattened("a b", separator=" ") == ["a", "b"]
    assert system.flattened(["a b"]) == ["a b"]
    assert system.flattened(["a b", ["a b c"]]) == ["a b", "a b c"]
    assert system.flattened(["a b", ["a b c"]], separator=" ") == ["a", "b", "c"]
    assert system.flattened(["a b", ["a b c"], "a"], separator=" ", unique=False) == ["a", "b", "a", "b", "c", "a"]

    assert system.flattened(["a b", [None, "-i", None]]) == ["a b", "-i"]
    assert system.flattened(["a b", [None, "-i", None]], unique=False) == ["a b"]


def test_file_operations(temp_base):
    system.touch("foo")
    with CaptureOutput(dryrun=True) as logged:
        system.copy("foo", "bar")
        system.move("foo", "bar")
        system.delete("foo")
        assert system.make_executable("foo") == 1
        assert system.write_contents("foo", "bar", quiet=False) == 1
        assert "Would copy foo -> bar" in logged
        assert "Would move foo -> bar" in logged
        assert "Would delete foo" in logged
        assert "Would make foo executable" in logged
        assert "Would write 3 bytes to foo" in logged


def test_edge_cases(temp_base):
    assert system.added_env_paths(dict(FOO=":."), env=dict(FOO="bar:baz")) == dict(FOO="bar:baz:.")

    assert system.check_pid(None) is False
    assert system.check_pid("foo") is False
    with patch("os.kill", return_value=True):
        assert system.check_pid(5) is True

    assert not system.resolved_path("")

    assert system.write_contents("", "") == 0

    assert system.which("") is None
    assert system.which(INEXISTING_FILE) is None
    assert system.which("foo/bar/baz/not/a/program") is None
    assert system.which("bash")

    assert system.ensure_folder("") == 0

    assert "does not exist" in verify_abort(system.move, INEXISTING_FILE, "bar")

    assert "Can't create folder" in verify_abort(system.ensure_folder, INEXISTING_FILE)

    assert system.copy("", "") == 0
    assert system.move("", "") == 0

    with CaptureOutput(dryrun=True) as logged:
        assert system.copy("foo/bar/baz", "foo", fatal=False) == -1
        assert "source contained in destination" in logged

        assert system.copy("foo/bar/baz", "foo/baz", fatal=False) == 1
        assert system.copy("foo/bar/baz", "foo/bar", fatal=False) == 1

    assert system.delete("/dev/null", fatal=False) == -1
    assert system.delete("/dev/null", fatal=False) == -1
    assert system.make_executable(INEXISTING_FILE, fatal=False) == -1
    assert system.make_executable("/dev/null", fatal=False) == -1

    assert "is not installed" in verify_abort(system.run_program, INEXISTING_FILE)
    assert "exited with code" in verify_abort(system.run_program, "ls", INEXISTING_FILE)

    assert system.run_program(INEXISTING_FILE, fatal=False) is None
    assert system.run_program("ls", INEXISTING_FILE, fatal=False) is None

    # Can't copy non-existing file
    with patch("os.path.exists", return_value=False):
        assert system.copy("foo", "bar", fatal=False) == -1

    # Can't read
    with patch("os.path.isfile", return_value=True):
        with patch("os.path.getsize", return_value=10):
            with patch("io.open", mock_open()) as m:
                m.side_effect = Exception
                assert "Can't read" in verify_abort(system.relocate_venv, "foo", "source", "dest")

    # Can't write
    with patch("pickley.system.open", mock_open()) as m:
        m.return_value.write.side_effect = Exception
        assert "Can't write" in verify_abort(system.write_contents, "foo", "test")

    # Copy/move crash
    with patch("os.path.exists", return_value=True):
        with patch("shutil.copy", side_effect=Exception):
                assert system.copy("foo", "bar", fatal=False) == -1
        with patch("shutil.move", side_effect=Exception):
            assert system.move("foo", "bar", fatal=False) == -1


@patch("subprocess.Popen", side_effect=Exception)
def test_popen_crash(_):
    assert "ls failed:" in verify_abort(system.run_program, "ls")


def test_real_run():
    s = Settings()
    s.load_config()
    assert len(s.config_paths) == 1
    s.load_config("foo.json")
    assert len(s.config_paths) == 2


def test_missing_implementation():
    m = ImplementationMap("custom")
    m.register(ImplementationMap)
    assert len(m.names()) == 1
    assert "No custom type configured" in verify_abort(m.resolved, "foo")
    system.SETTINGS.cli.contents["custom"] = "bar"
    assert "Unknown custom type" in verify_abort(m.resolved, "foo")


def test_relocate_venv_successfully(temp_base):
    system.write_contents("foo", "line 1: source\nline 2\n", quiet=False)
    assert system.relocate_venv("foo", "source", "dest", fatal=False) == 1
    assert system.get_lines("foo") == ["line 1: dest\n", "line 2\n"]


def test_find_venvs():
    # There's always at least one venv in project when running tests
    # No need to check which ones are there, just that they yield bin folders
    venvs = list(system.find_venvs(PROJECT))
    assert venvs
    assert os.path.basename(venvs[0]) == "bin"
