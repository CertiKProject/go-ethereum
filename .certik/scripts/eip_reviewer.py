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
EIP_DIR = os.path.join(os.path.dirname(__file__), "..", "eips")

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

def get_ai_review(file_path, diff_content, eip_context=""):
    """
    调用 AI 进行代码评审
    eip_context: 如果有 EIP 文档，会被添加到提示中
    """
    eip_section = ""
    if eip_context:
        eip_section = f"\n\n相关的 EIP 文档:\n{eip_context}\n"
    
    prompt = f"""
    你是一个资深的程序员，正在审核代码变更。
    请针对以下 Git Diff 内容进行评审。

    文件路径: {file_path}
    {eip_section}
    评审要求:
    1. 发现潜在的 Bug、性能问题、安全隐患或不符合最佳实践的代码。
    2. 如果有 EIP 上下文，请确保代码实现是否符合 EIP 的要求。
    3. 如果代码没有问题，返回空列表。
    4. 必须返回 JSON 格式，且包含一个名为 'reviews' 的数组。
    
    JSON 示例结构:
    {{
      "reviews": [
        {{
          "line": 15,
          "suggestion": "这里可能存在空指针异常，建议增加非空判断。"
        }}
      ]
    }}

    待评审 Diff:
    {diff_content}
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini", # 建议生产环境用 gpt-4o
            messages=[{"role": "system", "content": "你是一个只输出 JSON 的代码评审专家。"},
                      {"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        result = json.loads(response.choices[0].message.content)
        return result.get("reviews", [])
    except Exception as e:
        print(f"Error calling AI for {file_path}: {e}")
        return []

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
    eip_contexts = {}
    for eip_num in eip_numbers:
        eip_content = load_eip_document(eip_num)
        if eip_content:
            eip_contexts[eip_num] = eip_content
            print(f"Loaded EIP-{eip_num}")
        else:
            print(f"Warning: EIP-{eip_num} not found in .certik/eips/")
    
    # 如果没有找到任何 EIP 文档，提示并返回
    if not eip_contexts:
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
    all_comments = []

    # 5. 合并所有 EIP 内容作为上下文
    combined_eip_context = "\n\n".join([
        f"=== EIP-{eip_num} ===\n{content}"
        for eip_num, content in eip_contexts.items()
    ])

    # 6. 逐个文件分析
    for path, content in chunks.items():
        print(f"Analyzing {path}...")
        # 过滤掉不需要评审的文件类型
        if any(path.endswith(ext) for ext in ['.md', '.txt', '.lock', '_test.go']):
            continue
            
        reviews = get_ai_review(path, content, combined_eip_context)
        for r in reviews:
            all_comments.append({
                "path": path,
                "line": int(r['line']),
                "body": f"🤖 **AI Reviewer:** {r['suggestion']}",
                "side": "RIGHT"
            })

    # 7. 提交到 GitHub
    if all_comments:
        repo = gh.get_repo(REPO_NAME)
        pull = repo.get_pull(PR_NUMBER)
        
        # 提交一个整体 Review
        pull.create_review(
            body="I've compared the original EIP and reviewed the changes using AI. Here are my comments:",
            event="COMMENT",
            comments=all_comments
        )
        print(f"Successfully posted {len(all_comments)} comments.")
    else:
        print("No issues found by AI.")

if __name__ == "__main__":
    main()