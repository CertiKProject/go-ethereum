from codex_runner import run_codex, EIP_consitency_check_preprompt, EIP_finding_validation_preprompt


if __name__ == "__main__":
    # print(EIP_consitency_check_preprompt)
    # print(EIP_finding_validation_preprompt)
    diff_files = [] # List of diff file relative paths
    codebase_path = "" #codebase relative path
    eip_path = "eips/eip-7825.md" #eip relative path
    for diff_file in diff_files:
        prompt = f"{EIP_consitency_check_preprompt}\n\nEIP: @{eip_path}\nDIFF:@{diff_file}\ncodebase:@{codebase_path} "
        response = run_codex(prompt)
        print(f"Response for {diff_file}:\n{response}\n")
    # validate the reponse ...
