# -*- coding: utf-8 -*-
"""
@File    : services/llm_service.py
@Desc    : 本地大语言模型服务层：封装对 Ollama 的 HTTP 请求，支持普通和流式两种调用模式
"""

import requests
import json
from loguru import logger
from typing import Optional, Generator

from app.core.config import OLLAMA_API_URL, OLLAMA_MODEL_NAME


class OllamaLLMService:
    """
    Ollama 本地大语言模型服务类。
    通过 HTTP 请求调用本地运行的 Ollama 服务，实现完全离线的大模型推理。
    支持普通（一次性返回）和流式（逐 token 返回）两种模式。
    """

    def __init__(
        self,
        api_url: str = OLLAMA_API_URL,
        model_name: str = OLLAMA_MODEL_NAME,
        timeout: int = 180
    ):
        self.api_url = api_url
        self.model_name = model_name
        self.timeout = timeout
        logger.info(f"OllamaLLMService 初始化完成: url={api_url}, model={model_name}")

    def generate(self, prompt: str, options: Optional[dict] = None) -> str:
        """普通模式：等待完整响应后一次性返回文本"""
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False
        }
        if options:
            options = dict(options)
            response_format = options.pop("format", None)
            if response_format:
                payload["format"] = response_format
            if options:
                payload["options"] = options

        logger.info(f"Ollama 普通推理: model={self.model_name}, prompt_len={len(prompt)}")
        try:
            resp = requests.post(
                url=self.api_url,
                json=payload,
                timeout=self.timeout,
                headers={"Content-Type": "application/json"}
            )
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(
                f"无法连接到 Ollama 服务 ({self.api_url})。"
                f"请确认 Ollama 已启动，并且模型 '{self.model_name}' 已下载。错误: {e}"
            ) from e
        except requests.exceptions.Timeout as e:
            raise TimeoutError(f"Ollama 推理超时（>{self.timeout}s）: {e}") from e
        except requests.exceptions.HTTPError as e:
            raise ValueError(f"Ollama API HTTP 错误: {resp.status_code} - {resp.text}") from e

        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise ValueError(f"无法解析 Ollama 响应 JSON: {resp.text}") from e

        text = data.get("response", "")
        if not text:
            logger.warning(f"Ollama 返回空 response，完整响应: {data}")
            return "模型未生成有效建议，请检查模型状态。"
        logger.success(f"Ollama 普通推理完成，生成 {len(text)} 字符")
        return text.strip()

    def generate_stream(self, prompt: str, options: Optional[dict] = None) -> Generator[str, None, None]:
        """
        流式模式：逐 token 返回生成内容，用于 SSE 流式输出。

        Yields:
            每次 Ollama 返回的 token 文本片段（字符串）
        """
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": True  # 开启流式模式
        }
        if options:
            payload["options"] = options

        logger.info(f"Ollama 流式推理: model={self.model_name}, prompt_len={len(prompt)}")
        try:
            with requests.post(
                url=self.api_url,
                json=payload,
                timeout=self.timeout,
                headers={"Content-Type": "application/json"},
                stream=True  # requests 也需要开启流式
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line.decode("utf-8"))
                        token = chunk.get("response", "")
                        if token:
                            yield token
                        # Ollama 流式结束标志
                        if chunk.get("done", False):
                            logger.success("Ollama 流式推理完成")
                            break
                    except json.JSONDecodeError:
                        continue
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(
                f"无法连接到 Ollama 服务 ({self.api_url})。"
                f"请确认 Ollama 已启动，并且模型 '{self.model_name}' 已下载。错误: {e}"
            ) from e
        except requests.exceptions.Timeout as e:
            raise TimeoutError(f"Ollama 流式推理超时（>{self.timeout}s）: {e}") from e

    def health_check(self) -> bool:
        """检查 Ollama 服务是否可用"""
        try:
            health_url = self.api_url.replace("/api/generate", "")
            resp = requests.get(health_url, timeout=5)
            return resp.status_code == 200
        except Exception:
            return False
