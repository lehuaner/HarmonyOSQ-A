"""
HarmonyOS Q&A 脚本
基于抓包数据_解析结果中的内容，回答用户关于 HarmonyOS 开发的问题
"""

import json
import os
import re
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent / "抓包数据_解析结果"


def load_all_responses():
    """加载所有解析结果中的响应体内容"""
    results = []

    if not DATA_DIR.exists():
        print(f"错误：数据目录不存在 - {DATA_DIR}")
        return results

    for dirpath in sorted(DATA_DIR.iterdir()):
        if not dirpath.is_dir():
            continue

        # 读取响应体 JSON
        json_file = dirpath / "响应体.json"
        if json_file.exists():
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                results.append({
                    "source": dirpath.name,
                    "type": "json",
                    "data": data,
                })
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        # 读取响应体 HTML
        html_file = dirpath / "响应体.html"
        if html_file.exists():
            try:
                with open(html_file, "r", encoding="utf-8") as f:
                    content = f.read()
                results.append({
                    "source": dirpath.name,
                    "type": "html",
                    "data": content,
                })
            except UnicodeDecodeError:
                pass

        # 读取响应 TXT（含 SSE 流式数据）
        txt_file = dirpath / "响应.txt"
        if txt_file.exists():
            try:
                with open(txt_file, "r", encoding="utf-8") as f:
                    content = f.read()
                results.append({
                    "source": dirpath.name,
                    "type": "txt",
                    "data": content,
                })
            except UnicodeDecodeError:
                pass

    return results


def extract_intelligent_customer_answer(responses):
    """从智能客服的流式响应中提取完整回答"""
    full_answer = ""
    references = []

    for item in responses:
        if item["type"] != "txt":
            continue
        if "intelligentcustomer" not in item["source"]:
            continue

        content = item["data"]
        # 解析 SSE 格式的流式数据
        for line in content.split("\n"):
            line = line.strip()
            if not line.startswith("data:"):
                continue

            json_str = line[5:].strip()
            if not json_str:
                continue

            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                continue

            result = data.get("result", {})
            streaming_text = result.get("streamingText", "")
            if streaming_text:
                full_answer = streaming_text  # 每个 chunk 包含累积文本，取最新的

            refs = result.get("resultReferences", [])
            if refs:
                references = refs

    return full_answer, references


def search_in_json_data(responses, keywords):
    """在 JSON 响应数据中搜索关键词相关内容"""
    matches = []

    for item in responses:
        if item["type"] != "json":
            continue

        text = json.dumps(item["data"], ensure_ascii=False)

        # 计算关键词命中数
        hit_count = sum(1 for kw in keywords if kw.lower() in text.lower())
        if hit_count > 0:
            matches.append({
                "source": item["source"],
                "hit_count": hit_count,
                "data": item["data"],
            })

    # 按命中数排序
    matches.sort(key=lambda x: x["hit_count"], reverse=True)
    return matches


def extract_search_highlights(matches, keywords):
    """从搜索结果中提取高亮信息摘要"""
    summaries = []

    for match in matches[:5]:  # 最多取前5条
        data = match["data"]

        # 处理搜索结果
        search_result = data.get("searchResult", [])
        for sr in search_result:
            dev_infos = sr.get("developerInfos", [])
            for info in dev_infos:
                title = info.get("name", "")
                url = info.get("url", "")
                desc = info.get("description", "")

                highlights = info.get("highlightInfos", [])
                highlight_texts = []
                for h in highlights:
                    h_content = h.get("content", "")
                    # 去掉 HTML 标签
                    clean = re.sub(r"<[^>]+>", "", h_content)
                    # 截取包含关键词的片段
                    for kw in keywords:
                        idx = clean.lower().find(kw.lower())
                        if idx != -1:
                            start = max(0, idx - 50)
                            end = min(len(clean), idx + len(kw) + 200)
                            snippet = clean[start:end]
                            if start > 0:
                                snippet = "..." + snippet
                            if end < len(clean):
                                snippet = snippet + "..."
                            highlight_texts.append(snippet)

                if title or highlight_texts:
                    summaries.append({
                        "title": title,
                        "url": "https:" + url if url.startswith("//") else url,
                        "highlights": highlight_texts[:2],
                    })

    return summaries


def answer_question(question):
    """根据抓包数据回答问题"""
    print(f"\n问题：{question}\n")
    print("=" * 70)

    # 加载所有响应数据
    responses = load_all_responses()
    if not responses:
        print("未找到任何抓包数据，请确认 抓包数据_解析结果 目录存在且不为空。")
        return

    # 提取关键词
    keywords = [w for w in re.split(r"[，。？？\s\"\"\"+\-+/()（）]", question) if len(w) >= 2]
    if not keywords:
        keywords = [question]

    # 1. 优先从智能客服回答中查找
    answer, references = extract_intelligent_customer_answer(responses)
    if answer:
        # 检查回答是否与问题相关（至少命中一个关键词）
        relevant = any(kw.lower() in answer.lower() for kw in keywords)
        if relevant:
            print("【智能客服回答】\n")
            # 去掉 XML 标签
            clean_answer = re.sub(r"<rsup[^>]*>.*?</rsup>", "", answer)
            clean_answer = clean_answer.strip()
            print(clean_answer)

            if references:
                print("\n【参考来源】")
                for i, ref in enumerate(references, 1):
                    print(f"  [{i}] {ref.get('title', '')}")
                    url = ref.get("url", "")
                    if url:
                        print(f"      {url}")

            print()
            return

    # 2. 从搜索结果中查找
    matches = search_in_json_data(responses, keywords)
    if matches:
        summaries = extract_search_highlights(matches, keywords)
        if summaries:
            print("【搜索结果摘要】\n")
            for i, s in enumerate(summaries, 1):
                print(f"  {i}. {s['title']}")
                print(f"     链接：{s['url']}")
                for h in s["highlights"]:
                    print(f"     > {h}")
                print()
            return

    # 3. 未找到相关信息
    print("抱歉，在抓包数据中未找到与该问题相关的信息。")
    print("建议访问华为开发者官网搜索：https://developer.huawei.com/consumer/cn/")


def main():
    print("=" * 70)
    print("  HarmonyOS Q&A - 基于抓包数据的问答系统")
    print("  输入问题获取答案，输入 q 退出")
    print("=" * 70)

    if len(sys.argv) > 1:
        # 命令行直接传入问题
        question = " ".join(sys.argv[1:])
        answer_question(question)
        return

    while True:
        print()
        try:
            question = input("请输入问题：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not question:
            continue
        if question.lower() in ("q", "quit", "exit"):
            print("再见！")
            break

        answer_question(question)


if __name__ == "__main__":
    main()
