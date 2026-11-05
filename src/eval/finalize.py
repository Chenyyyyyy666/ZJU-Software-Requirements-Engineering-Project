"""Build final-report tables/figures from completed, comparable experiment runs.

This is an offline command. It accepts only run_final_eval's complete three-method
runs with the same question identities and labels. It never fills missing results
with estimates. Use --check-only to validate inputs without writing anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.common.json_io import digest, write_json
from src.eval.metrics import RELATION_LABELS, evaluate_rows

METHODS = ("full_text", "vector_rag", "ours")
METHOD_NAMES = {"full_text": "Full Text", "vector_rag": "Vector RAG", "ours": "本组方法"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(row: dict[str, Any]) -> tuple[str, str]:
    return row["conversation_id"], row["question_id"]


def load_failed_attempts(path: Path, config_id: str) -> dict[str, Any]:
    summary = {
        method: {"attempts": 0, "known_tokens": 0, "unknown_token_attempts": 0}
        for method in METHODS
    }
    if not path.exists():
        return summary
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("config_id") != config_id or row.get("method") not in METHODS:
            raise ValueError("failed-attempt log contains a different run configuration")
        method = row["method"]
        summary[method]["attempts"] += 1
        usage = row.get("token_usage")
        tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
        if (
            isinstance(tokens, int | float)
            and not isinstance(tokens, bool)
            and math.isfinite(tokens)
        ):
            summary[method]["known_tokens"] += tokens
        else:
            summary[method]["unknown_token_attempts"] += 1
            known = usage.get("known_total_tokens") if isinstance(usage, dict) else None
            if (
                isinstance(known, int | float)
                and not isinstance(known, bool)
                and math.isfinite(known)
            ):
                summary[method]["known_tokens"] += known
    return summary


def load_run(directory: Path, dataset: str) -> dict[str, Any]:
    manifest_path = directory / "manifest.json"
    manifest = load_json(manifest_path)
    if manifest.get("runner_version") != "final-eval-v1":
        raise ValueError(f"not a versioned final evaluation run: {directory}")
    if manifest.get("status") != "complete":
        raise ValueError(f"run is not complete: {directory}")
    if manifest.get("dataset") != dataset:
        raise ValueError(f"wrong split in run: expected {dataset}")
    if manifest.get("limit_per_conversation") is not None:
        raise ValueError("smoke tests with --limit cannot populate final result tables")
    if set(manifest.get("methods", [])) != set(METHODS):
        raise ValueError("final results require exactly ours, full_text and vector_rag")
    expected = sum(
        item["selected_questions"] for item in manifest["question_selection_counts"].values()
    )
    if expected <= 0:
        raise ValueError("completed run has no selected questions")
    metrics, per_conversation, evidence = {}, {}, {}
    reference_identities = None
    reference_labels = None
    for method in METHODS:
        path = directory / method / "answers.json"
        payload = load_json(path)
        if payload.get("config_id") != manifest.get("config_id"):
            raise ValueError(f"answer configuration mismatch: {method}")
        rows = payload.get("answers", [])
        if len(rows) != expected:
            raise ValueError(f"incomplete answer rows for {method}: {len(rows)}/{expected}")
        identities = [_identity(row) for row in rows]
        if len(set(identities)) != len(identities):
            raise ValueError(f"duplicate answer identities for {method}")
        if reference_identities is None:
            reference_identities = set(identities)
        elif set(identities) != reference_identities:
            raise ValueError("methods did not answer the same question identities")
        labels = {}
        for row in rows:
            if row.get("method") != method or row.get("config_id") != manifest["config_id"]:
                raise ValueError(f"row method/configuration mismatch: {method}")
            if row.get("status") in ("failed", "error") or not row.get("answer"):
                raise ValueError(f"unanswered/failed question in {method}")
            if row.get("question_sha256") != digest(row["question"]):
                raise ValueError(f"question content hash mismatch: {method}")
            if row.get("model") != manifest["model"]:
                raise ValueError(f"generation model mismatch: {method}")
            if row.get("prompt_versions") != manifest["prompts"]:
                raise ValueError(f"prompt version mismatch: {method}")
            conversation = row["conversation_id"]
            if conversation not in manifest["conversations"]:
                raise ValueError("unexpected conversation in answer file")
            labels[_identity(row)] = digest(
                [row["question"], row.get("gold_answer"), row.get("gold_evidence")]
            )
        if reference_labels is None:
            reference_labels = labels
        elif labels != reference_labels:
            raise ValueError("methods have different questions or scoring labels")
        metrics[method] = evaluate_rows(rows)
        per_conversation[method] = {}
        for conversation in manifest["conversations"]:
            subset = [row for row in rows if row["conversation_id"] == conversation]
            selected = manifest["question_selection_counts"][conversation]["selected_questions"]
            if len(subset) != selected:
                raise ValueError(f"per-conversation row count mismatch: {method}/{conversation}")
            per_conversation[method][conversation] = evaluate_rows(subset)
        evidence[method] = {"path": str(path), "sha256": sha256(path)}
    return {
        "manifest": manifest,
        "manifest_sha256": sha256(manifest_path),
        "metrics": metrics,
        "per_conversation": per_conversation,
        "evidence": evidence,
        "failed_attempts": load_failed_attempts(
            directory / "attempts.jsonl", manifest["config_id"]
        ),
    }


def validate_split_comparability(dev: dict[str, Any], test: dict[str, Any]) -> None:
    fields = (
        "base_url",
        "model",
        "prompts",
        "code_sha256",
        "data_sha256",
        "splits_sha256",
        "top_k",
        "candidate_k",
        "ours_mode",
        "question_policy",
        "generation_temperature",
        "thinking",
    )
    different = [
        field for field in fields if dev["manifest"].get(field) != test["manifest"].get(field)
    ]
    if different:
        raise ValueError("dev/test frozen configurations differ: " + ", ".join(different))
    if set(dev["manifest"]["conversations"]) & set(test["manifest"]["conversations"]):
        raise ValueError("dev and test conversation sets overlap")


def load_relations(directory: Path) -> dict[str, Any]:
    metrics = load_json(directory / "metrics.json")
    manifest = load_json(directory / "manifest.json")
    if not manifest.get("completed_at") or manifest.get("summary") != metrics:
        raise ValueError("relation evaluation is incomplete or its summary is inconsistent")
    if metrics.get("total_pairs", 0) <= 0:
        raise ValueError("relation evaluation contains no human-labelled pairs")
    matrix = metrics["confusion_matrix"]
    successful = sum(matrix[gold][pred] for gold in RELATION_LABELS for pred in RELATION_LABELS)
    failed = sum(metrics["failed_by_class"][label] for label in RELATION_LABELS)
    correct = sum(matrix[label][label] for label in RELATION_LABELS)
    if successful + failed != metrics["total_pairs"]:
        raise ValueError("relation matrix/sample counts are inconsistent")
    if not math.isclose(correct / metrics["total_pairs"], metrics["accuracy"]):
        raise ValueError("relation accuracy does not match the confusion matrix")
    for label in RELATION_LABELS:
        support = sum(matrix[label].values()) + metrics["failed_by_class"][label]
        if metrics["per_class"][label]["support"] != support:
            raise ValueError("relation per-class counts are inconsistent")
    return {
        "metrics": metrics,
        "manifest": manifest,
        "metrics_sha256": sha256(directory / "metrics.json"),
        "predictions_sha256": sha256(directory / "predictions.json"),
    }


def _value(value: Any, digits: int = 4) -> str:
    return "未记录" if value is None else f"{value:.{digits}f}"


def render_main_table(run: dict[str, Any], *, title: str) -> str:
    lines = [
        title,
        "",
        "| 方法 | QA F1 | Recall@1 | Recall@5 | 状态更新准确率 | 平均 token | 平均耗时（秒） |",
        "| --- | ---: | ---: | ---: | --- | ---: | ---: |",
    ]
    for method in METHODS:
        metrics = run["metrics"][method]
        r1 = "不适用" if method == "full_text" else _value(metrics["recall@1"])
        r5 = "不适用" if method == "full_text" else _value(metrics["recall@5"])
        state = "见独立关系评测" if method == "ours" else "不适用"
        lines.append(
            f"| {METHOD_NAMES[method]} | {_value(metrics['qa_f1'])} | {r1} | {r5} | "
            f"{state} | {_value(metrics['average_tokens'], 1)} | "
            f"{_value(metrics.get('average_elapsed_seconds'), 3)} |"
        )
    manifest = run["manifest"]
    lines.extend(
        [
            "",
            f"配置编号：`{manifest['config_id']}`；模型：`{manifest['model']}`；"
            f"数据划分：{', '.join(manifest['conversations'])}；"
            f"检索参数：Top-K={manifest['top_k']}，candidate-K={manifest['candidate_k']}。",
            "",
            "三个方法均为本仓库实现；QA F1 使用项目评分器，尚不代表课程官方评分。"
            "状态准确率以独立人工记忆对评测报告，不能从 QA 题数推算。",
            "",
            (
                "| 方法 | 运行题数 | QA F1 分母 | Recall 分母 | token 有记录题数 | "
                "耗时有记录题数 | 中位耗时（秒） |"
            ),
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for method in METHODS:
        m = run["metrics"][method]
        evidence_n = "不适用" if method == "full_text" else m["evidence_rows"]
        lines.append(
            f"| {METHOD_NAMES[method]} | {m['rows']} | {m.get('qa_rows', '未记录')} | "
            f"{evidence_n} | {m.get('token_rows', 0)} | {m.get('elapsed_rows', 0)} | "
            f"{_value(m.get('median_elapsed_seconds'), 3)} |"
        )
    lines.extend(
        [
            "",
            "平均 token/耗时按成功完成且有记录的问题统计；失败尝试单列，不作为完整费用账单。",
            "",
            "| 方法 | 记录的失败尝试数 | 失败尝试已知 token | token 未知的失败尝试数 |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for method in METHODS:
        failure = run["failed_attempts"][method]
        lines.append(
            f"| {METHOD_NAMES[method]} | {failure['attempts']} | {failure['known_tokens']} | "
            f"{failure['unknown_token_attempts']} |"
        )
    return "\n".join(lines)


def render_conversations(run: dict[str, Any]) -> str:
    lines = [
        "**表 4 开发集分对话结果。** 下表为本组方法；原始结果另保存三方法各对话指标。",
        "",
        "| 对话 | 问题数 | QA F1 | Recall@5 | 状态准确率 | 主要失败类型 |",
        "| --- | ---: | ---: | ---: | --- | --- |",
    ]
    for conversation in run["manifest"]["conversations"]:
        m = run["per_conversation"]["ours"][conversation]
        lines.append(
            f"| `{conversation}` | {m['rows']} | {_value(m['qa_f1'])} | "
            f"{_value(m['recall@5'])} | 未作逐对话人工关系标注 | 待依据错误样本人工归因 |"
        )
    lines.extend(
        [
            "",
            "问题数依照该次运行的样本选择策略；QA/Recall 分母见主结果说明。"
            "失败归因需要核对原文，脚本不根据低分自动判断原因。",
        ]
    )
    return "\n".join(lines)


def render_relations(relation: dict[str, Any]) -> str:
    m = relation["metrics"]
    lines = [
        f"人工关系样本共 **{m['total_pairs']}** 对；成功判定 {m['successfully_evaluated']} 对，"
        f"失败或缺失 {m['failed_or_missing']} 对。Accuracy={m['accuracy']:.4f}，"
        f"Macro-F1={m['macro_f1']:.4f}。Accuracy 的分母包含失败与缺失。",
        "",
        "**表 5 状态关系混淆矩阵。** 行为人工标签，列为系统预测；失败另列。",
        "",
        "| 真实类别 \\ 预测类别 | RETAIN | UPDATE | SUPERSEDE | CONFLICT | 失败/缺失 | 样本量 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label in RELATION_LABELS:
        counts = " | ".join(str(m["confusion_matrix"][label][pred]) for pred in RELATION_LABELS)
        lines.append(
            f"| {label} | {counts} | {m['failed_by_class'][label]} | "
            f"{m['per_class'][label]['support']} |"
        )
    config = relation["manifest"]["config"]
    lines.extend(
        [
            "",
            f"模型：`{config['model']}`；prompt：`{config['prompt_version']}`；"
            f"gold SHA256：`{config['gold_sha256']}`。",
            "样本来自有目标的记忆对抽样，不能推断全部记忆对的总体准确率。"
            "主要混淆方向和具体原文案例仍由负责人复核并解释。",
        ]
    )
    return "\n".join(lines)


def _plot_modules() -> tuple[Any, Any]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "Report plotting dependencies missing; run python -m pip install "
            "-r requirements-report.txt"
        ) from exc
    return plt, np


def create_charts(snapshot: dict[str, Any], directory: Path) -> dict[str, str]:
    if not snapshot.get("dev") and not snapshot.get("relations"):
        return {}
    plt, np = _plot_modules()
    directory.mkdir(parents=True, exist_ok=True)
    images = {}
    if snapshot.get("dev"):
        run = snapshot["dev"]
        figure, axis = plt.subplots(figsize=(9, 4.5), layout="constrained")
        x = np.arange(3)
        width = 0.25
        for index, (key, label) in enumerate(
            [("qa_f1", "QA F1"), ("recall@1", "Recall@1"), ("recall@5", "Recall@5")]
        ):
            for position, method in enumerate(METHODS):
                value = run["metrics"][method].get(key)
                if method == "full_text" and key.startswith("recall"):
                    continue
                if value is not None:
                    axis.bar(
                        position + (index - 1) * width,
                        value,
                        width,
                        color=["#355f8d", "#238a8d", "#73b969"][index],
                        label=label if position == (0 if key == "qa_f1" else 1) else None,
                    )
                    axis.text(
                        position + (index - 1) * width,
                        value + 0.012,
                        f"{value:.3f}",
                        ha="center",
                        fontsize=9,
                    )
        axis.set_xticks(x, ["Full Text", "Vector RAG", "Ours"])
        axis.set_ylim(0, 1.1)
        axis.set_ylabel("Score")
        axis.set_title("Development set: project scorer and local baselines")
        axis.legend(ncols=3, loc="upper center")
        axis.grid(axis="y", alpha=0.2)
        axis.set_axisbelow(True)
        figure.text(0.02, 0.01, "Full Text retrieval recall: not applicable", fontsize=8)
        for suffix in ("png", "svg"):
            path = directory / f"fig-04-dev-comparison.{suffix}"
            figure.savefig(path, dpi=180)
            images[f"dev_{suffix}"] = str(path)
        plt.close(figure)
    if snapshot.get("relations"):
        relation = snapshot["relations"]
        metrics = relation["metrics"]
        matrix = np.array(
            [
                [metrics["confusion_matrix"][gold][pred] for pred in RELATION_LABELS]
                for gold in RELATION_LABELS
            ]
        )
        figure, axis = plt.subplots(figsize=(7.5, 5.5), layout="constrained")
        image = axis.imshow(matrix, cmap="Blues", vmin=0)
        figure.colorbar(image, ax=axis, label="Pairs")
        axis.set_xticks(range(4), RELATION_LABELS, rotation=20)
        axis.set_yticks(
            range(4),
            [f"{label} (n={metrics['per_class'][label]['support']})" for label in RELATION_LABELS],
        )
        for i in range(4):
            for j in range(4):
                axis.text(
                    j,
                    i,
                    str(matrix[i, j]),
                    ha="center",
                    va="center",
                    color="white" if matrix[i, j] > matrix.max() / 2 else "black",
                )
        axis.set_xlabel("Predicted label")
        axis.set_ylabel("Human gold label")
        axis.set_title(f"State relations ({metrics['total_pairs']} labelled pairs)")
        figure.text(
            0.02,
            0.01,
            f"Failed/missing predictions outside matrix: {metrics['failed_or_missing']}",
            fontsize=8,
        )
        for suffix in ("png", "svg"):
            path = directory / f"fig-05-confusion-matrix.{suffix}"
            figure.savefig(path, dpi=180)
            images[f"relations_{suffix}"] = str(path)
        plt.close(figure)
    return images


def update_block(text: str, name: str, content: str) -> str:
    begin, end = f"<!-- BEGIN AUTO:{name} -->", f"<!-- END AUTO:{name} -->"
    if text.count(begin) != 1 or text.count(end) != 1:
        raise ValueError(f"report requires exactly one AUTO block for {name}")
    pattern = re.escape(begin) + r".*?" + re.escape(end)
    updated, count = re.subn(pattern, lambda _: f"{begin}\n{content}\n{end}", text, flags=re.S)
    if count != 1:
        raise ValueError(f"AUTO block markers appear in the wrong order: {name}")
    return updated


def report_blocks(snapshot: dict[str, Any]) -> dict[str, str]:
    blocks = {}
    findings = []
    for split, title, block in [
        ("dev", "**表 3 开发集主结果。**", "table-dev"),
        ("test", "**表 6 独立测试集主结果。**", "table-test"),
    ]:
        if snapshot.get(split):
            run = snapshot[split]
            blocks[block] = render_main_table(run, title=title)
            values = run["metrics"]
            findings.append(
                f"{split} 同题比较：Full Text QA F1={_value(values['full_text']['qa_f1'])}，"
                f"Vector RAG={_value(values['vector_rag']['qa_f1'])}，"
                f"本组方法={_value(values['ours']['qa_f1'])}。"
                "上述是实测分数，原因分析需结合检索和状态错误记录。"
            )
    if snapshot.get("dev"):
        blocks["table-dev-conversations"] = render_conversations(snapshot["dev"])
        blocks["table-dev"] += (
            "\n\n![图 4 三方法开发集对比](figures/fig-04-dev-comparison.png)\n\n"
            "*图 4：来源为本次完整开发集逐题结果；样本量、配置编号和分母见表 3。"
            f"生成时间：{snapshot['generated_at_utc']}。*"
        )
    if snapshot.get("relations"):
        blocks["relation-results"] = render_relations(snapshot["relations"])
        blocks["relation-results"] += (
            "\n\n![图 5 状态关系混淆矩阵](figures/fig-05-confusion-matrix.png)\n\n"
            "*图 5：来源为人工 gold 与逐对预测，样本量和配置见表 5；"
            f"生成时间：{snapshot['generated_at_utc']}。*"
        )
        m = snapshot["relations"]["metrics"]
        findings.append(
            f"人工关系评测 {m['total_pairs']} 对，Accuracy={m['accuracy']:.4f}，"
            f"Macro-F1={m['macro_f1']:.4f}，失败/缺失 {m['failed_or_missing']} 对。"
        )
    if findings:
        blocks["main-findings"] = "\n\n".join(findings)
    if all(snapshot.get(name) for name in ("dev", "test", "relations")):
        blocks["abstract"], blocks["conclusion"] = render_final_prose(snapshot)
    return blocks


def render_final_prose(snapshot: dict[str, Any]) -> tuple[str, str]:
    """State observed measurements without claiming acceptance or causal gains."""
    dev, test = snapshot["dev"]["metrics"], snapshot["test"]["metrics"]
    relation = snapshot["relations"]["metrics"]
    dev_scores = "、".join(_value(dev[method]["qa_f1"]) for method in METHODS)
    test_scores = "、".join(_value(test[method]["qa_f1"]) for method in METHODS)
    dev_token = _value(dev["ours"]["average_tokens"], 1)
    test_token = _value(test["ours"]["average_tokens"], 1)
    abstract = (
        "长程多会话对话中的历史事实可能被后续计划、偏好或状态更新。"
        "本项目实现结构化 Memory、混合检索、显式状态关系判定、有效记忆选择和答案生成，"
        "并通过本地 Web 与评测接口保存答案及来源。"
        f"开发集共运行 {dev['ours']['rows']} 题，其中 {dev['ours']['qa_rows']} 题有有效参考答案；"
        f"Full Text、Vector RAG 与本组方法的项目 token F1 分别为 {dev_scores}。"
        f"冻结后的独立测试共运行 {test['ours']['rows']} 题，三方法 F1 分别为 {test_scores}。"
        f"本组方法在用量记录完整的成功题上平均在线 token 为开发集 {dev_token}、"
        f"测试集 {test_token}，分别有 {dev['ours'].get('token_rows', 0)} 与 "
        f"{test['ours'].get('token_rows', 0)} 题完整记录用量；"
        "失败尝试和未知用量另行披露。"
        f"人工关系评测 {relation['total_pairs']} 对，Accuracy={relation['accuracy']:.4f}，"
        f"Macro-F1={relation['macro_f1']:.4f}。"
        "这些结果来自项目评分器与本地基线，尚不代表课程官方分数或完整验收；"
        "定向记忆对样本、远端模型版本和当前实现范围限制了结论的适用性。"
    )
    conclusion = (
        "项目已实现可追溯的记忆构建、检索、关系判定、有效记忆选择、答案生成及 Web/CLI 接口。"
        f"在同题比较中，开发集 Full Text、Vector RAG、本组方法的项目 token F1 为 {dev_scores}；"
        f"独立测试对应结果为 {test_scores}。"
        f"开发集与测试集本组方法的 Recall@5 分别为 {_value(dev['ours']['recall@5'])}、"
        f"{_value(test['ours']['recall@5'])}，用量完整的成功题平均在线 token 为 "
        f"{dev_token}、{test_token}。"
        "用量缺失与失败调用的成本单独列出，不能据成功题均值推断完整账单。\n\n"
        f"{relation['total_pairs']} 对人工关系样本得到 Accuracy={relation['accuracy']:.4f}、"
        f"Macro-F1={relation['macro_f1']:.4f}，失败或缺失 {relation['failed_or_missing']} 对。"
        "三方法同时改变输入组织、检索与状态处理，因此分数差异不能单独归因于状态解析；"
        "若要判断该模块的独立收益，需要相同检索配置下关闭关系解析的消融实验。"
        "当前采用项目指标和自实现基线，关系样本也属于定向抽样；课程官方评分、"
        "浏览器完整验收、跨平台阈值和最终交付证据仍需核对。"
    )
    return abstract, conclusion


def update_completion_status(text: str, snapshot: dict[str, Any]) -> str:
    if not all(snapshot.get(name) for name in ("dev", "test", "relations")):
        return text
    text = text.replace("> 版本：v1.0-result-pending", "> 版本：v1.0-result-summary")
    return re.sub(
        r"^> 当前状态：.*$",
        "> 当前状态：实验已汇总，验收待核对；指标来自项目评分器与自实现基线。",
        text,
        count=1,
        flags=re.M,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev-dir", type=Path)
    parser.add_argument("--test-dir", type=Path)
    parser.add_argument("--relations-dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--figures", type=Path, default=Path("docs/figures"))
    parser.add_argument("--output-dir", type=Path, default=Path("experiments/final-report-results"))
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if not any([args.dev_dir, args.test_dir, args.relations_dir]):
        parser.error("provide at least one completed result directory")
    snapshot = {"generated_at_utc": datetime.now(UTC).isoformat()}
    if args.dev_dir:
        snapshot["dev"] = load_run(args.dev_dir, "dev")
    if args.test_dir:
        snapshot["test"] = load_run(args.test_dir, "test")
    if args.dev_dir and args.test_dir:
        validate_split_comparability(snapshot["dev"], snapshot["test"])
    if args.relations_dir:
        snapshot["relations"] = load_relations(args.relations_dir)
    blocks = report_blocks(snapshot)
    report_text = args.report.read_text(encoding="utf-8") if args.report else None
    if report_text is not None:
        for name, block in blocks.items():
            report_text = update_block(report_text, name, block)
        report_text = update_completion_status(report_text, snapshot)
    if args.check_only:
        print("Validated complete experiment inputs and report anchors; no files written.")
        return
    snapshot["figures"] = create_charts(snapshot, args.figures)
    write_json(snapshot, args.output_dir / "report-snapshot.json")
    (args.output_dir / "report-fragments.md").write_text(
        "\n\n".join(f"<!-- {name} -->\n{block}" for name, block in blocks.items()) + "\n",
        encoding="utf-8",
    )
    if report_text is not None:
        args.report.write_text(report_text, encoding="utf-8")
    print(f"Generated report artifacts in {args.output_dir}")


if __name__ == "__main__":
    main()
