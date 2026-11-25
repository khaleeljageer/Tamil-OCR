"""restore_library_path: child processes of a frozen Linux build get the user's LD_LIBRARY_PATH."""
import pytest

import main

MEIPASS = "/tmp/_MEI123456"


def test_restores_original_value():
    env = {"LD_LIBRARY_PATH": MEIPASS, "LD_LIBRARY_PATH_ORIG": "/opt/lib"}
    main.restore_library_path(env, frozen=True, system="Linux")
    assert env["LD_LIBRARY_PATH"] == "/opt/lib"


def test_removes_value_when_user_had_none():
    env = {"LD_LIBRARY_PATH": MEIPASS}
    main.restore_library_path(env, frozen=True, system="Linux")
    assert "LD_LIBRARY_PATH" not in env


def test_restores_empty_original():
    env = {"LD_LIBRARY_PATH": MEIPASS, "LD_LIBRARY_PATH_ORIG": ""}
    main.restore_library_path(env, frozen=True, system="Linux")
    assert env["LD_LIBRARY_PATH"] == ""


@pytest.mark.parametrize("frozen, system", [(False, "Linux"), (True, "Windows"), (False, "Windows")])
def test_leaves_environment_alone_otherwise(frozen, system):
    env = {"LD_LIBRARY_PATH": "/user/lib", "LD_LIBRARY_PATH_ORIG": "/opt/lib"}
    main.restore_library_path(env, frozen=frozen, system=system)
    assert env == {"LD_LIBRARY_PATH": "/user/lib", "LD_LIBRARY_PATH_ORIG": "/opt/lib"}
