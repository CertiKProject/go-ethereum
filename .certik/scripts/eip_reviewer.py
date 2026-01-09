import os
import sys
import json
import re
from openai import OpenAI
from github import Github

# 从环境变量获取配置
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
REPO_NAME = os.getenv("REPO_NAME")
PR_NUMBER = int(os.getenv("PR_NUMBER", 0))
DIFF_FILE_PATH = sys.argv[1] if len(sys.argv) > 1 else "pr.diff"
SCRIPT_DIR = os.path.dirname(__file__)
EIP_DIR = os.path.join(SCRIPT_DIR, "..", "eips")
PROMPTS_DIR = os.path.join(SCRIPT_DIR, "..", "prompts")

# 初始化客户端
client = OpenAI(api_key=OPENAI_API_KEY)
gh = Github(GITHUB_TOKEN)

def parse_diff(diff_text):
    """
    极简 Diff 解析器
    将 diff 分割成文件块，便于处理大 PR
    """
    file_chunks = {}
    # 匹配 diff --git a/path/to/file b/path/to/file
    pattern = re.compile(r'^diff --git a/(.*) b/(.*)', re.MULTILINE)
    
    current_file = None
    lines = diff_text.split('\n')
    
    chunk_content = []
    for line in lines:
        match = pattern.match(line)
        if match:
            if current_file:
                file_chunks[current_file] = "\n".join(chunk_content)
            current_file = match.group(2)
            chunk_content = [line]
        else:
            chunk_content.append(line)
            
    if current_file:
        file_chunks[current_file] = "\n".join(chunk_content)
        
    return file_chunks

def extract_eip_number(text):
    """
    从文本中提取 EIP 号码（支持多个）
    返回 EIP 号码的列表，例如 ['7701', '7825']
    """
    # 匹配 EIP-数字 的格式
    pattern = r'eip-(\d+)'
    # 忽略大小写
    matches = re.findall(pattern, text, re.IGNORECASE)
    return list(set(matches))  # 去重

def load_eip_document(eip_number):
    """
    从 .certik/eips 目录加载对应的 EIP 文档
    返回文档内容或 None（如果文件不存在）
    """
    eip_file = os.path.join(EIP_DIR, f"eip-{eip_number}.md")
    if os.path.exists(eip_file):
        try:
            with open(eip_file, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            print(f"Error reading EIP-{eip_number}: {e}")
            return None
    return None

def load_prompt(prompt_name):
    """
    从 .certik/prompts 目录加载对应的 prompt 模板
    返回 prompt 内容或 None（如果文件不存在）
    """
    prompt_file = os.path.join(PROMPTS_DIR, f"{prompt_name}.md")
    if os.path.exists(prompt_file):
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            print(f"Error reading prompt {prompt_name}: {e}")
            return None
    return None

def get_pr_title(repo_name, pr_number):
    """
    从 GitHub API 获取 PR 的标题
    """
    try:
        repo = gh.get_repo(repo_name)
        pull = repo.get_pull(pr_number)
        return pull.title
    except Exception as e:
        print(f"Error fetching PR title: {e}")
        return None

def get_ai_review(file_path, diff_content, eip_specs):
    """
    第一轮：调用 AI 进行 PR 代码审查
    eip_specs: dict，格式为 {eip_number: eip_content}
    返回审查意见列表
    """
    prompt_template = load_prompt("pr-review")
    if not prompt_template:
        print("Warning: pr-review.md prompt template not found, using default prompt")
        prompt_template = "Review the following code diff and EIP specification."
    
    # 构建 EIP 规范上下文
    eip_context = "\n\n".join([
        f"=== EIP-{eip_num} ===\n{content}"
        for eip_num, content in eip_specs.items()
    ])
    
    prompt = f"""
{prompt_template}

FILE: {file_path}

EIP SPECIFICATIONS:
{eip_context}

CODE DIFF TO REVIEW:
{diff_content}

Please analyze the above PR diff against the EIP specification and provide your findings in JSON format.
Your response must be a valid JSON object with the following structure:
{{
  "mandatory_rules": [
    {{
      "rule": "description of rule",
      "scope": "where it applies",
      "enforcement_point": "where it's enforced"
    }}
  ],
  "findings": [
    {{
      "severity": "CRITICAL|HIGH|MEDIUM|LOW",
      "issue": "description of the issue",
      "file": "{file_path}",
      "spec_ref": "EIP-XXX section",
      "recommendation": "how to fix"
    }}
  ],
  "assessment": "Safe to merge | Unsafe (consensus risk) | Needs fixes | Needs spec clarification"
}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a security-focused Ethereum protocol auditor. Respond with valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        result = json.loads(response.choices[0].message.content)
        return result
    except Exception as e:
        print(f"Error calling AI for {file_path}: {e}")
        return {"findings": [], "assessment": "Error during review"}

def validate_review_findings(eip_specs, pr_code, initial_findings):
    """
    第二轮：对第一轮的审查结果进行 validation
    eip_specs: dict，格式为 {eip_number: eip_content}
    pr_code: PR diff 内容
    initial_findings: 第一轮生成的 findings
    返回验证后的结果
    """
    prompt_template = load_prompt("issue-validation")
    if not prompt_template:
        print("Warning: issue-validation.md prompt template not found, using default prompt")
        prompt_template = "Validate the following code review findings."
    
    # 构建 EIP 规范上下文
    eip_context = "\n\n".join([
        f"=== EIP-{eip_num} ===\n{content}"
        for eip_num, content in eip_specs.items()
    ])
    
    # 格式化 initial findings
    findings_text = json.dumps(initial_findings, indent=2)
    
    prompt = f"""
{prompt_template}

EIP SPECIFICATIONS:
{eip_context}

PR DIFF:
{pr_code}

INITIAL REVIEW FINDINGS:
{findings_text}

Please independently validate each reported finding and provide verification results in JSON format.
Your response must be a valid JSON object with the following structure:
{{
  "validation_results": [
    {{
      "issue_index": 0,
      "original_issue": "original issue description",
      "verdict": "VALID|INVALID|SPEC_AMBIGUOUS",
      "severity": "CONSENSUS_CRITICAL|SECURITY|LOGIC_BUG|NO_ISSUE",
      "justification": "why you reached this verdict",
      "spec_reference": "EIP-XXX section",
      "recommended_action": "fix|test|document|ignore"
    }}
  ],
  "additional_security_considerations": [
    "any additional concerns..."
  ],
  "overall_summary": "summary of validation results"
}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-5.1-codex-max",
            messages=[
                {"role": "system", "content": "You are an expert security researcher validating code review findings. Respond with valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        result = json.loads(response.choices[0].message.content)
        return result
    except Exception as e:
        print(f"Error validating findings: {e}")
        return {"validation_results": [], "overall_summary": "Error during validation"}

def main():
    # 1. 获取 PR 标题
    pr_title = get_pr_title(REPO_NAME, PR_NUMBER)
    if not pr_title:
        print(f"Failed to fetch PR title for {REPO_NAME}#{PR_NUMBER}")
        return
    
    print(f"PR Title: {pr_title}")
    
    # 2. 从标题中提取 EIP 号码
    eip_numbers = extract_eip_number(pr_title)
    
    if not eip_numbers:
        print("No EIP found in PR title. This PR does not require EIP review.")
        # add a comment to the PR
        repo = gh.get_repo(REPO_NAME)
        pull = repo.get_pull(PR_NUMBER)
        pull.create_issue_comment(
            "ℹ️ No EIP numbers found in the PR title. This PR does not require EIP review."
        )
        return
    
    print(f"EIPs found: {eip_numbers}")
    
    # 3. 加载对应的 EIP 文档
    eip_specs = {}
    for eip_num in eip_numbers:
        eip_content = load_eip_document(eip_num)
        if eip_content:
            eip_specs[eip_num] = eip_content
            print(f"Loaded EIP-{eip_num}")
        else:
            print(f"Warning: EIP-{eip_num} not found in .certik/eips/")
    
    # 如果没有找到任何 EIP 文档，提示并返回
    if not eip_specs:
        print("No EIP documents found. Unable to proceed with review.")
        # add a comment to the PR
        repo = gh.get_repo(REPO_NAME)
        pull = repo.get_pull(PR_NUMBER)
        pull.create_issue_comment(
            "⚠️ Unable to find any EIP documents for the EIPs mentioned in the PR title. "
            "Please ensure the EIP files are present in the `.certik/eips/` directory."
        )
        return
    
    # 4. 解析 Diff
    if not os.path.exists(DIFF_FILE_PATH):
        print("Diff file not found.")
        return

    with open(DIFF_FILE_PATH, 'r') as f:
        diff_text = f.read()

    chunks = parse_diff(diff_text)
    
    # 5. STEP 1: 第一轮审查 - 对每个文件进行 PR review
    print("\n=== STEP 1: PR Review ===")
    all_review_findings = {}
    
    for path, content in chunks.items():
        print(f"Reviewing {path}...")
        # 过滤掉不需要审查的文件类型
        if any(path.endswith(ext) for ext in ['.md', '.txt', '.lock', '_test.go']):
            print(f"  Skipping {path} (excluded file type)")
            continue
        
        review_result = get_ai_review(path, content, eip_specs)
        all_review_findings[path] = review_result
        print(f"  Found {len(review_result.get('findings', []))} findings")
    
    # 6. STEP 2: 第二轮验证 - 对审查结果进行 validation
    print("\n=== STEP 2: Validation ===")
    all_validation_results = {}
    
    for path, review_data in all_review_findings.items():
        if not review_data.get('findings'):
            print(f"No findings to validate for {path}")
            continue
        
        print(f"Validating findings for {path}...")
        # 获取该文件的 diff 内容
        file_diff = chunks.get(path, "")
        validation_result = validate_review_findings(eip_specs, file_diff, review_data['findings'])
        all_validation_results[path] = validation_result
        print(f"  Validation complete")
    
    # 7. 构建最终的评论列表（只包含通过 validation 的 VALID 问题）
    print("\n=== STEP 3: Building Comments ===")
    all_comments = []
    validated_findings_count = 0
    
    for path, validation_data in all_validation_results.items():
        for idx, validation in enumerate(validation_data.get('validation_results', [])):
            if validation.get('verdict') == 'VALID':
                # 从原始 review findings 中获取原问题的详细信息
                original_findings = all_review_findings[path].get('findings', [])
                if idx < len(original_findings):
                    original = original_findings[idx]
                    severity = validation.get('severity', 'MEDIUM')
                    severity_emoji = {
                        'CONSENSUS_CRITICAL': '🔴',
                        'SECURITY': '🟠',
                        'LOGIC_BUG': '🟡',
                        'NO_ISSUE': '🟢'
                    }.get(severity, '⚪')
                    
                    comment_body = f"""{severity_emoji} **[VALIDATED] {severity}**
**Issue:** {original.get('issue', 'N/A')}
**Spec Reference:** {validation.get('spec_reference', 'N/A')}
**Justification:** {validation.get('justification', 'N/A')}
**Recommendation:** {validation.get('recommended_action', 'N/A')}"""
                    
                    all_comments.append({
                        "path": path,
                        "body": comment_body,
                        "side": "RIGHT"
                    })
                    validated_findings_count += 1
    
    # 8. 提交到 GitHub
    if all_comments:
        repo = gh.get_repo(REPO_NAME)
        pull = repo.get_pull(PR_NUMBER)
        
        # 提交一个整体 Review
        review_summary = f"""## EIP Compliance Review & Validation

**Process:**
1. Initial PR review against EIP specification(s)
2. Independent validation of flagged issues

**Results:** {validated_findings_count} validated issues found and reported below."""
        
        pull.create_review(
            body=review_summary,
            event="COMMENT",
            comments=all_comments
        )
        print(f"✓ Successfully posted {len(all_comments)} validated comments.")
    else:
        repo = gh.get_repo(REPO_NAME)
        pull = repo.get_pull(PR_NUMBER)
        pull.create_issue_comment(
            "✓ EIP compliance review and validation complete. No issues found."
        )
        print("✓ No issues found after validation.")

if __name__ == "__main__":
    main()