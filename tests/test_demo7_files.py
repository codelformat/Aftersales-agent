import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent


def test_demo7_dialog_has_22_turns_with_early_order_details():
    path = ROOT / "scripts" / "demo7_dialog.json"
    assert path.is_file(), "缺少 demo7 对话文件"
    dialog = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(dialog, list)
    assert len(dialog) == 22
    assert all(isinstance(line, str) and line.strip() for line in dialog)
    opening = "\n".join(dialog[:3])
    for detail in ("1001", "13800001234"):
        assert detail in opening
        assert all(detail not in line for line in dialog[3:])


def test_demo7_dialog_more_has_12_turns_without_early_order_details():
    path = ROOT / "scripts" / "demo7_dialog_more.json"
    assert path.is_file(), "缺少 demo7 续聊对话文件"
    dialog = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(dialog, list)
    assert len(dialog) == 12
    assert all(isinstance(line, str) and line.strip() for line in dialog)
    for detail in ("1001", "13800001234"):
        assert all(detail not in line for line in dialog)


def test_demo7_shell_syntax():
    path = ROOT / "scripts" / "demo7.sh"
    assert path.is_file(), "缺少 demo7 验收脚本"
    result = subprocess.run(
        ["bash", "-n", str(path)], capture_output=True, text=True, cwd=ROOT
    )
    assert result.returncode == 0, result.stderr


def test_demo7_shell_variables_before_non_ascii_are_braced():
    result = subprocess.run(
        ["/bin/bash", "-c", 'set -u; k=x; echo "${k}："'],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "x：\n"
    script = (ROOT / "scripts" / "demo7.sh").read_text(encoding="utf-8")
    assert not re.search(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7F]", script)


def test_demo7_python_syntax(tmp_path, monkeypatch):
    paths = [ROOT / "evals" / "run_token_calibration.py", ROOT / "scripts" / "demo7_chat.py"]
    for path in paths:
        assert path.is_file(), f"缺少 Python 脚本：{path.name}"
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pycache"))
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", *(str(path) for path in paths)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
