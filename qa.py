"""
华为开发者网站智能客服脚本
通过调用华为开发者官网的智能客服 API，实现交互式问答
流式输出 AI 回答
"""

import hashlib
import json
import random
import re
import string
import sys
import time

import httpx

# 强制 stdout 无缓冲，确保流式输出立即可见
sys.stdout.reconfigure(write_through=True)

BASE_URL = "https://svc-drcn.developer.huawei.com/intelligentcustomer/v1/public/dialog"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Origin": "https://developer.huawei.com",
    "Referer": "https://developer.huawei.com/",
    "Accept": "application/json, text/plain, */*",
}

SSE_HEADERS = {
    **HEADERS,
    "Accept": "text/event-stream",
    # 禁用压缩，避免解压缓冲导致延迟
    "Accept-Encoding": "identity",
}

# 打字机效果：每字符输出间隔（秒）
TYPEWRITER_DELAY = 0.016


def generate_anonymous_id():
    """生成匿名 ID（MD5 哈希 + '_search' 后缀）"""
    random_str = "".join(random.choices(string.hexdigits, k=32))
    md5 = hashlib.md5(random_str.encode()).hexdigest()
    return f"{md5}_search"


def create_dialog(anonymous_id, client):
    """创建对话，获取 dialogId"""
    url = f"{BASE_URL}/id"
    payload = {
        "anonymousId": anonymous_id,
        "type": 1001,
        "origin": 4,
    }

    resp = client.post(url, json=payload, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != 0:
        raise RuntimeError(f"创建对话失败: {data.get('message', '未知错误')}")

    dialog_id = data["result"]["dialogId"]
    return dialog_id


def submit_question(query, dialog_id, anonymous_id, client):
    """提交问题，接收 SSE 流式响应，从"生成总结"开始实时打印"""
    url = f"{BASE_URL}/submission"
    payload = {
        "query": query,
        "dialogId": dialog_id,
        "anonymousId": anonymous_id,
        "channel": 1,
        "subType": 1,
        "type": 1001,
        "origin": 4,
        "thinkType": 0,
    }

    full_answer = ""
    references = []
    streaming_started = False
    printed_len = 0
    buffer = ""

    # 使用 httpx 的流式请求 + HTTP/2，绕过 CDN 对 HTTP/1.1 的缓冲
    with client.stream("POST", url, json=payload, headers=SSE_HEADERS, timeout=120) as resp:
        resp.raise_for_status()

        # 逐字节流式读取，SSE 数据到达即处理
        for chunk in resp.iter_bytes():
            if not chunk:
                continue
            buffer += chunk.decode("utf-8", errors="replace")

            # 按 SSE 事件边界解析（双换行分隔）
            while "\n\n" in buffer:
                event_text, buffer = buffer.split("\n\n", 1)

                for line in event_text.split("\n"):
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

                    # 检测"生成总结"阶段，开始流式打印
                    if not streaming_started:
                        step_info = result.get("stepInfo", "")
                        if "生成总结" in step_info:
                            streaming_started = True

                    # 累积流式文本
                    streaming_text = result.get("streamingText", "")
                    if streaming_text:
                        clean_text = re.sub(r"<rsup[^>]*>.*?</rsup>", "", streaming_text)
                        full_answer = streaming_text

                        # 打字机效果：逐字符打印增量部分
                        if streaming_started and len(clean_text) > printed_len:
                            delta = clean_text[printed_len:]
                            for ch in delta:
                                sys.stdout.write(ch)
                                sys.stdout.flush()
                                time.sleep(TYPEWRITER_DELAY)
                            printed_len = len(clean_text)

                    # 提取参考来源
                    refs = result.get("resultReferences", [])
                    if refs:
                        references = refs

                    # 回答完毕
                    if result.get("isFinal"):
                        if streaming_started:
                            print()
                        return full_answer, references

    # 流意外结束
    if streaming_started:
        print()

    return full_answer, references


def clean_answer(text):
    """清理回答文本中的 XML 标签"""
    text = re.sub(r"<rsup[^>]*>.*?</rsup>", "", text)
    return text.strip()


def ask(query, client=None, anonymous_id=None):
    """提问并获取回答"""
    if client is None:
        # 启用 HTTP/2，这是流式输出的关键！
        # CDN/反向代理通常对 HTTP/1.1 的 SSE 流做整段缓冲，
        # 而对 HTTP/2 直接透传，浏览器用 HTTP/2 所以流式正常。
        client = httpx.Client(http2=True)
    if anonymous_id is None:
        anonymous_id = generate_anonymous_id()

    dialog_id = create_dialog(anonymous_id, client)
    answer, references = submit_question(query, dialog_id, anonymous_id, client)
    return answer, references


def main():

    # 命令行直接传入问题
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        client = httpx.Client(http2=True)
        anonymous_id = generate_anonymous_id()

        print(f"\n问题：{question}\n")
        try:
            answer, references = ask(question, client, anonymous_id)
        except Exception as e:
            print(f"\n请求失败: {e}")
            sys.exit(1)

        if references:
            print("\n【参考来源】")
            for i, ref in enumerate(references, 1):
                print(f"  [{i}] {ref.get('title', '')}")
                url = ref.get("url", "")
                if url:
                    print(f"      {url}")

        client.close()
        sys.exit(0)

    # 交互模式
    client = httpx.Client(http2=True)
    anonymous_id = generate_anonymous_id()

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

        try:
            answer, references = ask(question, client, anonymous_id)
        except Exception as e:
            print(f"\n请求失败: {e}")
            continue

        if references:
            print("\n【参考来源】")
            for i, ref in enumerate(references, 1):
                print(f"  [{i}] {ref.get('title', '')}")
                url = ref.get("url", "")
                if url:
                    print(f"      {url}")

        print()

    client.close()


if __name__ == "__main__":
    main()
