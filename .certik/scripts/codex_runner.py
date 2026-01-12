import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

with open(BASE_DIR / "prompts" / "pr-review.md", "r", encoding="utf-8") as f:
    EIP_consitency_check_preprompt = f.read()
with open(BASE_DIR / "prompts" / "issue-validation.md", "r", encoding="utf-8") as f:
    EIP_finding_validation_preprompt = f.read()
with open(BASE_DIR / "prompts" / "dedupe-issues.md", "r", encoding="utf-8") as f:
    EIP_dedupe_preprompt = f.read()


def run_codex(prompt: str, project_root: str, model: str = "gpt-5.1-codex-max", approval: str = "never") -> str:
    cmd = ["codex",
        "--ask-for-approval",
        approval,
        "exec",
        "--skip-git-repo-check",
        "--model",
        model,
        "--cd",
        project_root,
        prompt,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"codex exited with {result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


if __name__ == "__main__":
    print(run_codex("return 42 for testing purpose"))
