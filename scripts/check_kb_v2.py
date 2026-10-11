"""离线检查 v2 知识库新增小节、数值和旧内容。"""

import difflib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
BASELINE_COMMIT = "cd7a110"
MANUAL_PATH = "knowledge/docs/manual/商品手册.md"
INTRO_OLD = "本手册覆盖 8 个品类、24 个型号"
INTRO_NEW = "本手册覆盖 8 个品类、40 个型号"
NEW_MODELS: dict[str, str] = {
    "X3 Lite": "蓝牙耳机", "X5 Pro": "蓝牙耳机",
    "W1 Pro": "羊毛衫", "W2 Plus": "羊毛衫",
    "S10 Pro": "扫地机器人", "S20 Max": "扫地机器人",
    "T3 Lite": "电动牙刷", "T5 Pro": "电动牙刷",
    "L1 Plus": "台灯", "L2 Pro": "台灯",
    "C5 Pro": "保温杯", "C8 Plus": "保温杯",
    "R1 Plus": "运动鞋", "R2 Pro": "运动鞋",
    "K1 Plus": "手机壳", "K2 Pro": "手机壳",
}
SECTION_NAMES: dict[str, tuple[str, ...]] = {
    "蓝牙耳机": ("参数", "续航", "保修", "退换规则", "常见故障"),
    "羊毛衫": ("参数", "材质", "保修", "退换规则", "常见故障"),
    "扫地机器人": ("参数", "容量", "保修", "退换规则", "常见故障"),
    "电动牙刷": ("参数", "续航", "保修", "退换规则", "常见故障"),
    "台灯": ("参数", "材质", "保修", "退换规则", "常见故障"),
    "保温杯": ("参数", "容量", "保修", "退换规则", "常见故障"),
    "运动鞋": ("参数", "材质", "保修", "退换规则", "常见故障"),
    "手机壳": ("参数", "材质", "保修", "退换规则", "常见故障"),
}
FACTS: dict[tuple[str, str], tuple[str, ...]] = {
    ("X3 Lite", "参数"): ("蓝牙 5.2", "3.8 克", "3 秒"),
    ("X3 Lite", "续航"): ("5 小时", "20 小时", "1.5 小时", "50%"),
    ("X3 Lite", "保修"): ("90 天",),
    ("X3 Lite", "退换规则"): ("7 天", "15 天", "30 天"),
    ("X3 Lite", "常见故障"): ("5V 1A", "30 分钟"),
    ("X5 Pro", "参数"): ("蓝牙 5.4", "5.5 克"),
    ("X5 Pro", "续航"): ("50%", "12 小时", "48 小时", "8 小时", "2.5 小时"),
    ("X5 Pro", "保修"): ("540 天",),
    ("X5 Pro", "退换规则"): ("7 天", "15 天", "30 天"),
    ("X5 Pro", "常见故障"): (),
    ("W1 Pro", "参数"): ("63 厘米", "98 厘米"),
    ("W1 Pro", "材质"): ("85%", "15%"),
    ("W1 Pro", "保修"): ("45 天",),
    ("W1 Pro", "退换规则"): (),
    ("W1 Pro", "常见故障"): ("2 厘米",),
    ("W2 Plus", "参数"): ("68 厘米", "106 厘米", "6 颗", "1 个"),
    ("W2 Plus", "材质"): ("100%",),
    ("W2 Plus", "保修"): ("120 天",),
    ("W2 Plus", "退换规则"): (),
    ("W2 Plus", "常见故障"): (),
    ("S10 Pro", "参数"): ("2500 帕", "8 厘米", "2.4GHz Wi-Fi"),
    ("S10 Pro", "容量"): ("350 毫升", "240 毫升"),
    ("S10 Pro", "保修"): ("270 天",),
    ("S10 Pro", "退换规则"): (),
    ("S10 Pro", "常见故障"): ("0.5 米", "1.5 米"),
    ("S20 Max", "参数"): ("5000 帕", "9.5 厘米", "2.4GHz Wi-Fi", "5 张"),
    ("S20 Max", "容量"): ("550 毫升", "350 毫升"),
    ("S20 Max", "保修"): ("540 天",),
    ("S20 Max", "退换规则"): (),
    ("S20 Max", "常见故障"): (),
    ("T3 Lite", "参数"): ("1 档", "2 分钟", "30 秒"),
    ("T3 Lite", "续航"): ("4 小时", "2 次", "2 分钟", "25 天"),
    ("T3 Lite", "保修"): ("120 天",),
    ("T3 Lite", "退换规则"): (),
    ("T3 Lite", "常见故障"): (),
    ("T5 Pro", "参数"): ("5 档",),
    ("T5 Pro", "续航"): ("6 小时", "2 次", "2 分钟", "70 天"),
    ("T5 Pro", "保修"): ("540 天",),
    ("T5 Pro", "退换规则"): (),
    ("T5 Pro", "常见故障"): ("5V 2A",),
    ("L1 Plus", "参数"): ("5 档", "3 种", "380 流明", "30 分钟"),
    ("L1 Plus", "材质"): ("32 厘米", "5V 2A"),
    ("L1 Plus", "保修"): ("270 天",),
    ("L1 Plus", "退换规则"): (),
    ("L1 Plus", "常见故障"): ("5 秒",),
    ("L2 Pro", "参数"): ("5 档", "4 种", "800 流明", "60 分钟"),
    ("L2 Pro", "材质"): ("48 厘米", "5V 3A", "5V 2A", "600 流明"),
    ("L2 Pro", "保修"): ("540 天",),
    ("L2 Pro", "退换规则"): (),
    ("L2 Pro", "常见故障"): ("5V 3A", "5V 2A", "600 流明"),
    ("C5 Pro", "参数"): ("20 厘米", "4 厘米", "95 度", "6 小时", "75 度", "12 小时", "60 度"),
    ("C5 Pro", "容量"): ("400 毫升", "316 不锈钢", "4 小时"),
    ("C5 Pro", "保修"): ("120 天",),
    ("C5 Pro", "退换规则"): (),
    ("C5 Pro", "常见故障"): (),
    ("C8 Plus", "参数"): ("28 厘米", "5 厘米", "6 小时", "70 度", "12 小时", "55 度"),
    ("C8 Plus", "容量"): ("800 毫升", "316 不锈钢"),
    ("C8 Plus", "保修"): ("450 天",),
    ("C8 Plus", "退换规则"): (),
    ("C8 Plus", "常见故障"): (),
    ("R1 Plus", "参数"): ("42 码", "250 克", "2.2 厘米"),
    ("R1 Plus", "材质"): (),
    ("R1 Plus", "保修"): ("120 天",),
    ("R1 Plus", "退换规则"): (),
    ("R1 Plus", "常见故障"): (),
    ("R2 Pro", "参数"): ("42 码", "320 克", "3.5 厘米"),
    ("R2 Pro", "材质"): (),
    ("R2 Pro", "保修"): ("540 天",),
    ("R2 Pro", "退换规则"): (),
    ("R2 Pro", "常见故障"): (),
    ("K1 Plus", "参数"): ("1.1 毫米",),
    ("K1 Plus", "材质"): ("1.8 毫米",),
    ("K1 Plus", "保修"): ("45 天",),
    ("K1 Plus", "退换规则"): ("15 天",),
    ("K1 Plus", "常见故障"): (),
    ("K2 Pro", "参数"): ("1.5 毫米", "90 度"),
    ("K2 Pro", "材质"): ("3 毫米",),
    ("K2 Pro", "保修"): ("180 天",),
    ("K2 Pro", "退换规则"): (),
    ("K2 Pro", "常见故障"): (),
}
BANNED_WORDS: tuple[str, ...] = (
    "防水", "产地", "噪声", "分贝", "转速", "显色", "抗菌", "碳板", "磁吸", "编解码",
    "毫安", "紫外", "杀菌", "无气味", "蓝光", "冻裂", "耐摔", "分期", "免息", "积分",
    "会员", "年费", "海外", "境外", "跨境", "关税", "礼品卡", "批量采购", "企业采购",
    "学生优惠", "宠物", "货到付款", "租赁", "订阅", "自动扣款", "延保", "备用机", "押金",
    "个人信息导出",
)


def parse_sections(markdown: str) -> dict[str, str]:
    sections = {}
    title = None
    body = []
    for line in markdown.splitlines():
        heading = re.fullmatch(r"(#{1,3}) (.+)", line)
        if heading:
            if title is not None:
                sections[title] = "\n".join(body)
            title = heading[2] if heading[1] == "###" else None
            body = []
        elif title is not None:
            body.append(line)
    if title is not None:
        sections[title] = "\n".join(body)
    return sections


def check(docs: dict[str, str], baseline: dict[str, str], old_d_queries: list[str]) -> list[str]:
    errors = []
    manual = docs.get(MANUAL_PATH, "")
    sections = parse_sections(manual)
    if INTRO_NEW not in manual or INTRO_OLD in manual:
        errors.append("商品手册：开头应为 40 个型号")
    queries = [re.sub(r"[\s\-]", "", q).lower() for q in old_d_queries]
    for model, category in NEW_MODELS.items():
        if any(re.sub(r"[\s\-]", "", model).lower() in q for q in queries):
            errors.append(f"{model}：新型号名出现在旧 D 题中")
        for name in SECTION_NAMES[category]:
            title = f"{model} {name}"
            if title not in sections:
                errors.append(f"商品手册：缺少小节 {title}")
                continue
            errors.extend(f"{title}：缺少数值 {value}"
                          for value in FACTS[(model, name)] if value not in sections[title])
    for path in sorted(docs.keys() | baseline.keys()):
        old_lines = baseline.get(path, "").splitlines()
        new_lines = docs.get(path, "").splitlines()
        if path == MANUAL_PATH:
            new_lines = [line.replace(INTRO_NEW, INTRO_OLD) for line in new_lines]
        for line in difflib.ndiff(old_lines, new_lines):
            if line.startswith("- "):
                errors.append(f"{path}：修改或删除了已有行 {line[2:]}")
            elif line.startswith("+ "):
                errors.extend(f"{path}：新增行包含禁用词 {word}：{line[2:]}"
                              for word in BANNED_WORDS if word in line[2:])
    return errors


def load_baseline(paths: list[str]) -> dict[str, str]:
    try:
        subprocess.run(["git", "cat-file", "-e", f"{BASELINE_COMMIT}^{{commit}}"],
                       check=True, capture_output=True, text=True, cwd=ROOT)
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        raise RuntimeError(f"找不到基线提交 {BASELINE_COMMIT}") from exc
    baseline = {}
    for path in paths:
        try:
            try:
                subprocess.run(["git", "cat-file", "-e", f"{BASELINE_COMMIT}:{path}"],
                               check=True, capture_output=True, text=True, cwd=ROOT)
            except subprocess.CalledProcessError:
                listing = subprocess.run(["git", "ls-tree", "-r", "--name-only", BASELINE_COMMIT, "--", path],
                                         check=True, capture_output=True, text=True, cwd=ROOT)
                if listing.stdout:
                    raise
                baseline[path] = ""
                continue
            result = subprocess.run(["git", "show", f"{BASELINE_COMMIT}:{path}"],
                                    check=True, capture_output=True, text=True, cwd=ROOT)
            baseline[path] = result.stdout
        except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
            raise RuntimeError(f"读取基线文件失败：{path}") from exc
    return baseline


def main() -> int:
    try:
        docs = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8")
                for p in sorted((ROOT / "knowledge/docs").rglob("*.md"))}
        try:
            listing = subprocess.run(["git", "ls-tree", "-r", "-z", "--name-only", BASELINE_COMMIT, "--", "knowledge/docs"],
                                     check=True, capture_output=True, text=True, cwd=ROOT)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"找不到基线提交 {BASELINE_COMMIT}") from exc
        old_paths = [p for p in listing.stdout.split("\0") if p.endswith(".md")]
        baseline = load_baseline(sorted(docs.keys() | set(old_paths)))
        samples = [json.loads(line) for line in (ROOT / "evals/rag_eval.jsonl").read_text(encoding="utf-8").splitlines()
                   if line.strip()]
        errors = check(docs, baseline, [s["query"] for s in samples if s["bucket"] == "D_unanswerable"])
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        print(exc)
        return 1
    if errors:
        print("\n".join(errors))
        return 1
    print("知识库检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
