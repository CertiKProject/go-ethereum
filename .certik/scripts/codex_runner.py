import subprocess

with open("prompts/pr-review.md", "r") as f:
    EIP_consitency_check_preprompt = f.read()
with open("prompts/issue-validation.md", "r") as f:
    EIP_finding_validation_preprompt = f.read()

    
def run_codex(prompt: str, model="gpt-5.1-codex-max") -> str:
    result = subprocess.run(
        ["codex", "exec", "--skip-git-repo-check", "--model", model, prompt],
        capture_output=True,
        text=True,
    )
    return result.stdout




    
    # run finding validation?
    

