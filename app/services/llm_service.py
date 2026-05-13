# -*- coding: utf-8 -*-
"""
@File    : services/llm_service.py
@Desc    : 大语言模型服务层 —— 多 Provider 抽象

支持两种 provider：

  1) ollama       本地 Ollama (默认)
                  适用场景：单机部署，CPU/小显存机器，离线优先
                  端点：    POST {base}/api/generate

  2) openai       任何 OpenAI 兼容协议的 API
                  适用场景：机房 GPU 服务器 vLLM / TGI / SGLang，
                            llama.cpp server, LM Studio,
                            DeepSeek / Qwen / 智谱 等云 API
                  端点：    POST {base}/chat/completions

切换方式：在 .env 里设置
    LLM_PROVIDER=openai
    OPENAI_API_BASE=http://gpu-host:8000/v1
    OPENAI_MODEL=Qwen/Qwen2.5-7B-Instruct
    OPENAI_API_KEY=optional-api-key

数据合规提示：
    用 OpenAI provider 时，prompt 会以 HTTP 明文发到 OPENAI_API_BASE。
    院内合规场景下，强烈建议 OPENAI_API_BASE 指向**内网**的自建 vLLM/TGI，
    而非外网的 SaaS 端点，否则等同于"数据出院"。
"""

import json
import requests
from loguru import logger
from typing import Optional, Generator, Protocol, runtime_checkable

from app.core.config import (
    LLM_PROVIDER,
    OLLAMA_API_URL,
    OLLAMA_MODEL_NAME,
    OPENAI_API_BASE,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    LLM_TIMEOUT_SECONDS,
)


# ----------------------------------------------------------------
# 公共 Protocol：所有 provider 必须实现这两个方法 + health_check
# ----------------------------------------------------------------
@runtime_checkable
class LLMService(Protocol):
    """所有 LLM provider 的统一接口。"""
    api_url: str
    model_name: str

    def generate(self, prompt: str, options: Optional[dict] = None) -> str: ...
    def generate_stream(self, prompt: str, options: Optional[dict] = None) -> Generator[str, None, None]: ...
    def health_check(self) -> bool: ...


# ================================================================
# Provider 1: Ollama（本地，默认）
# ================================================================
class OllamaLLMService:
    """本地 Ollama provider。沿用项目 1.x 的实现，行为完全向后兼容。"""

    provider_name = "ollama"

    def __init__(
        self,
        api_url: str = OLLAMA_API_URL,
        model_name: str = OLLAMA_MODEL_NAME,
        timeout: int = LLM_TIMEOUT_SECONDS,
    ):
        self.api_url = api_url
        self.model_name = model_name
        self.timeout = timeout
        logger.info(f"[LLM] OllamaLLMService 初始化: url={api_url}, model={model_name}")

    def generate(self, prompt: str, options: Optional[dict] = None) -> str:
        """普通模式：等待完整响应后一次性返回文本"""
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
        }
        if options:
            options = dict(options)
            response_format = options.pop("format", None)
            if response_format:
                payload["format"] = response_format
            if options:
                payload["options"] = options

        logger.info(f"[Ollama] 普通推理: model={self.model_name}, prompt_len={len(prompt)}")
        try:
            resp = requests.post(
                url=self.api_url,
                json=payload,
                timeout=self.timeout,
                headers={"Content-Type": "application/json"},
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
            logger.warning(f"[Ollama] 返回空 response: {data}")
            return "模型未生成有效建议，请检查模型状态。"
        logger.success(f"[Ollama] 普通推理完成，{len(text)} 字符")
        return text.strip()

    def generate_stream(self, prompt: str, options: Optional[dict] = None) -> Generator[str, None, None]:
        """流式模式：逐 token 返回生成内容"""
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": True,
        }
        if options:
            payload["options"] = options

        logger.info(f"[Ollama] 流式推理: model={self.model_name}, prompt_len={len(prompt)}")
        try:
            with requests.post(
                url=self.api_url,
                json=payload,
                timeout=self.timeout,
                headers={"Content-Type": "application/json"},
                stream=True,
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
                        if chunk.get("done", False):
                            logger.success("[Ollama] 流式推理完成")
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
        try:
            health_url = self.api_url.replace("/api/generate", "")
            resp = requests.get(health_url, timeout=5)
            return resp.status_code == 200
        except Exception:
            return False


# ================================================================
# Provider 2: OpenAI 兼容协议
# ================================================================
class OpenAICompatibleLLMService:
    """
    OpenAI Chat Completions 协议客户端。

    覆盖面：
      - 自建 GPU 推理：vLLM (`--served-model-name`)、TGI、SGLang、llama.cpp server、LM Studio
      - 国内云：DeepSeek、智谱 GLM、Qwen DashScope（兼容模式）、月之暗面 Moonshot
      - 海外：OpenAI、OpenRouter、Together、Groq、Anthropic（部分兼容）

    设计要点：
      1. 只用 requests，不引 openai SDK（依赖太重，且 SDK 各家行为有微妙差异）
      2. 把 prompt 包成单条 user message —— 最大兼容，无需用户配 system prompt
      3. 处理 SSE：data: <json>\\n\\n + data: [DONE]
      4. format=json 用 response_format={"type":"json_object"}（vLLM/OpenAI 都支持）
    """

    provider_name = "openai"

    def __init__(
        self,
        api_base: str = OPENAI_API_BASE,
        api_key: str = OPENAI_API_KEY,
        model_name: str = OPENAI_MODEL,
        timeout: int = LLM_TIMEOUT_SECONDS,
    ):
        if not api_base:
            raise ValueError(
                "OPENAI_API_BASE 未配置。当 LLM_PROVIDER=openai 时，"
                "必须设置 OPENAI_API_BASE 指向 OpenAI 兼容端点（不带 /chat/completions 后缀）。"
                "示例：http://gpu-host:8000/v1"
            )
        if not model_name:
            raise ValueError(
                "OPENAI_MODEL 未配置。当 LLM_PROVIDER=openai 时，"
                "必须设置 OPENAI_MODEL 指定模型名（即 vLLM 的 --served-model-name 或云端模型 ID）。"
            )

        # 规范化 api_base：去掉末尾 /
        self.api_base = api_base.rstrip("/")
        # api_url 给 LLMService Protocol 用，对外语义保持一致
        self.api_url = f"{self.api_base}/chat/completions"
        self.model_name = model_name
        self.api_key = api_key or ""
        self.timeout = timeout

        # 启动时不打印 api_key，但提示是否有 key
        logger.info(
            f"[LLM] OpenAICompatibleLLMService 初始化: "
            f"base={self.api_base}, model={model_name}, "
            f"auth={'on' if self.api_key else 'off (no API key)'}"
        )

    # ──────────────── 内部工具 ────────────────
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _build_payload(self, prompt: str, options: Optional[dict], stream: bool) -> dict:
        """把 prompt + options 翻译成 OpenAI Chat Completions 请求体。"""
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }
        if not options:
            return payload

        opts = dict(options)

        # Ollama options 名 → OpenAI 名（保持 router 调用方零修改）
        if "num_predict" in opts:
            payload["max_tokens"] = opts.pop("num_predict")
        if "temperature" in opts:
            payload["temperature"] = opts.pop("temperature")
        if "top_p" in opts:
            payload["top_p"] = opts.pop("top_p")
        if "stop" in opts:
            payload["stop"] = opts.pop("stop")
        if "seed" in opts:
            payload["seed"] = opts.pop("seed")
        if "presence_penalty" in opts:
            payload["presence_penalty"] = opts.pop("presence_penalty")
        if "frequency_penalty" in opts:
            payload["frequency_penalty"] = opts.pop("frequency_penalty")

        # JSON 严格模式：Ollama 是 format="json"，OpenAI 是 response_format={"type":"json_object"}
        fmt = opts.pop("format", None)
        if fmt == "json":
            payload["response_format"] = {"type": "json_object"}

        # 任何剩下的字段当 extra 透传，照顾 vLLM 的非标准选项（如 top_k, repetition_penalty）
        if opts:
            payload.update(opts)

        return payload

    # ──────────────── 公共接口 ────────────────
    def generate(self, prompt: str, options: Optional[dict] = None) -> str:
        payload = self._build_payload(prompt, options, stream=False)
        logger.info(f"[OpenAI] 普通推理: model={self.model_name}, prompt_len={len(prompt)}")

        try:
            resp = requests.post(
                url=self.api_url,
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(
                f"无法连接到 OpenAI 兼容端点 ({self.api_url})。"
                f"请确认服务已启动，模型 '{self.model_name}' 已加载。错误: {e}"
            ) from e
        except requests.exceptions.Timeout as e:
            raise TimeoutError(f"OpenAI 兼容端点推理超时（>{self.timeout}s）: {e}") from e
        except requests.exceptions.HTTPError as e:
            # OpenAI 协议错误体通常是 {"error": {"message": "..."}}
            try:
                err = resp.json().get("error", {}).get("message") or resp.text
            except Exception:
                err = resp.text
            raise ValueError(f"OpenAI 兼容端点返回 {resp.status_code}: {err}") from e

        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise ValueError(f"无法解析 OpenAI 响应 JSON: {resp.text[:500]}") from e

        # 标准 OpenAI 响应：choices[0].message.content
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            logger.warning(f"[OpenAI] 响应结构不符合预期: {data}")
            return "模型未生成有效建议，请检查模型状态。"

        if not text.strip():
            logger.warning(f"[OpenAI] 返回空 content: {data}")
            return "模型未生成有效建议，请检查模型状态。"

        logger.success(f"[OpenAI] 普通推理完成，{len(text)} 字符")
        return text.strip()

    def generate_stream(self, prompt: str, options: Optional[dict] = None) -> Generator[str, None, None]:
        payload = self._build_payload(prompt, options, stream=True)
        logger.info(f"[OpenAI] 流式推理: model={self.model_name}, prompt_len={len(prompt)}")

        try:
            with requests.post(
                url=self.api_url,
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
                stream=True,
            ) as resp:
                resp.raise_for_status()
                # SSE 协议：每行 data: {...}，结束时 data: [DONE]
                for raw in resp.iter_lines(decode_unicode=True):
                    if not raw:
                        continue
                    line = raw.strip()
                    # vLLM/OpenAI 都以 "data: " 开头；少数实现也会发 ": ping" 心跳，跳过
                    if not line.startswith("data:"):
                        continue
                    body = line[len("data:"):].strip()
                    if body == "[DONE]":
                        logger.success("[OpenAI] 流式推理完成")
                        break
                    try:
                        chunk = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    # delta 路径：choices[0].delta.content
                    try:
                        delta = chunk["choices"][0].get("delta", {})
                        token = delta.get("content")
                    except (KeyError, IndexError, TypeError):
                        token = None
                    if token:
                        yield token
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(
                f"无法连接到 OpenAI 兼容端点 ({self.api_url})。错误: {e}"
            ) from e
        except requests.exceptions.Timeout as e:
            raise TimeoutError(f"OpenAI 流式推理超时（>{self.timeout}s）: {e}") from e

    def health_check(self) -> bool:
        """探测端点是否在线 —— 调用 /models 列表接口（OpenAI 协议标配）。"""
        try:
            resp = requests.get(
                f"{self.api_base}/models",
                headers=self._headers(),
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False


# ================================================================
# 工厂：根据 LLM_PROVIDER 环境变量选 provider
# ================================================================
_singleton: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    """
    返回当前进程的 LLM provider 单例。

    路由层应通过这个工厂取实例，**不要**直接 `OllamaLLMService()`。
    这样改 LLM_PROVIDER 不需要改业务代码。
    """
    global _singleton
    if _singleton is not None:
        return _singleton

    provider = (LLM_PROVIDER or "ollama").lower().strip()
    if provider == "ollama":
        _singleton = OllamaLLMService()
    elif provider in {"openai", "openai_compatible", "openai-compatible"}:
        _singleton = OpenAICompatibleLLMService()
    else:
        raise ValueError(
            f"未知的 LLM_PROVIDER='{provider}'。"
            f"合法值：'ollama'（默认）或 'openai'。"
        )
    return _singleton


def reset_llm_service() -> None:
    """仅供测试使用：重置单例，方便不同测试用例之间切 provider。"""
    global _singleton
    _singleton = None
