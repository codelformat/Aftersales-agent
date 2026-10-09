import json
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_PATH = ROOT / "evals" / "expand_samples.jsonl"
LEXICON_PATH = ROOT / "knowledge" / "lexicon.json"


@pytest.fixture
def samples():
    return [json.loads(line) for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def model_pattern():
    lexicon = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
    models = {model for group in lexicon["models"].values() for model in group}
    # 长型号优先，避免把 X3 Pro 中的 X3 单独匹配出来。
    alternatives = "|".join(re.escape(model) for model in sorted(models, key=len, reverse=True))
    return re.compile(r"(?<![A-Za-z0-9])(?:" + alternatives + r")(?![A-Za-z0-9])")


def test_expand_samples_shape(samples):
    assert len(samples) == 15
    for sample in samples:
        assert isinstance(sample, dict)
        assert set(sample) == {"question", "products", "keep"}
        assert isinstance(sample["question"], str) and sample["question"].strip()
        for field in ("products", "keep"):
            assert isinstance(sample[field], list)
            assert all(isinstance(word, str) and word.strip() for word in sample[field])
        assert all(word in sample["question"] for word in sample["keep"])


def test_expand_samples_model_coverage(samples, model_pattern):
    model_samples = 0
    for sample in samples:
        models = model_pattern.findall(sample["question"])
        model_samples += bool(models)
        assert all(model in sample["keep"] for model in models)
    assert model_samples >= 5


def test_expand_samples_number_coverage(samples, model_pattern):
    number_samples = 0
    for sample in samples:
        # 型号自身的数字不计入签收天数等数字场景。
        question = model_pattern.sub("", sample["question"])
        numbers = re.findall(r"\d+(?:\.\d+)?", question)
        number_samples += bool(numbers)
        assert all(number in sample["keep"] for number in numbers)
    assert number_samples >= 3
