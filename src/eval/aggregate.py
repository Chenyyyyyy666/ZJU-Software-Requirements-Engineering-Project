"""Aggregate recorded evaluation rows into reproducible experiment artifacts.

The course runner writes one JSON file per conversation.  This module combines
those files without re-running a model and produces JSON, CSV, Markdown and a
small SVG chart for the experiment folder.  Prefer passing answer-row files so
that macro metrics are recomputed over the complete set rather than averaged
from per-conversation summaries.

Example::

    python -m src.eval.aggregate \
      --dataset dev \
      --input ours=outputs/eval/conv-26-hybrid-answers.json \
      --input ours=outputs/eval/conv-30-hybrid-answers.json \
      --input vector=outputs/eval/conv-26-vector-answers.json \
      --output-dir experiments/2026-11-01-dev-results
"""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.eval.metrics import evaluate_rows

METRIC_COLUMNS = (
    ("qa_f1", "QA F1"),
    ("recall@1", "Recall@1"),
    ("recall@5", "Recall@5"),
    ("relation_accuracy", "Relation accuracy"),
    ("average_tokens", "Average tokens"),
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_binding(value: str) -> tuple[str, Path]:
    method, separator, raw_path = value.partition("=")
    if not separator or not method.strip() or not raw_path.strip():
        raise ValueError(f"expected METHOD=PATH, got {value!r}")
    return method.strip(), Path(raw_path).expanduser()


def _rows_or_metrics(
    payload: Any, path: Path
) -> tuple[list[Mapping[str, Any]] | None, dict[str, Any] | None]:
    if isinstance(payload, list):
        if not all(isinstance(row, Mapping) for row in payload):
            raise ValueError(f"answer file contains a non-object row: {path}")
        return list(payload), None
    if not isinstance(payload, Mapping):
        raise ValueError(f"evaluation input must be a JSON list or object: {path}")
    for key in ("answers", "rows"):
        rows = payload.get(key)
        if isinstance(rows, list):
            if not all(isinstance(row, Mapping) for row in rows):
                raise ValueError(f"{key} contains a non-object row: {path}")
            return list(rows), None
    if any(key in payload for key, _ in METRIC_COLUMNS):
        return None, dict(payload)
    raise ValueError(f"cannot find answer rows or metric fields in {path}")


def aggregate_inputs(bindings: Iterable[str], *, dataset: str) -> dict[str, Any]:
    """Load answer/metric files and return one summary object.

    Multiple answer-row files may be supplied for one method.  They are merged
    before scoring, which keeps QA F1 and Recall denominators correct.
    """

    grouped_rows: dict[str, list[Mapping[str, Any]]] = {}
    metric_only: dict[str, dict[str, Any]] = {}
    input_paths: dict[str, list[str]] = {}
    for raw_binding in bindings:
        method, path = _parse_binding(raw_binding)
        if not path.exists():
            raise FileNotFoundError(path)
        rows, metrics = _rows_or_metrics(_load_json(path), path)
        input_paths.setdefault(method, []).append(str(path))
        if rows is not None:
            grouped_rows.setdefault(method, []).extend(rows)
        else:
            if method in metric_only:
                raise ValueError(
                    f"multiple metric-only files for {method!r}; pass answer-row files "
                    "to recompute a weighted summary"
                )
            metric_only[method] = metrics or {}

    methods = sorted(set(grouped_rows) | set(metric_only))
    result_methods: dict[str, dict[str, Any]] = {}
    for method in methods:
        metrics = (
            evaluate_rows(grouped_rows[method])
            if method in grouped_rows
            else dict(metric_only[method])
        )
        result_methods[method] = {
            **metrics,
            "input_files": input_paths[method],
            "source": "answer_rows" if method in grouped_rows else "metrics_json",
        }
    return {
        "dataset": dataset,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "methods": result_methods,
    }


def _display(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render_markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Evaluation summary",
        "",
        f"- Dataset: `{summary['dataset']}`",
        f"- Generated: `{summary['generated_at']}`",
        "- `—` means the input did not contain enough labels to compute that metric.",
        "",
        "| Method | Rows | QA F1 | Recall@1 | Recall@5 | Relation accuracy | Average tokens |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for method, metrics in summary["methods"].items():
        lines.append(
            "| {method} | {rows} | {qa_f1} | {r1} | {r5} | {relation} | {tokens} |".format(
                method=method,
                rows=metrics.get("rows", "—"),
                qa_f1=_display(metrics.get("qa_f1")),
                r1=_display(metrics.get("recall@1")),
                r5=_display(metrics.get("recall@5")),
                relation=_display(metrics.get("relation_accuracy")),
                tokens=_display(metrics.get("average_tokens")),
            )
        )
    lines.extend(
        [
            "",
            "## Input files",
            "",
        ]
    )
    for method, metrics in summary["methods"].items():
        paths = ", ".join(f"`{path}`" for path in metrics["input_files"])
        lines.append(f"- **{method}**: {paths}")
    lines.extend(
        [
            "",
            "The summary is a measurement record, not evidence that a method is better unless the "
            "same questions, split, model configuration and token accounting were used "
            "for every row.",
            "",
        ]
    )
    return "\n".join(lines)


def write_csv(summary: Mapping[str, Any], path: Path) -> None:
    fieldnames = ["method", "rows", *(key for key, _ in METRIC_COLUMNS)]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for method, metrics in summary["methods"].items():
            writer.writerow(
                {
                    "method": method,
                    "rows": metrics.get("rows"),
                    **{key: metrics.get(key) for key, _ in METRIC_COLUMNS},
                }
            )


def _svg_bar(
    value: float | None,
    maximum: float,
    x: float,
    y: float,
    width: float,
    height: float,
) -> str:
    if value is None:
        return ""
    bar_width = max(0.0, min(width, width * value / maximum)) if maximum else 0.0
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{height:.1f}" rx="3" />'


def render_svg(summary: Mapping[str, Any]) -> str:
    """Render a dependency-free, printable comparison chart."""

    methods = list(summary["methods"])
    width, height = 860, 360
    panel_width, panel_height = 260, 260
    panels = [
        ("qa_f1", "QA F1", 1.0),
        ("recall@5", "Recall@5", 1.0),
        ("relation_accuracy", "Relation accuracy", 1.0),
    ]
    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">'
        ),
        (
            "<style>text{font-family:Arial,sans-serif;fill:#243447} "
            ".axis{stroke:#9aa7b2} rect{fill:#2f6f9f}</style>"
        ),
        '<rect width="100%" height="100%" fill="white"/>',
    ]
    for panel_index, (key, title, maximum) in enumerate(panels):
        origin_x = 20 + panel_index * (panel_width + 20)
        origin_y = 28
        parts.append(
            f'<text x="{origin_x}" y="18" font-size="15" font-weight="bold">{title}</text>'
        )
        parts.append(
            f'<line class="axis" x1="{origin_x}" y1="{origin_y + panel_height}" '
            f'x2="{origin_x + panel_width}" y2="{origin_y + panel_height}"/>'
        )
        if not methods:
            continue
        slot = panel_width / len(methods)
        for index, method in enumerate(methods):
            value = summary["methods"][method].get(key)
            x = origin_x + index * slot + slot * 0.2
            y = origin_y + panel_height - 20
            bar_height = (panel_height - 40) * (value or 0)
            parts.append(_svg_bar(value, maximum, x, y - bar_height, slot * 0.6, bar_height))
            label = method[:14]
            parts.append(
                f'<text x="{x}" y="{origin_y + panel_height + 17}" font-size="10" '
                f'transform="rotate(25 {x} {origin_y + panel_height + 17})">{label}</text>'
            )
            if value is not None:
                parts.append(
                    f'<text x="{x}" y="{y - (panel_height - 40) * value - 4}" '
                    f'font-size="10">{value:.3f}</text>'
                )
    parts.append(
        '<text x="20" y="345" font-size="11">Generated from recorded evaluation '
        "rows; missing metrics are left blank.</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="dev", help="split name recorded in the output")
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        metavar="METHOD=PATH",
        help="recorded answer rows or one metrics JSON; repeat for files/methods",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    summary = aggregate_inputs(args.input, dataset=args.dataset)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "summary.md").write_text(render_markdown(summary), encoding="utf-8")
    write_csv(summary, args.output_dir / "summary.csv")
    (args.output_dir / "summary.svg").write_text(render_svg(summary), encoding="utf-8")
    print(f"wrote evaluation summary for {len(summary['methods'])} method(s) to {args.output_dir}")


if __name__ == "__main__":
    main()
