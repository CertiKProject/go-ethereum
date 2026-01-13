import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from github import Github

from codex_runner import (
    EIP_consitency_check_preprompt,
    EIP_finding_validation_preprompt,
    EIP_dedupe_preprompt,
    run_codex,
)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")
PR_NUMBER = int(os.getenv("PR_NUMBER", 0))
DIFF_FILE_PATH = sys.argv[1] if len(sys.argv) > 1 else "pr.diff"
CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-5.1-codex-max")
CODEX_APPROVAL = os.getenv("CODEX_APPROVAL", "never")
EIP_SPEC_PATH = os.getenv("EIP_SPEC_PATH", "scripts/eips/eip-7825.md") #relative path to EIP spec file in the repo
SKIP_EXTENSIONS = {".md", ".txt", ".lock"}
REPO_ROOT = Path(__file__).resolve().parents[2]


def parse_diff(diff_text: str) -> Dict[str, str]:
    """
    Minimal diff parser that splits the diff into per-file chunks.
    """
    file_chunks: Dict[str, List[str]] = {}
    pattern = re.compile(r"^diff --git a/(.*) b/(.*)", re.MULTILINE)

    current_file = None
    chunk: List[str] = []

    for line in diff_text.splitlines():
        match = pattern.match(line)
        if match:
            if current_file:
                file_chunks[current_file] = "\n".join(chunk)
            current_file = match.group(2)
            chunk = [line]
        else:
            chunk.append(line)

    if current_file:
        file_chunks[current_file] = "\n".join(chunk)

    return file_chunks


def write_diff_chunks(chunks: Dict[str, str]) -> Tuple[Path, Dict[str, Path]]:
    """
    Persist per-file diffs so Codex can read them directly from disk.
    """
    diff_root = REPO_ROOT / "diff_tmp"
    diff_root.mkdir(parents=True, exist_ok=True)
    diff_dir = diff_root
    diff_paths: Dict[str, Path] = {}

    for path, content in chunks.items():
        dest = diff_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        diff_paths[path] = dest.relative_to(REPO_ROOT)
    print("diff files: ", diff_paths.keys())
    return diff_dir, diff_paths


def load_json_block(text: str) -> Dict:
    """
    Attempt to parse JSON from a Codex response, falling back to the first JSON object substring.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                return {}
    return {}


def normalize_issues(payload: Dict, default_path: str, stage: str) -> Tuple[List[Dict], str]:
    issues: List[Dict] = []
    entries = payload.get("issues") or payload.get("reviews") or []
    for entry in entries:
        suggestion = (
            entry.get("suggestion")
            or entry.get("description")
            or entry.get("message")
            or entry.get("text")
            or ""
        )
        if not suggestion:
            continue

        path = entry.get("path") or default_path
        try:
            line = int(entry.get("line") or entry.get("line_number") or 0)
        except (TypeError, ValueError):
            line = 0

        severity = (entry.get("severity") or entry.get("impact") or "").upper()
        spec_ref = entry.get("spec_ref") or ""

        source = entry.get("source") or entry.get("stage") or stage

        issues.append(
            {
                "path": path,
                "line": line,
                "suggestion": suggestion,
                "severity": severity,
                "spec_ref": spec_ref,
                "stage": stage,
                "source": source,
            }
        )

    summary = payload.get("summary") or ""
    return issues, summary.strip()


def build_stage_one_prompt(file_path: str, diff_file: Path) -> str:
    eip_hint = f"@{EIP_SPEC_PATH}" if EIP_SPEC_PATH else "not provided"
    schema_hint = (
        '{ "summary": "<short risk summary>", '
        '"issues": [ { "path": "<file path>", "line": <new file line>, '
        '"severity": "<CRITICAL|HIGH|MEDIUM|LOW|INFO>", '
        '"suggestion": "<issue + recommendation>", "spec_ref": "<optional>" } ] }'
    )
    return (
        f"{EIP_consitency_check_preprompt}\n\n"
        "Stage: per-file diff review.\n"
        f"- Diff file path: {diff_file}\n"
        f"- EIP spec path: {eip_hint}\n"
        "Output JSON only using this schema:\n"
        f"{schema_hint}\n"
        "Use an empty issues array if there are no findings."
    )


def build_stage_two_prompt(diff_dir: Path, diff_paths: Dict[str, Path]) -> str:
    eip_hint = f"@{EIP_SPEC_PATH}" if EIP_SPEC_PATH else "not provided"
    path_list = "\n".join(f"- {p}" for p in diff_paths.values())
    schema_hint = (
        '{ "summary": "<cross-file risk summary>", '
        '"issues": [ { "path": "<file path>", "line": <new file line>, '
        '"severity": "<CRITICAL|HIGH|MEDIUM|LOW|INFO>", '
        '"suggestion": "<issue + recommendation>", "spec_ref": "<optional>" } ] }'
    )
    return (
        f"{EIP_consitency_check_preprompt}\n\n"
        "Stage: cross-file review across the entire PR.\n"
        f"- Diff directory: {diff_dir}\n"
        f"- Diff files:\n{path_list}\n"
        f"- EIP spec path: {eip_hint}\n\n"
        "Output JSON only using this schema:\n"
        f"{schema_hint}\n"
        "Use an empty issues array if there are no findings."
    )


def build_dedupe_prompt(issues: List[Dict]) -> str:
    issue_blob = json.dumps(issues, indent=2)
    schema_hint = (
        '{ "issues": [ { "path": "<file path>", "line": <new file line>, '
        '"severity": "<CRITICAL|HIGH|MEDIUM|LOW|INFO>", '
        '"suggestion": "<consolidated issue>", "spec_ref": "<optional>", '
        '"source": "<stage list>" } ] }'
    )
    return (
        f"{EIP_dedupe_preprompt}\n\n"
        "You will deduplicate findings produced by multiple review stages. "
        "Consolidate overlapping issues into a single representative entry.\n\n"
        "Issues to dedupe (JSON):\n"
        f"{issue_blob}\n\n"
        "Return JSON only using this schema:\n"
        f"{schema_hint}\n"
        "If no issues remain, return an empty array."
    )


def build_validation_prompt(
    issues: List[Dict],
    diff_dir: Path,
    diff_paths: Dict[str, Path],
) -> str:
    eip_hint = f"@{EIP_SPEC_PATH}" if EIP_SPEC_PATH else "not provided"
    issue_blob = json.dumps(issues, indent=2)
    path_list = "\n".join(f"- {p}" for p in diff_paths.values())
    schema_hint = (
        '{ "validated": [ { "path": "<file path>", "line": <new file line>, '
        '"verdict": "VALID|INVALID|SPEC-AMBIGUOUS", '
        '"severity": "<CRITICAL|HIGH|MEDIUM|LOW|INFO>", '
        '"justification": "<reasoning>", "recommendation": "<action>", '
        '"spec_ref": "<optional>", "source": "<stage1|stage2>" } ] }'
    )
    return (
        f"{EIP_finding_validation_preprompt}\n\n"
        f"- EIP spec path: {eip_hint}\n"
        f"- Diff directory: {diff_dir}\n"
        f"- Diff files:\n{path_list}\n\n"
        "Issues to validate (JSON):\n"
        f"{issue_blob}\n\n"
        "Return JSON only using this schema:\n"
        f"{schema_hint}\n"
        "Only include issues you have validated; drop items you consider invalid."
    )


def run_codex_json(prompt: str) -> Dict:
    output = run_codex(
        prompt,
        project_root=str(REPO_ROOT),
        model=CODEX_MODEL,
        approval=CODEX_APPROVAL,
    )
    data = load_json_block(output)
    if not data:
        print("[codex] Non-JSON output, returning empty payload.")
    return data


def stage_one(diff_paths: Dict[str, Path]) -> Tuple[List[Dict], List[str]]:
    issues: List[Dict] = []
    summaries: List[str] = []

    for path, diff_file in diff_paths.items():
        if Path(path).suffix in SKIP_EXTENSIONS:
            continue
        prompt = build_stage_one_prompt(path, diff_file)
        data = run_codex_json(prompt)
        file_issues, summary = normalize_issues(data, path, "stage1")
        issues.extend(file_issues)
        if summary:
            summaries.append(f"{path}: {summary}")

    return issues, summaries


def stage_two(diff_dir: Path, diff_paths: Dict[str, Path]) -> Tuple[List[Dict], List[str]]:
    prompt = build_stage_two_prompt(diff_dir, diff_paths)
    data = run_codex_json(prompt)
    issues, summary = normalize_issues(data, default_path="", stage="stage2")
    summaries = [f"cross-file: {summary}"] if summary else []
    return issues, summaries


def stage_dedupe(issues: List[Dict]) -> List[Dict]:
    if not issues:
        return []
    prompt = build_dedupe_prompt(issues)
    data = run_codex_json(prompt)
    deduped, _ = normalize_issues(data, default_path="", stage="dedupe")
    return deduped


def stage_three_validate(
    combined_issues: List[Dict],
    diff_dir: Path,
    diff_paths: Dict[str, Path],
) -> List[Dict]:
    if not combined_issues:
        return []

    prompt = build_validation_prompt(combined_issues, diff_dir, diff_paths)
    data = run_codex_json(prompt)
    validated_entries = data.get("validated") or data.get("issues") or []

    validated: List[Dict] = []
    for entry in validated_entries:
        verdict = (entry.get("verdict") or "").upper()
        if verdict not in {"VALID", "SPEC-AMBIGUOUS", "PARTIAL"}:
            continue

        try:
            line = int(entry.get("line") or entry.get("line_number") or 0)
        except (TypeError, ValueError):
            line = 0

        path = entry.get("path") or ""
        if not path or line <= 0:
            continue

        validated.append(
            {
                "path": path,
                "line": line,
                "verdict": verdict,
                "severity": entry.get("severity", "").upper(),
                "suggestion": entry.get("recommendation")
                or entry.get("suggestion")
                or entry.get("description")
                or "",
                "justification": entry.get("justification") or entry.get("reason") or "",
                "spec_ref": entry.get("spec_ref") or "",
                "source": entry.get("source") or entry.get("stage") or "",
            }
        )

    return validated


def build_comments(validated: List[Dict]) -> List[Dict]:
    comments: List[Dict] = []
    for item in validated:
        path = item.get("path")
        line = item.get("line")
        verdict = item.get("verdict")
        severity = item.get("severity") or ""
        if not path or not line:
            continue

        body_lines = [f"🤖 **Codex Reviewer ({verdict})**" + (f" [{severity}]" if severity else "")]

        if item.get("suggestion"):
            body_lines.append(item["suggestion"])
        if item.get("justification"):
            body_lines.append(f"Reason: {item['justification']}")
        if item.get("spec_ref"):
            body_lines.append(f"Spec: {item['spec_ref']}")
        if item.get("source"):
            body_lines.append(f"Source stage: {item['source']}")

        comments.append(
            {
                "path": path,
                "line": int(line),
                "body": "\n".join(body_lines),
                "side": "RIGHT",
            }
        )

    return comments


def main() -> None:
    # if not Path(DIFF_FILE_PATH).exists():
    #     print(f"Diff file not found at {DIFF_FILE_PATH}")
    #     return
    # if not (GITHUB_TOKEN and REPO_NAME and PR_NUMBER):
    #     print("Missing required environment variables (GITHUB_TOKEN, REPO_NAME, PR_NUMBER).")
    #     return

    diff_text = Path(DIFF_FILE_PATH).read_text(encoding="utf-8")
    chunks = parse_diff(diff_text)

    if not chunks:
        print("No diff chunks found.")
        return

    diff_dir, diff_paths = write_diff_chunks(chunks)
    stage1_issues, stage1_summaries = stage_one(diff_paths)
    stage2_issues, stage2_summaries = stage_two(diff_dir, diff_paths)

    merged_issues = stage_dedupe(stage1_issues + stage2_issues)
    validated = stage_three_validate(merged_issues, diff_dir, diff_paths)
    comments = build_comments(validated)

    summaries = stage1_summaries + stage2_summaries
    if validated:
        summaries.append(f"Validated findings: {len(validated)}")
    else:
        summaries.append("No validated issues found.")

    gh = Github(GITHUB_TOKEN)
    repo = gh.get_repo(REPO_NAME)
    pull = repo.get_pull(PR_NUMBER)
    print("Codex Review Summary:", "\n".join(summaries))
    if comments:
        pull.create_review(body="\n".join(f"- {s}" for s in summaries), event="COMMENT", comments=comments)
    else:
        pull.create_review(body="\n".join(f"- {s}" for s in summaries), event="COMMENT")

    print(f"Posted Codex review with {len(comments)} inline comments.")


if __name__ == "__main__":
    main()
