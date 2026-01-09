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

def get_ai_review(file_path, diff_content):
    """
    调用 AI 进行代码评审
    """
    prompt = f"""
    你是一个资深的程序员，正在审核代码变更。
    请针对以下 Git Diff 内容进行评审。

    文件路径: {file_path}
    
    评审要求:
    1. 发现潜在的 Bug、性能问题、安全隐患或不符合最佳实践的代码。
    2. 如果代码没有问题，返回空列表。
    3. 必须返回 JSON 格式，且包含一个名为 'reviews' 的数组。
    
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
    if not os.path.exists(DIFF_FILE_PATH):
        print("Diff file not found.")
        return

    with open(DIFF_FILE_PATH, 'r') as f:
        diff_text = f.read()

    # 1. 解析 Diff
    chunks = parse_diff(diff_text)
    all_comments = []

    # 2. 逐个文件分析
    for path, content in chunks.items():
        print(f"Analyzing {path}...")
        # 过滤掉不需要评审的文件类型
        if any(path.endswith(ext) for ext in ['.md', '.txt', '.lock']):
            continue
            
        reviews = get_ai_review(path, content)
        for r in reviews:
            all_comments.append({
                "path": path,
                "line": int(r['line']),
                "body": f"🤖 **AI Reviewer:** {r['suggestion']}",
                "side": "RIGHT"
            })

    # 3. 提交到 GitHub
    if all_comments:
        repo = gh.get_repo(REPO_NAME)
        pull = repo.get_pull(PR_NUMBER)
        
        # 提交一个整体 Review
        pull.create_review(
            body="我已完成代码自动评审，发现以下几个可以改进的地方：",
            event="COMMENT",
            comments=all_comments
        )
        print(f"Successfully posted {len(all_comments)} comments.")
    else:
        print("No issues found by AI.")

if __name__ == "__main__":
    main()