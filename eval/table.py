"""Turn scored eval rows into a per-task, per-header table and a csv."""
import csv
import io
import json
import subprocess
from pathlib import Path

CSV_HEADER = "case_id,task,header,expected,got,correct,conf,ms"
CSV_COLUMNS = CSV_HEADER.split(",")
ROOT = Path(__file__).resolve().parents[1]


def git_short_sha() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "local"
    sha = proc.stdout.strip()
    if not sha or any(ch in sha for ch in "/\\ \n"):
        return "local"
    return sha


def median(values: list[float]) -> float:
    ordered = sorted(float(v) for v in values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def summarize(rows: list[dict]) -> dict:
    """Per task and header: n, accuracy, abstain rate, median ms.

    Accuracy is correct answers divided by answers that were not abstentions.
    An abstention is never correct, and it is not treated as a wrong answer.
    When every row in a group abstained, accuracy is None.
    """
    groups: dict[tuple[str, str], list] = {}
    for row in rows:
        key = (row["task"], row.get("header") or "")
        groups.setdefault(key, []).append(row)

    summary_rows = []
    for task, header in sorted(groups):
        items = groups[(task, header)]
        n = len(items)
        abstain = sum(1 for item in items if item["abstain"])
        correct = sum(1 for item in items if item["correct"])
        decided = n - abstain
        summary_rows.append({
            "task": task,
            "header": header,
            "n": n,
            "accuracy": (correct / decided) if decided else None,
            "abstain_rate": abstain / n,
            "median_ms": median([item["ms"] for item in items]),
        })

    caught = 0
    for row in rows:
        if row["task"] != "verify" or row["abstain"]:
            continue
        if row["expected"] is False and row["got"] is False:
            caught += 1

    return {"groups": summary_rows, "wrong_plugs_caught": caught}


def _fmt_accuracy(value) -> str:
    if value is None:
        return ""
    return f"{value:.3f}"


def _fmt_ms(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def to_markdown(summary: dict) -> str:
    """One table row per task and header, then the wrong-plug count."""
    lines = [
        "| task | header | n | accuracy | abstain rate | median ms |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in summary["groups"]:
        lines.append(
            "| {task} | {header} | {n} | {accuracy} | {abstain} | {ms} |".format(
                task=row["task"],
                header=row["header"],
                n=row["n"],
                accuracy=_fmt_accuracy(row["accuracy"]),
                abstain=f"{row['abstain_rate']:.3f}",
                ms=_fmt_ms(row["median_ms"]),
            )
        )
    lines.append("")
    lines.append(f"Wrong plugs caught: {summary['wrong_plugs_caught']}")
    lines.append("")
    return "\n".join(lines)


def _cell(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def rows_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow([
            row["case_id"],
            row["task"],
            row["header"],
            _cell(row["expected"]),
            _cell(row["got"]),
            "true" if row["correct"] else "false",
            _cell(row["conf"]),
            _cell(row["ms"]),
        ])
    return buf.getvalue()


def write_report(rows: list[dict], out_dir: Path) -> tuple[Path, Path]:
    """Write `<sha>.csv` and `table.md`. The csv never contains image bytes."""
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{git_short_sha()}.csv"
    md_path = out_dir / "table.md"
    csv_path.write_text(rows_csv(rows))
    md_path.write_text(to_markdown(summarize(rows)))
    return csv_path, md_path
