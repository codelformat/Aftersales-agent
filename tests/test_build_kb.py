import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def build_kb():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_kb.py"
    spec = importlib.util.spec_from_file_location("build_kb", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("readonly", ["--status", "--check"])
def test_rebuild_rejects_readonly_flags(build_kb, readonly):
    with pytest.raises(SystemExit) as exc:
        build_kb.build_parser().parse_args(["--rebuild", readonly])
    assert exc.value.code == 2


@pytest.mark.parametrize("flag", ["--rebuild", "--status", "--check"])
def test_single_operation_flag_is_allowed(build_kb, flag):
    args = build_kb.build_parser().parse_args([flag])
    assert (args.rebuild, args.status, args.check) == (
        flag == "--rebuild", flag == "--status", flag == "--check"
    )


def test_status_and_check_remain_compatible(build_kb):
    args = build_kb.build_parser().parse_args(["--status", "--check"])
    assert args.status and args.check and not args.rebuild
