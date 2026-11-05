"""One-command stages for checks, paid runs, freezing and report generation.

Use: python -m experiments.final_workflow --help
All paid stages require --execute; check/report/package are offline.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from src.common.json_io import write_json

ROOT = Path(__file__).resolve().parents[1]
FINAL = ROOT / "outputs/final"
MEMORY = ROOT / "outputs/locomo-public/memory"
GOLD = ROOT / "experiments/final-relation-labels/memory_pairs_gold.csv"


def call(module: str, *args: str) -> None:
    subprocess.run([sys.executable, "-m", module, *map(str, args)], cwd=ROOT, check=True)


def eval_args(dataset: str, directory: str) -> list[str]:
    return [
        "--data",
        "locomo.json",
        "--memory-dir",
        str(MEMORY),
        "--dataset",
        dataset,
        "--splits",
        "experiments/splits.json",
        "--output-dir",
        str(FINAL / directory),
        "--top-k",
        "5",
        "--candidate-k",
        "30",
    ]


def render_test_strategy_section(
    report_text: str, checks: list[dict], generated_at_utc: str
) -> str:
    """Update measured cells while preserving the original §6.1 table and other rows."""
    pytest_log = Path(checks[0]["log"]).relative_to(ROOT)
    ruff_log = Path(checks[1]["log"]).relative_to(ROOT)
    format_log = Path(checks[2]["log"]).relative_to(ROOT)
    preflight_log = Path(checks[3]["log"]).relative_to(ROOT)
    pytest_text = Path(checks[0]["log"]).read_text(encoding="utf-8")
    pytest_result = next(
        (line.strip() for line in pytest_text.splitlines() if " passed" in line),
        "pytest 已完成；详见日志",
    )
    ruff_result = Path(checks[1]["log"]).read_text(encoding="utf-8").strip()
    format_result = Path(checks[2]["log"]).read_text(encoding="utf-8").strip()
    date = generated_at_utc[:10]
    results = {
        "单元测试": (
            f"{date}，系统与 Python 版本见 `outputs/final/verification.json`；"
            f"`python -m pytest -o addopts= -q`：{pytest_result}（全仓总数，包含集成测试，"
            f"未单独统计纯单元测试数）；记录：`{pytest_log}`。"
        ),
        "静态检查": (
            f"{date}：`python -m ruff check .`：{ruff_result}；"
            f"`python -m ruff format --check .`：{format_result}；"
            f"记录：`{ruff_log}`、`{format_log}`。"
        ),
        "集成测试": (
            f"{date}：Memory 保存/加载、检索、推理与指标交接随全仓 pytest 验证，"
            "通过数计入单元测试行的总数；"
            "`python -m experiments.run_final_eval --dataset dev ...` 开发集来源预检查通过，"
            f"完整参数见 `outputs/final/verification.json`，记录：`{preflight_log}`。"
        ),
    }
    start = report_text.index("### 6.1 测试策略")
    end = report_text.index("### 6.2", start)
    section = report_text[start:end]
    if "| 层次 | 验证内容 | 工具或证据 | 最终结果 |" not in section:
        raise ValueError("§6.1 requires the original four-column test strategy table")
    lines = section.splitlines(keepends=True)
    updated = set()
    for index, line in enumerate(lines):
        cells = line.split("|")
        if len(cells) == 6 and cells[1].strip() in results:
            name = cells[1].strip()
            cells[4] = " " + results[name].replace("\n", " ").replace("|", "/") + " "
            lines[index] = "|".join(cells)
            updated.add(name)
    if updated != results.keys():
        raise ValueError("§6.1 is missing a required original test strategy row")
    return report_text[:start] + "".join(lines) + report_text[end:]


def check() -> None:
    FINAL.mkdir(parents=True, exist_ok=True)
    checks = []
    commands = [
        [sys.executable, "-m", "pytest", "-o", "addopts=", "-q"],
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "ruff", "format", "--check", "."],
        [sys.executable, "-m", "experiments.run_final_eval", *eval_args("dev", "preflight")],
    ]
    for number, command in enumerate(commands, 1):
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        log = FINAL / f"check-{number}.txt"
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
        print(result.stdout + result.stderr, flush=True)
        checks.append({"command": command, "returncode": result.returncode, "log": str(log)})
    generated_at_utc = datetime.now(UTC).isoformat()
    write_json(
        {
            "generated_at_utc": generated_at_utc,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "checks": checks,
            "all_passed": all(item["returncode"] == 0 for item in checks),
            "scope": "offline tests and dev source verification; no model calls",
        },
        FINAL / "verification.json",
    )
    if not all(item["returncode"] == 0 for item in checks):
        raise SystemExit("Some checks failed; see outputs/final/check-*.txt")
    report = ROOT / "docs/final-report.md"
    report.write_text(
        render_test_strategy_section(report.read_text(encoding="utf-8"), checks, generated_at_utc),
        encoding="utf-8",
    )


def build_test() -> None:
    from src.common.json_io import digest
    from src.memory.build import BuildConfig, build_memories
    from src.memory.locomo import load_conversation
    from src.memory.store import load_memories, save_memories

    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("Set DEEPSEEK_API_KEY in the local .env first")
    split = json.loads((ROOT / "experiments/splits.json").read_text(encoding="utf-8-sig"))
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
    config = BuildConfig(model=model, max_tokens=8192, cache_dir=ROOT / "cache")
    path = FINAL / "test-build-manifest.json"
    manifest = json.loads(path.read_text()) if path.exists() else {"conversations": {}}
    manifest.update(
        {
            "started_at_utc": datetime.now(UTC).isoformat(),
            "config": config.model_dump(mode="json"),
            "config_id": config.config_id,
            "input_scope": "raw conversations only; no QA, evidence labels or event summaries",
        }
    )
    for conversation in split["test"]:
        destination = MEMORY / f"{conversation}.json"
        item = manifest["conversations"].setdefault(conversation, {"events": []})
        if destination.exists():
            bundle = load_memories(destination)
            if bundle.source_turns != load_conversation(ROOT / "locomo.json", conversation):
                raise ValueError(f"Existing test bundle has different data: {conversation}")
            if bundle.build_info.extractor_config_id != config.config_id:
                raise ValueError(
                    f"Existing test bundle has different configuration: {conversation}"
                )
            item.update(
                status="existing_verified",
                build_id=bundle.build_info.build_id,
                memory_count=len(bundle.memories),
            )
            write_json(manifest, path)
            continue

        def on_event(event: dict, item=item, conversation=conversation) -> None:
            item["events"].append(event)
            write_json(manifest, path)
            print(f"{conversation}: {event['kind']}", flush=True)

        started = time.perf_counter()
        item["status"] = "running"
        write_json(manifest, path)
        try:
            bundle = build_memories(
                ROOT / "locomo.json", conversation, config=config, on_event=on_event
            )
            save_memories(bundle, destination)
            replay = build_memories(
                ROOT / "locomo.json", conversation, config=config, cache_only=True
            )
            if bundle != replay:
                raise ValueError("Cache replay differs")
            item.update(
                status="complete",
                memory_count=len(bundle.memories),
                build_id=bundle.build_info.build_id,
                cache_replay_identical=True,
                bundle_sha256=digest(bundle.model_dump(mode="json")),
                elapsed_seconds=time.perf_counter() - started,
            )
            write_json(manifest, path)
        except Exception as exc:
            item.update(status="failed", error_type=type(exc).__name__)
            write_json(manifest, path)
            raise
    manifest["finished_at_utc"] = datetime.now(UTC).isoformat()
    write_json(manifest, path)
    print("Test memory built without QA labels; inspect sources, then freeze before testing.")


def package() -> None:
    """Create a local evidence archive, never a claim of course submission."""
    FINAL.mkdir(parents=True, exist_ok=True)
    files = []
    for directory in ("src", "tests", "examples", "docs", "slides", "process", "experiments"):
        files.extend((ROOT / directory).rglob("*"))
    for name in (
        "README.md",
        "pyproject.toml",
        "requirements.txt",
        "requirements-vector.txt",
        "requirements-report.txt",
        ".env.example",
    ):
        files.append(ROOT / name)
    files.extend(FINAL.rglob("*"))
    files.extend(MEMORY.glob("*.json"))
    excluded = {".env", ".DS_Store"}
    chosen = sorted(
        {
            path
            for path in files
            if path.is_file()
            and path.name not in excluded
            and not path.is_symlink()
            and "__pycache__" not in path.parts
            and "vector-cache" not in path.parts
            and path.suffix not in {".pyc", ".zip"}
        }
    )
    secret = os.environ.get("DEEPSEEK_API_KEY", "")
    with zipfile.ZipFile(FINAL / "final-evidence.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in chosen:
            # Never include a known local API key even if a log accidentally contains it.
            if len(secret) >= 12 and secret.encode() in path.read_bytes():
                raise ValueError(f"Local credential found; archive stopped: {path.name}")
            archive.write(path, str(path.relative_to(ROOT)))
    with zipfile.ZipFile(FINAL / "process-files.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT / "process").rglob("*")):
            if path.is_file() and not path.name.startswith("."):
                archive.write(path, str(path.relative_to(ROOT)))
    print("Created outputs/final/final-evidence.zip and process-files.zip.")
    print(
        "Check PDF/slides, meeting evidence and acceptance before submitting these actual records."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=[
            "check",
            "smoke",
            "dev",
            "build-test",
            "freeze",
            "test",
            "relations",
            "report",
            "package",
        ],
    )
    parser.add_argument("--execute", action="store_true", help="Authorize paid API stages")
    args = parser.parse_args()
    os.chdir(ROOT)
    load_dotenv(ROOT / ".env")
    if args.stage in {"smoke", "dev", "build-test", "test", "relations"} and not args.execute:
        parser.error("This stage calls DeepSeek; add --execute when ready")
    if args.stage == "check":
        check()
    elif args.stage in {"smoke", "dev"}:
        extra = ["--limit", "1"] if args.stage == "smoke" else []
        call(
            "experiments.run_final_eval",
            *eval_args("dev", args.stage),
            *extra,
            "--execute",
            "--resume",
        )
    elif args.stage == "build-test":
        build_test()
    elif args.stage == "freeze":
        call(
            "src.eval.freeze",
            "--data",
            "locomo.json",
            "--memory-dir",
            MEMORY,
            "--dev-dir",
            FINAL / "dev",
            "--output",
            FINAL / "freeze.json",
        )
    elif args.stage == "test":
        call(
            "experiments.run_final_eval",
            *eval_args("test", "test"),
            "--freeze-record",
            FINAL / "freeze.json",
            "--execute",
            "--resume",
        )
    elif args.stage == "relations":
        call(
            "experiments.evaluate_relations",
            "--memory-dir",
            MEMORY,
            "--gold",
            GOLD,
            "--output-dir",
            FINAL / "relations",
            "--resume",
        )
    elif args.stage == "report":
        parameters = [
            "--output-dir",
            str(FINAL / "report"),
            "--report",
            "docs/final-report.md",
            "--figures",
            "docs/figures",
        ]
        for flag, name in (
            ("--dev-dir", "dev"),
            ("--test-dir", "test"),
            ("--relations-dir", "relations"),
        ):
            if (FINAL / name / "manifest.json").exists():
                parameters.extend([flag, str(FINAL / name)])
        if not any(flag in parameters for flag in ("--dev-dir", "--test-dir", "--relations-dir")):
            raise ValueError("No completed experiment runs; run dev/relations first")
        call("src.eval.finalize", *parameters)
    else:
        package()


if __name__ == "__main__":
    main()
