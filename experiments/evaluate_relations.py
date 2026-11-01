
# experiments/evaluate_relations.py

import sys
import csv
import json
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")
from src.common.models import Memory
from src.reasoning.relation_judge import judge_pair


# =========================
# 路径配置
# =========================

EXPERIMENT_DIR = Path(__file__).resolve().parent

# 人工关系标注表
SAMPLE_PATH = EXPERIMENT_DIR / "memory_pairs_gold.csv"

# 自动生成的记忆文件
MEMORY_DIR = PROJECT_ROOT / "outputs" / "memory"

# 评估结果
RESULT_OUT_PATH = EXPERIMENT_DIR / "relation_eval_results.csv"

# 错误记录
ERROR_OUT_PATH = EXPERIMENT_DIR / "relation-errors.csv"

# 四种关系类型
LABELS = ["RETAIN", "UPDATE", "SUPERSEDE", "CONFLICT"]


# =========================
# 读取人工标注 CSV
# =========================

def load_samples() -> list[dict]:
    if not SAMPLE_PATH.exists():
        raise FileNotFoundError(
            f"找不到人工标注文件：{SAMPLE_PATH}"
        )

    with open(
        SAMPLE_PATH,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as f:
        reader = csv.DictReader(f)
        samples = list(reader)

        required_fields = {
            "pair_id",
            "conversation_id",
            "memory_a_id",
            "memory_b_id",
            "gold_relation",
            "evidence",
        }

        actual_fields = set(reader.fieldnames or [])
        missing = required_fields - actual_fields

        if missing:
            raise ValueError(
                f"CSV 缺少必要字段：{missing}"
            )

    if not samples:
        raise ValueError("人工标注 CSV 没有数据")

    return samples


# =========================
# 加载记忆文件
# =========================

def load_memory_bundle(conversation_id: str) -> dict:
    """
    加载一个 conversation 对应的记忆文件。
    返回 memory_id -> memory_data 的字典。
    """

    bundle_path = MEMORY_DIR / f"{conversation_id}.json"

    if not bundle_path.exists():
        raise FileNotFoundError(
            f"找不到记忆文件：{bundle_path}"
        )

    with open(bundle_path, "r", encoding="utf-8") as f:
        bundle = json.load(f)

    memories = bundle.get("memories", [])

    return {
        memory["memory_id"]: memory
        for memory in memories
    }


# =========================
# 构建 Memory 对象
# =========================

def build_memory(memory_data: dict) -> Memory:
    """
    使用原始记忆数据构建 Memory 对象。
    保留原始 entity、attribute、时间和 sources 等字段。
    """

    return Memory.model_validate(memory_data)


# =========================
# 标签规范化
# =========================

def normalize_label(label) -> str:
    if hasattr(label, "value"):
        label = label.value

    return str(label).strip().upper()


# =========================
# Macro-F1 计算
# =========================

def calculate_macro_f1(cm: dict) -> float:
    f1_scores = []

    for label in LABELS:
        tp = cm[label][label]

        fp = sum(
            cm[gold][label]
            for gold in LABELS
            if gold != label
        )

        fn = sum(
            cm[label][pred]
            for pred in LABELS
            if pred != label
        )

        precision = tp / (tp + fp) if tp + fp > 0 else 0.0
        recall = tp / (tp + fn) if tp + fn > 0 else 0.0

        if precision + recall == 0:
            f1 = 0.0
        else:
            f1 = 2 * precision * recall / (precision + recall)

        f1_scores.append(f1)

    return sum(f1_scores) / len(f1_scores)


# =========================
# 主评估流程
# =========================

def main():

    samples = load_samples()

    # 缓存已经加载的 conversation 记忆
    memory_cache = {}

    # 混淆矩阵：行是真实标签，列是预测标签
    confusion_matrix = {
        gold: dict.fromkeys(LABELS, 0)
        for gold in LABELS
    }

    total = 0
    correct = 0
    failed = 0

    result_rows = []
    error_rows = []

    print("=" * 60)
    print("Dynamic Memory Relation Evaluation")
    print("=" * 60)
    print(f"Annotation samples: {len(samples)}")
    print(f"Annotation file: {SAMPLE_PATH}")
    print()

    for index, sample in enumerate(samples, start=1):

        pair_id = sample["pair_id"].strip()
        conversation_id = sample["conversation_id"].strip()

        memory_a_id = sample["memory_a_id"].strip()
        memory_b_id = sample["memory_b_id"].strip()

        gold_label = normalize_label(
            sample["gold_relation"]
        )

        evidence = sample.get("evidence", "")

        try:

            # 检查人工标签
            if gold_label not in LABELS:
                raise ValueError(
                    f"非法人工标签：{gold_label}"
                )

            # 加载当前 conversation 的记忆
            if conversation_id not in memory_cache:
                memory_cache[conversation_id] = (
                    load_memory_bundle(conversation_id)
                )

            memory_map = memory_cache[conversation_id]

            # 检查 memory ID
            if memory_a_id not in memory_map:
                raise KeyError(
                    f"memory_a_id 不存在：{memory_a_id}"
                )

            if memory_b_id not in memory_map:
                raise KeyError(
                    f"memory_b_id 不存在：{memory_b_id}"
                )

            # 构建两个 Memory 对象
            mem_a = build_memory(
                memory_map[memory_a_id]
            )

            mem_b = build_memory(
                memory_map[memory_b_id]
            )

            # 调用关系判断模块
            res = judge_pair(mem_a, mem_b)

            pred_label = normalize_label(
                res.relation_type
            )

            model_reason = getattr(
                res,
                "reason",
                ""
            )

            if pred_label not in LABELS:
                raise ValueError(
                    f"模型返回非法标签：{pred_label}"
                )

            total += 1

            # 更新混淆矩阵
            confusion_matrix[gold_label][pred_label] += 1

            is_correct = gold_label == pred_label

            if is_correct:
                correct += 1
                status = "OK"
            else:
                status = "WRONG"

            print(
                f"[{status}] {pair_id:5} | "
                f"gold={gold_label:10} "
                f"pred={pred_label:10}"
            )

            # 保存每条预测结果
            result_rows.append([
                pair_id,
                conversation_id,
                memory_a_id,
                memory_b_id,
                gold_label,
                pred_label,
                is_correct,
                evidence,
                model_reason,
            ])

            # 保存预测错误的样本
            if not is_correct:
                error_rows.append([
                    pair_id,
                    gold_label,
                    pred_label,
                    "prediction mismatch",
                    evidence,
                    model_reason,
                ])

        except Exception as e:

            failed += 1

            print(
                f"[FAIL] {pair_id} | "
                f"{type(e).__name__}: {e}"
            )

            error_rows.append([
                pair_id,
                gold_label,
                "ERROR",
                str(e),
                evidence,
                "",
            ])

    # =========================
    # 计算评估指标
    # =========================

    accuracy = correct / total if total > 0 else 0.0

    macro_f1 = calculate_macro_f1(
        confusion_matrix
    )

    print("\n" + "=" * 60)
    print("Evaluation Summary")
    print("=" * 60)

    print(f"Total annotation pairs: {len(samples)}")
    print(f"Successfully evaluated: {total}")
    print(f"Correct predictions: {correct}")
    print(f"Failed samples: {failed}")
    print(f"Accuracy: {accuracy:.4f}")
    print(f"Macro-F1: {macro_f1:.4f}")

    # =========================
    # 输出混淆矩阵
    # =========================

    print("\nConfusion Matrix (Gold / Predicted)")

    print(
        f"{'Gold / Pred':<14}"
        + "".join(f"{label:<12}" for label in LABELS)
    )

    for gold in LABELS:

        row = f"{gold:<14}"

        for pred in LABELS:
            row += f"{confusion_matrix[gold][pred]:<12}"

        print(row)

    # =========================
    # 保存预测结果
    # =========================

    with open(
        RESULT_OUT_PATH,
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.writer(f)

        writer.writerow([
            "pair_id",
            "conversation_id",
            "memory_a_id",
            "memory_b_id",
            "gold_relation",
            "predicted_relation",
            "correct",
            "evidence",
            "model_reason",
        ])

        writer.writerows(result_rows)

    # =========================
    # 保存错误记录
    # =========================

    with open(
        ERROR_OUT_PATH,
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.writer(f)

        writer.writerow([
            "pair_id",
            "gold_relation",
            "predicted_relation",
            "issue",
            "evidence",
            "model_reason",
        ])

        writer.writerows(error_rows)

    print("\n" + "=" * 60)
    print("Output Files")
    print("=" * 60)

    print(f"Prediction results: {RESULT_OUT_PATH}")
    print(f"Error cases: {ERROR_OUT_PATH}")


if __name__ == "__main__":
    main()
