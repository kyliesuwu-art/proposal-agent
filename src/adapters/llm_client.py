# llm_client.py
"""LLM 生成接口封装：调用 DashScope Qwen，用于基于检索结果生成新方案内容。"""
import base64
from pathlib import Path

import json
import time

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
import os

from src.config import dashscope_settings

# 默认使用的模型。qwen3.7-plus  是性价比和能力的均衡档，先用它跑通整条链路；
# 如果后面发现生成质量不够，换成 qwen-max 只需要改这一个字符串。
# qwen3.8-max-preview 当前为预览版本，预览期间模型能力会持续迭代升级。预览结束后该模型会下线或替换成正式版本。
# 模型列表：https://help.aliyun.com/zh/model-studio/getting-started/models
_DEFAULT_MODEL = "qwen3.7-plus"

# 图片理解专用的视觉模型，跟 _DEFAULT_MODEL 是两条独立的链路：
# 文字生成用 _DEFAULT_MODEL，看图用这个，互不干扰
_VISION_MODEL = "qwen3-vl-plus"

# 单次请求失败时的最大重试次数，兜底偶发的网络抖动 / 限流
_MAX_RETRIES = 2
_CONNECT_TIMEOUT_SECONDS = 15.0

# 重试前的等待时间（秒）
_RETRY_SLEEP_SECONDS = 2.0


def _redact_secret_text(value: str) -> str:
    """Keep diagnostic text useful without allowing an API key to escape logs."""
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if key:
        value = value.replace(key, "[REDACTED]")
    return value

# 生成 slide context 摘要 + metadata 用的系统提示词。强制要求只输出 JSON，
# 不输出别的文字，方便下游直接解析。
#
# 注：proposal_type / client_industry 目前每张 slide 各自独立判断一次，
# 同一份方案的不同 slide 理论上可能被判成不同类型。这是架构层面的取舍，
# 如果后续发现同一方案内类型不一致的情况变多，再考虑改成文档级缓存复用。
_CONTEXT_METADATA_SYSTEM_PROMPT = (
    "你是电气工程方案库的信息整理助手。给定一份历史方案 PPT 中某一张幻灯片的"
    "标题和正文，以及整份方案的幻灯片标题大纲，请判断这张幻灯片在方案中的"
    "位置，以及这份方案的业务信息。\n"
    "context_summary：用 30-60 字概括这张幻灯片在方案中处于什么阶段/章节，"
    "主要讲的是什么内容。\n"
    "proposal_type：请从以下列表中选择最贴切的一个：配电、光伏、储能、自动化、"
    "综合能源/零碳园区、电力交易、智能微电网、电能质量/无功补偿、用电信息"
    "采集/计量、变电站/开关站、其他。如果这份方案大纲中确实综合了多个类型，"
    "选择这张幻灯片所属章节对应的那一个；实在无法判断填空字符串。\n"
    "client_industry：请尽量从以下列表中选择：政府/园区、工业制造、数据中心、"
    "交通、医院、教育、商业地产、电网公司、其他。无法判断填空字符串。\n"
    "只输出一个 JSON 对象，不要输出任何其他文字、不要用 markdown 代码块包裹，"
    "格式如下：\n"
    '{"context_summary": "30-60字的章节位置与内容概括", '
    '"proposal_type": "上述列表中的一个或空字符串", '
    '"client_industry": "上述列表中的一个或空字符串"}'
)

# query 改写用的系统提示词，同样强制只输出 JSON
_QUERY_REWRITE_SYSTEM_PROMPT = (
    "你是电气工程方案库的检索助手。给定用户想要生成新方案的需求描述，"
    "请把它改写成 1-3 个更适合语义检索的查询语句（覆盖需求里不同的关键"
    "角度，例如技术方案角度、客户场景角度、设备/规格角度等，不要三个查询"
    "都是同一句话的简单变形）。\n"
    "并且如果能从描述里判断出方案类型，请从以下列表中选择最贴切的一个："
    "配电、光伏、储能、自动化、综合能源/零碳园区、电力交易、智能微电网、"
    "电能质量/无功补偿、用电信息采集/计量、变电站/开关站、其他；无法判断"
    "则留空字符串。\n"
    "只输出一个 JSON 对象，不要输出任何其他文字、不要用 markdown 代码块包裹，"
    "格式如下：\n"
    '{"queries": ["查询1", "查询2"], "proposal_type": "上述列表中的一个或空字符串"}'
)

_IMAGE_CAPTION_SYSTEM_PROMPT = (
    "你是电气工程方案库的图片理解助手。请用一句话客观描述这张图片的内容。\n"
    "如果图片是系统拓扑图/接线图/设备照片/参数表格等，请在这句话里明确点出"
    "关键设备名称、型号规格（如有）、拓扑结构或流程环节，方便后续按关键词检索；"
    "如果是纯装饰性配图或看不出具体信息，客观描述画面内容即可，不要编造设备信息。\n"
    "不要输出与描述无关的文字，不要以“这张图片”开头，直接描述内容本身。"
)




class LLMClient:
    """调用 DashScope Qwen 生成文本，与 vector_store.py 共用同一套 DashScope 账号。"""

    def __init__(self, model: str | None = None, vision_model: str = _VISION_MODEL) -> None:
        # DashScope 兼容 OpenAI 接口，直接用 openai 包调用，
        # 跟 vector_store.py 里的 DashScopeEmbeddingFunction 是同一种调用方式
        settings = dashscope_settings()
        self._base_url = settings["base_url"]
        self._timeout_seconds = float(settings["timeout_seconds"])
        self._client = OpenAI(
            api_key=settings["api_key"],
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout_seconds, connect=_CONNECT_TIMEOUT_SECONDS),
            max_retries=0,
        )
        self._model = model or settings["model"] or _DEFAULT_MODEL
        self._vision_model = vision_model

    @property
    def connection_settings(self) -> dict[str, object]:
        """Safe diagnostics: deliberately excludes credentials and request headers."""
        return {
            "endpoint": self._base_url,
            "model": self._model,
            "timeout_seconds": self._timeout_seconds,
        }

    @staticmethod
    def connection_error_diagnostics(exc: BaseException) -> dict[str, object]:
        """Preserve the exception chain while redacting accidental credential echoes."""
        chain: list[dict[str, str]] = []
        current: BaseException | None = exc
        while current is not None:
            chain.append({"type": type(current).__name__, "repr": _redact_secret_text(repr(current))})
            current = current.__cause__ or current.__context__
        return {"error_type": type(exc).__name__, "repr": _redact_secret_text(repr(exc)), "cause_chain": chain}

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

    def generate_once(self, prompt: str, system_prompt: str = "") -> str:
        """Issue exactly one text-generation request; no retry for metered acceptance runs."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        try:
            resp = self._client.chat.completions.create(model=self._model, messages=messages)
        except APIConnectionError as exc:
            diagnostic = {**self.connection_settings, **self.connection_error_diagnostics(exc)}
            # This is intentionally safe to print in a CLI acceptance run: no key or headers.
            print(f"LLM connection diagnostic: {json.dumps(diagnostic, ensure_ascii=False)}")
            raise
        return resp.choices[0].message.content or ""

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
    

    def generate_image_captions(self, slide: dict) -> list[str]:
        """为 slide 里的所有图片依次生成一句话 caption。

           对 slide["images"] 逐张调用 _generate_single_image_caption；
           某张图片生成失败时该项返回空字符串，不抛异常，不影响其他图片继续处理。

           Args:
            slide: 当前 slide dict，用它的 images 字段（每项含 "path"）

           Returns:
           与 slide["images"] 一一对应（按下标）的 caption 字符串列表
        """
        captions = []
        for img in slide.get("images", []):
            try:
                caption = self._generate_single_image_caption(img["path"], slide)
                captions.append(caption)
            except Exception as e:
                print(f"  警告：图片 {img['path']} caption 生成失败，跳过，原因：{e}")
                captions.append("")
        return captions

    
    def _generate_single_image_caption(self, image_path: str, slide: dict) -> str:
        """对单张图片调用视觉模型，返回一句话 caption。
        图片是本地文件（parser 存的是本地路径），不是可公开访问的 URL，
        所以读文件转 base64、拼成 data URL 传给模型，而不是传 URL。
        """
        path_obj = Path(image_path)
        if not path_obj.exists():
            raise FileNotFoundError(f"图片文件不存在: {image_path}")
        with open(path_obj, "rb") as f:
            b64_data = base64.b64encode(f.read()).decode("utf-8")
        
        ext = path_obj.suffix.lstrip(".").lower() or "jpeg"
        data_url = f"data:image/{ext};base64,{b64_data}"

        messages = [
            {"role": "system", "content": _IMAGE_CAPTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {
                        "type": "text",
                        "text": (
                            f"这张图片出自方案《{slide['source_file']}》"
                            f"第 {slide['slide_number']} 页，标题：{slide['title'] or '无'}。"
                            f"请用一句话描述这张图片的内容。"
                        ),
                    },
                ],
            },
        ]   
        return self._call_with_retry(messages, model=self._vision_model).strip()
    
    

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

    def _call_with_retry(self, messages: list[dict], model: str | None = None) -> str:
        last_error: Exception | None = None
        target_model = model or self._model

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=target_model,
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
