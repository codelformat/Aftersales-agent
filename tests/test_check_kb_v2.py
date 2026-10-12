import json
import subprocess

import pytest

from scripts import check_kb_v2 as ck

BASE_MANUAL = "# 商品手册\n\n本手册覆盖 8 个品类、24 个型号的参数。\n\n## 蓝牙耳机\n\n### X3 参数\n\nX3 使用蓝牙 5.2。\n"


def manual_with(models_md: str, intro=ck.INTRO_NEW) -> str:
    return BASE_MANUAL.replace(ck.INTRO_OLD, intro) + models_md


def full_new_sections() -> str:
    parts = []
    for model, category in ck.NEW_MODELS.items():
        for name in ck.SECTION_NAMES[category]:
            facts = "，".join(ck.FACTS.get((model, name), ()))
            parts.append(f"### {model} {name}\n\n{model} {facts}。\n")
    return "\n".join(parts)


def run(docs_manual, old_d=("X7 的续航？",), extra=None):
    docs = {"knowledge/docs/manual/商品手册.md": docs_manual, **(extra or {})}
    baseline = {"knowledge/docs/manual/商品手册.md": BASE_MANUAL}
    return ck.check(docs, baseline, list(old_d))


def test_complete_kb_passes():
    assert run(manual_with(full_new_sections())) == []


def test_missing_section_and_wrong_value():
    md = full_new_sections().replace("### X3 Lite 保修", "### X3 Lite 保修期").replace("350 毫升", "300 毫升")
    errors = "\n".join(run(manual_with(md)))
    assert "X3 Lite 保修" in errors and "350 毫升" in errors


def test_new_model_name_in_old_d_query():
    errors = "\n".join(run(manual_with(full_new_sections()), old_d=("x3lite 防水吗",)))
    assert "X3 Lite" in errors


def test_banned_word_in_added_line():
    md = full_new_sections() + "\n### 补充\n\n支持分期付款。\n"
    assert any("分期" in e for e in run(manual_with(md)))


def test_modified_existing_line():
    md = manual_with(full_new_sections()).replace("X3 使用蓝牙 5.2。", "X3 使用蓝牙 5.3。")
    assert any("修改或删除" in e for e in run(md))


def test_intro_not_updated():
    assert any("40 个型号" in e for e in run(manual_with(full_new_sections(), intro=ck.INTRO_OLD)))


def test_missing_baseline_commit(monkeypatch):
    def fail(*a, **k):
        raise RuntimeError("bad object")
    monkeypatch.setattr(ck.subprocess, "run", fail)
    with pytest.raises(RuntimeError):
        ck.load_baseline(["knowledge/docs/manual/商品手册.md"])


@pytest.mark.parametrize("model, section, value", [
    ("X3 Lite", "参数", "蓝牙 5.2"),
    ("X3 Lite", "参数", "3.8 克"),
    ("X3 Lite", "参数", "3 秒"),
    ("X3 Lite", "续航", "5 小时"),
    ("X3 Lite", "续航", "20 小时"),
    ("X3 Lite", "续航", "1.5 小时"),
    ("X3 Lite", "续航", "50%"),
    ("X3 Lite", "保修", "90 天"),
    ("X3 Lite", "退换规则", "7 天"),
    ("X3 Lite", "退换规则", "15 天"),
    ("X3 Lite", "退换规则", "30 天"),
    ("X3 Lite", "常见故障", "5V 1A"),
    ("X3 Lite", "常见故障", "30 分钟"),
    ("X5 Pro", "参数", "蓝牙 5.4"),
    ("X5 Pro", "参数", "5.5 克"),
    ("X5 Pro", "续航", "50%"),
    ("X5 Pro", "续航", "12 小时"),
    ("X5 Pro", "续航", "48 小时"),
    ("X5 Pro", "续航", "8 小时"),
    ("X5 Pro", "续航", "2.5 小时"),
    ("X5 Pro", "保修", "540 天"),
    ("X5 Pro", "退换规则", "7 天"),
    ("X5 Pro", "退换规则", "15 天"),
    ("X5 Pro", "退换规则", "30 天"),
    ("W1 Pro", "参数", "63 厘米"),
    ("W1 Pro", "参数", "98 厘米"),
    ("W1 Pro", "材质", "85%"),
    ("W1 Pro", "材质", "15%"),
    ("W1 Pro", "保修", "45 天"),
    ("W1 Pro", "常见故障", "2 厘米"),
    ("W2 Plus", "参数", "68 厘米"),
    ("W2 Plus", "参数", "106 厘米"),
    ("W2 Plus", "参数", "6 颗"),
    ("W2 Plus", "参数", "1 个"),
    ("W2 Plus", "材质", "100%"),
    ("W2 Plus", "保修", "120 天"),
    ("S10 Pro", "参数", "2500 帕"),
    ("S10 Pro", "参数", "8 厘米"),
    ("S10 Pro", "参数", "2.4GHz"),
    ("S10 Pro", "容量", "350 毫升"),
    ("S10 Pro", "容量", "240 毫升"),
    ("S10 Pro", "保修", "270 天"),
    ("S10 Pro", "常见故障", "0.5 米"),
    ("S10 Pro", "常见故障", "1.5 米"),
    ("S20 Max", "参数", "5000 帕"),
    ("S20 Max", "参数", "9.5 厘米"),
    ("S20 Max", "参数", "2.4GHz"),
    ("S20 Max", "参数", "5 张"),
    ("S20 Max", "容量", "550 毫升"),
    ("S20 Max", "容量", "350 毫升"),
    ("S20 Max", "保修", "540 天"),
    ("T3 Lite", "参数", "1 档"),
    ("T3 Lite", "参数", "2 分钟"),
    ("T3 Lite", "参数", "30 秒"),
    ("T3 Lite", "续航", "4 小时"),
    ("T3 Lite", "续航", "2 分钟"),
    ("T3 Lite", "续航", "25 天"),
    ("T3 Lite", "保修", "120 天"),
    ("T5 Pro", "参数", "5 档"),
    ("T5 Pro", "续航", "6 小时"),
    ("T5 Pro", "续航", "2 分钟"),
    ("T5 Pro", "续航", "70 天"),
    ("T5 Pro", "保修", "540 天"),
    ("T5 Pro", "常见故障", "5V 2A"),
    ("L1 Plus", "参数", "5 档"),
    ("L1 Plus", "参数", "3 种"),
    ("L1 Plus", "参数", "380 流明"),
    ("L1 Plus", "参数", "30 分钟"),
    ("L1 Plus", "材质", "32 厘米"),
    ("L1 Plus", "材质", "5V 2A"),
    ("L1 Plus", "保修", "270 天"),
    ("L1 Plus", "常见故障", "5 秒"),
    ("L2 Pro", "参数", "5 档"),
    ("L2 Pro", "参数", "4 种"),
    ("L2 Pro", "参数", "800 流明"),
    ("L2 Pro", "参数", "60 分钟"),
    ("L2 Pro", "材质", "48 厘米"),
    ("L2 Pro", "材质", "5V 3A"),
    ("L2 Pro", "材质", "5V 2A"),
    ("L2 Pro", "材质", "600 流明"),
    ("L2 Pro", "保修", "540 天"),
    ("L2 Pro", "常见故障", "5V 3A"),
    ("L2 Pro", "常见故障", "5V 2A"),
    ("L2 Pro", "常见故障", "600 流明"),
    ("C5 Pro", "参数", "20 厘米"),
    ("C5 Pro", "参数", "4 厘米"),
    ("C5 Pro", "参数", "95 度"),
    ("C5 Pro", "参数", "6 小时"),
    ("C5 Pro", "参数", "75 度"),
    ("C5 Pro", "参数", "12 小时"),
    ("C5 Pro", "参数", "60 度"),
    ("C5 Pro", "容量", "400 毫升"),
    ("C5 Pro", "容量", "316 不锈钢"),
    ("C5 Pro", "容量", "4 小时"),
    ("C5 Pro", "保修", "120 天"),
    ("C8 Plus", "参数", "28 厘米"),
    ("C8 Plus", "参数", "5 厘米"),
    ("C8 Plus", "参数", "6 小时"),
    ("C8 Plus", "参数", "70 度"),
    ("C8 Plus", "参数", "12 小时"),
    ("C8 Plus", "参数", "55 度"),
    ("C8 Plus", "容量", "800 毫升"),
    ("C8 Plus", "容量", "316 不锈钢"),
    ("C8 Plus", "保修", "450 天"),
    ("R1 Plus", "参数", "42 码"),
    ("R1 Plus", "参数", "250 克"),
    ("R1 Plus", "参数", "2.2 厘米"),
    ("R1 Plus", "保修", "120 天"),
    ("R2 Pro", "参数", "42 码"),
    ("R2 Pro", "参数", "320 克"),
    ("R2 Pro", "参数", "3.5 厘米"),
    ("R2 Pro", "保修", "540 天"),
    ("K1 Plus", "参数", "1.1 毫米"),
    ("K1 Plus", "材质", "1.8 毫米"),
    ("K1 Plus", "保修", "45 天"),
    ("K1 Plus", "退换规则", "15 天"),
    ("K2 Pro", "参数", "1.5 毫米"),
    ("K2 Pro", "参数", "90 度"),
    ("K2 Pro", "材质", "3 毫米"),
    ("K2 Pro", "保修", "180 天"),
])
def test_each_appendix_value_is_required_in_its_section(model, section, value):
    title = f"### {model} {section}\n\n"
    md = manual_with(full_new_sections())
    before, body = md.split(title, 1)
    section_body, separator, after = body.partition("\n### ")
    md = before + title + section_body.replace(value, "错误值") + separator + after
    errors = "\n".join(run(md))
    assert f"{model} {section}" in errors and value in errors


def test_parse_sections_stops_at_parent_heading():
    md = "# 手册\n### X3 Lite 参数\n\n蓝牙 5.2\n#### 说明\n3.8 克\n## 羊毛衫\n其他文字\n### W1 Pro 参数\n63 厘米\n"
    assert ck.parse_sections(md) == {
        "X3 Lite 参数": "\n蓝牙 5.2\n#### 说明\n3.8 克",
        "W1 Pro 参数": "63 厘米",
    }


def test_new_model_matching_ignores_case_spaces_and_hyphens():
    errors = "\n".join(run(manual_with(full_new_sections()), old_d=("S10-\tpro 的产地？",)))
    assert "S10 Pro" in errors


@pytest.mark.parametrize("word", [
    "防水", "产地", "噪声", "分贝", "转速", "显色", "抗菌", "碳板", "磁吸", "编解码",
    "毫安", "紫外", "杀菌", "无气味", "蓝光", "冻裂", "耐摔", "分期", "免息", "积分",
    "会员", "年费", "海外", "境外", "跨境", "关税", "礼品卡", "批量采购", "企业采购",
    "学生优惠", "宠物", "货到付款", "租赁", "订阅", "自动扣款", "延保", "备用机", "押金",
    "个人信息导出",
])
def test_banned_words_in_new_document(word):
    errors = "\n".join(run(manual_with(full_new_sections()), extra={
        "knowledge/docs/policy/新政策.md": f"# 政策\n支持{word}。\n",
    }))
    assert "新政策.md" in errors and word in errors


def test_existing_banned_word_is_allowed():
    path = "knowledge/docs/policy/已有政策.md"
    docs = {"knowledge/docs/manual/商品手册.md": manual_with(full_new_sections()), path: "不支持分期。\n"}
    baseline = {"knowledge/docs/manual/商品手册.md": BASE_MANUAL, path: "不支持分期。\n"}
    assert ck.check(docs, baseline, []) == []


def test_deleted_document_is_rejected():
    baseline = {
        "knowledge/docs/manual/商品手册.md": BASE_MANUAL,
        "knowledge/docs/policy/已有政策.md": "# 政策\n旧事实。\n",
    }
    errors = "\n".join(ck.check({"knowledge/docs/manual/商品手册.md": manual_with(full_new_sections())}, baseline, []))
    assert "已有政策.md" in errors and "修改或删除" in errors


def test_intro_change_preserves_the_rest_of_the_line():
    md = manual_with(full_new_sections()).replace("的参数。", "的错误事实。")
    assert any("修改或删除" in e for e in run(md))


def test_appended_intro_does_not_replace_the_old_intro():
    md = manual_with(full_new_sections(), intro=ck.INTRO_OLD) + f"\n{ck.INTRO_NEW}\n"
    assert any("40 个型号" in e for e in run(md))


def test_intro_exception_is_only_for_product_manual():
    path = "knowledge/docs/faq/旧问答.md"
    docs = {"knowledge/docs/manual/商品手册.md": manual_with(full_new_sections()), path: ck.INTRO_NEW}
    baseline = {"knowledge/docs/manual/商品手册.md": BASE_MANUAL, path: ck.INTRO_OLD}
    errors = "\n".join(ck.check(docs, baseline, []))
    assert "旧问答.md" in errors and "修改或删除" in errors


def test_load_baseline_reads_existing_and_missing_files():
    manual = "knowledge/docs/manual/商品手册.md"
    new_policy = "knowledge/docs/policy/配送与签收政策.md"
    baseline = ck.load_baseline([manual, new_policy])
    assert ck.INTRO_OLD in baseline[manual] and "### X3 参数" in baseline[manual]
    assert baseline[new_policy] == ""


def test_load_baseline_missing_commit_has_clear_error(monkeypatch):
    monkeypatch.setattr(ck, "BASELINE_COMMIT", "0" * 40)
    with pytest.raises(RuntimeError, match="找不到基线提交"):
        ck.load_baseline(["knowledge/docs/manual/商品手册.md"])


def test_load_baseline_show_failure_is_not_an_empty_file(monkeypatch):
    real_run = ck.subprocess.run

    def fail_show(args, **kwargs):
        if args[:2] == ["git", "show"]:
            raise subprocess.CalledProcessError(128, args, stderr="读取失败")
        return real_run(args, **kwargs)

    monkeypatch.setattr(ck.subprocess, "run", fail_show)
    with pytest.raises(RuntimeError, match="读取失败|读取基线"):
        ck.load_baseline(["knowledge/docs/manual/商品手册.md"])


def test_load_baseline_path_probe_failure_is_not_a_missing_file(monkeypatch):
    real_run = ck.subprocess.run

    def fail_probe(args, **kwargs):
        if args[:3] == ["git", "cat-file", "-e"] and ":" in args[-1]:
            raise subprocess.CalledProcessError(128, args, stderr="损坏的对象")
        return real_run(args, **kwargs)

    monkeypatch.setattr(ck.subprocess, "run", fail_probe)
    with pytest.raises(RuntimeError, match="读取基线"):
        ck.load_baseline(["knowledge/docs/manual/商品手册.md"])


@pytest.fixture
def kb_files(tmp_path, monkeypatch):
    manual = "knowledge/docs/manual/商品手册.md"
    path = tmp_path / manual
    path.parent.mkdir(parents=True)
    path.write_text(manual_with(full_new_sections()), encoding="utf-8")
    samples = tmp_path / "evals/rag_eval.jsonl"
    samples.parent.mkdir()
    samples.write_text(json.dumps({"bucket": "D_unanswerable", "query": "X7 的续航？"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(ck, "ROOT", tmp_path)
    monkeypatch.setattr(ck, "load_baseline", lambda paths: {manual: BASE_MANUAL})
    monkeypatch.setattr(ck.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, manual + "\0", ""))
    return path, samples


def test_main_passes(kb_files, capsys):
    assert ck.main() == 0
    assert capsys.readouterr().out.strip() == "知识库检查通过"


def test_main_checks_old_d_queries_only(kb_files, capsys):
    _, samples = kb_files
    samples.write_text("\n".join(json.dumps(row) for row in [
        {"bucket": "B_model", "query": "X5 Pro 的续航？"},
        {"bucket": "D_unanswerable", "query": "x3-lite 的产地？"},
    ]), encoding="utf-8")
    assert ck.main() == 1
    output = capsys.readouterr().out
    assert "X3 Lite" in output and "X5 Pro" not in output


def test_main_missing_baseline_returns_one(kb_files, monkeypatch, capsys):
    def fail(paths):
        raise RuntimeError("找不到基线提交 cd7a110")

    monkeypatch.setattr(ck, "load_baseline", fail)
    assert ck.main() == 1
    assert "找不到基线提交 cd7a110" in capsys.readouterr().out


def test_main_detects_deleted_document(kb_files, monkeypatch, capsys):
    manual = "knowledge/docs/manual/商品手册.md"
    policy = "knowledge/docs/policy/已有政策.md"

    def listing(args, **kwargs):
        paths = [manual, policy]
        output = "\0".join(paths) + "\0" if "-z" in args else "\n".join(json.dumps(p) for p in paths) + "\n"
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(ck.subprocess, "run", listing)
    monkeypatch.setattr(ck, "load_baseline", lambda paths: {
        manual: BASE_MANUAL,
        **({policy: "旧事实。\n"} if policy in paths else {}),
    })
    assert ck.main() == 1
    output = capsys.readouterr().out
    assert policy in output and "修改或删除" in output
