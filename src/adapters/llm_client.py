# llm_client.py
"""LLM 生成接口封装：调用 DashScope Qwen，用于基于检索结果生成新方案内容。"""

import json
import time

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
import os

# 默认使用的模型。qwen3.7-max 是性价比和能力的均衡档，先用它跑通整条链路；
# 如果后面发现生成质量不够，换成 qwen-max 只需要改这一个字符串。
# 模型列表：https://help.aliyun.com/zh/model-studio/getting-started/models
_DEFAULT_MODEL = "qwen3.7-max"

# 单次请求失败时的最大重试次数，兜底偶发的网络抖动 / 限流
_MAX_RETRIES = 3

# 重试前的等待时间（秒）
_RETRY_SLEEP_SECONDS = 2.0

# 生成 slide context 摘要 + metadata 用的系统提示词。强制要求只输出 JSON，
# 不输出别的文字，方便下游直接解析。
_CONTEXT_METADATA_SYSTEM_PROMPT = (
    "你是电气工程方案库的信息整理助手。给定一份历史方案 PPT 中某一张幻灯片的"
    "标题和正文，以及整份方案的幻灯片标题大纲，请判断这张幻灯片在方案中的"
    "位置，以及这份方案的业务信息。只输出一个 JSON 对象，不要输出任何其他"
    "文字、不要用 markdown 代码块包裹，格式如下：\n"
    '{"context_summary": "一句话说明这张幻灯片属于哪份方案、大致章节位置", '
    '"proposal_type": "方案类型，如配电/光伏/储能/自动化等，无法判断填空字符串", '
    '"client_industry": "客户所属行业，无法判断填空字符串"}'
)

# query 改写用的系统提示词，同样强制只输出 JSON
_QUERY_REWRITE_SYSTEM_PROMPT = (
    "你是电气工程方案库的检索助手。给定用户想要生成新方案的需求描述，"
    "请把它改写成 1-3 个更适合语义检索的查询语句（覆盖需求里不同的关键"
    "角度），并且如果能从描述里判断出方案类型（如配电/光伏/储能/自动化"
    "等），一并给出，无法判断则留空字符串。只输出一个 JSON 对象，不要"
    "输出任何其他文字、不要用 markdown 代码块包裹，格式如下：\n"
    '{"queries": ["查询1", "查询2"], "proposal_type": "方案类型或空字符串"}'
)


class LLMClient:
    """调用 DashScope Qwen 生成文本，与 vector_store.py 共用同一套 DashScope 账号。"""

    def __init__(self, model: str = _DEFAULT_MODEL) -> None:
        # DashScope 兼容 OpenAI 接口，直接用 openai 包调用，
        # 跟 vector_store.py 里的 DashScopeEmbeddingFunction 是同一种调用方式
        self._client = OpenAI(
            api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            timeout=120.0,
        )
        self._model = model

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        """把 prompt 发给模型，返回生成的文本。

        这是最基础的一层：只负责"发请求、拿回复"，不涉及检索结果怎么拼进
        prompt、也不涉及输出格式的后处理——那些逻辑留在 main.py 里组装，
        保持这一层职责单一，方便以后换模型或者换供应商。

        Args:
            prompt: 用户侧的完整输入内容（检索结果 + 需求描述等都在这里拼好）
            system_prompt: 可选的系统提示词，用来设定角色和输出规范；不传则
                走模型默认行为

        Returns:
            模型生成的文本内容
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        return self._call_with_retry(messages)

    def rewrite_query(self, need_description: str) -> dict:
        """把用户的方案需求描述改写成 1-3 个检索用 query，并尝试判断方案类型。

        对应 claude.md 检索流程第 1 步。用户口语化的需求描述和历史 slide
        里的专业表述经常对不上，改写成更贴近素材措辞的查询能提高检索命中率；
        顺带判断方案类型是为了给检索的 metadata 过滤用（相当于低成本的
        大纲引导式检索），判断不出来就留空，不强行过滤。

        Args:
            need_description: 用户描述的新方案需求（自然语言）

        Returns:
            {"queries": list[str], "proposal_type": str}
            解析失败时退化为 {"queries": [need_description], "proposal_type": ""}——
            也就是直接拿原始需求描述去检索，不影响后续流程继续跑
        """
        raw = self.generate(need_description, system_prompt=_QUERY_REWRITE_SYSTEM_PROMPT)
        return self._parse_query_rewrite(raw, fallback_query=need_description)

    @staticmethod
    def _parse_query_rewrite(raw: str, fallback_query: str) -> dict:
        """解析 query 改写结果，失败时退化为直接用原始需求描述检索。"""
        fallback = {"queries": [fallback_query], "proposal_type": ""}
        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.strip("`")
                cleaned = cleaned.removeprefix("json").strip()
            data = json.loads(cleaned)
            queries = data.get("queries", [])
            if not isinstance(queries, list) or not queries:
                return fallback
            return {
                "queries": [str(q) for q in queries][:3],
                "proposal_type": str(data.get("proposal_type", "")),
            }
        except (json.JSONDecodeError, AttributeError):
            print(
                f"  警告：query 改写结果解析失败，退化为直接检索，"
                f"原始返回：{raw[:100]!r}"
            )
            return fallback

    def generate_slide_context(
        self,
        slide: dict,
        outline_titles: list[str],
    ) -> dict:
        """为单张 slide 生成 context 摘要 + metadata，用于入库前 embedding 增强。

        对应 claude.md 里"检索策略设计"确定的方案：每张 slide 单独调用一次
        LLM，不做文档级摊薄；只传标题大纲而不是全文，控制 token 成本。

        Args:
            slide: 当前要处理的 slide（SlideDict，用它的 title/content/
                source_file/slide_number）
            outline_titles: 这份 pptx 全部 slide 的标题列表（按 slide 顺序），
                作为低成本的"文档大纲"上下文，帮助模型判断当前 slide 在
                方案里的大致章节位置

        Returns:
            {"context_summary": str, "proposal_type": str, "client_industry": str}
            解析失败时三个字段都返回空字符串，不抛异常——这一步生成的是
            增强信息，不是必需数据，失败了不该打断整个 ingest 流程
        """
        outline_text = "\n".join(
            f"{i + 1}. {t}" for i, t in enumerate(outline_titles) if t
        )
        user_prompt = (
            f"方案文件名：{slide['source_file']}\n"
            f"方案大纲（全部幻灯片标题）：\n{outline_text}\n\n"
            f"当前幻灯片（第 {slide['slide_number']} 页）标题：{slide['title']}\n"
            f"当前幻灯片正文：\n{slide['content']}"
        )

        raw = self.generate(user_prompt, system_prompt=_CONTEXT_METADATA_SYSTEM_PROMPT)
        return self._parse_context_metadata(raw)

    @staticmethod
    def _parse_context_metadata(raw: str) -> dict:
        """解析模型返回的 JSON，失败时给空字符串兜底而不是抛异常。"""
        fallback = {"context_summary": "", "proposal_type": "", "client_industry": ""}
        try:
            # 模型偶尔会用 ```json 代码块包裹，尽管 prompt 里明确要求了不要
            # 这么做，这里还是稳妥地剥一层再解析
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.strip("`")
                cleaned = cleaned.removeprefix("json").strip()
            data = json.loads(cleaned)
            return {
                "context_summary": str(data.get("context_summary", "")),
                "proposal_type": str(data.get("proposal_type", "")),
                "client_industry": str(data.get("client_industry", "")),
            }
        except (json.JSONDecodeError, AttributeError):
            print(
                f"  警告：context/metadata 生成结果解析失败，跳过增强，"
                f"原始返回：{raw[:100]!r}"
            )
            return fallback

    def _call_with_retry(self, messages: list[dict]) -> str:
        last_error: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                )
                return resp.choices[0].message.content or ""
            
            except (APITimeoutError, APIConnectionError) as e:
                last_error = e
                if attempt == _MAX_RETRIES:
                    raise
                print(f"  LLM 请求失败（第 {attempt} 次，{type(e).__name__}），{_RETRY_SLEEP_SECONDS}s 后重试...")
                time.sleep(_RETRY_SLEEP_SECONDS)

            except APIStatusError as e:
                last_error = e
                is_retryable = e.status_code == 429 or e.status_code >= 500
                if not is_retryable or attempt == _MAX_RETRIES:
                    raise
                print(
                    f"  LLM 请求失败（第 {attempt} 次，status={e.status_code}），"
                    f"{_RETRY_SLEEP_SECONDS}s 后重试..."
                )
                time.sleep(_RETRY_SLEEP_SECONDS)

        raise last_error if last_error else RuntimeError("LLM 请求未知失败")