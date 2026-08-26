# deepeval_llm.py
"""DeepEval 自定义 LLM 适配层：封装 DashScope Qwen，复用项目现有的 API 配置。"""

import os
from typing import Any

from deepeval.models.base_model import DeepEvalBaseLLM
from openai import OpenAI


class QwenDashScopeModel(DeepEvalBaseLLM):
    """将 DashScope Qwen 接入 DeepEval 的自定义 LLM 类。

    复用项目现有的 DASHSCOPE_API_KEY 和 DASHSCOPE_BASE_URL 环境变量，
    不需要额外配置。默认使用 qwen3.7-plus 模型，可按需更换。
    """

    def __init__(
        self,
        model: str = "qwen3.7-plus",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> None:
        """初始化 DashScope Qwen 模型。

        Args:
            model: 模型名称，默认 qwen3.7-plus
            temperature: 生成温度，默认 0.7
            max_tokens: 最大生成 token 数，默认 4096
        """
        # 先初始化 OpenAI client，因为父类 __init__ 会调用 load_model()
        self._client = OpenAI(
            api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
            base_url=os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        )
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        super().__init__()

    def load_model(self) -> Any:
        """返回底层客户端对象。"""
        return self._client

    def get_model_name(self) -> str:
        """返回模型名称标识，用于 DeepEval 日志和报告。"""
        return f"DashScope:{self._model}"

    def generate(self, prompt: str) -> str:
        """调用 DashScope API 生成文本，DeepEval 指标评估的核心入口。

        Args:
            prompt: 完整输入提示词（含系统提示+用户内容）

        Returns:
            模型生成的文本字符串
        """
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        return response.choices[0].message.content

    async def a_generate(self, prompt: str) -> str:
        """异步版本的 generate，DeepEval 批量评估时使用。"""
        import asyncio

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.generate, prompt)

    def supports_temperature(self) -> bool:
        return True

    def supports_json_mode(self) -> bool:
        """qwen3.7-plus 支持 JSON 输出模式。"""
        return True

    def supports_structured_outputs(self) -> bool:
        return True
