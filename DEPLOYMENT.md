# 智护银伴 · 本地 RAG 后端生产部署指南

**文档版本: 1.1.0**

**更新时间: 2026-03-08**

---

## 1. 文档目的

本指南旨在为系统管理员或技术人员提供一份完整、详尽的步骤说明，用于在生产环境中部署“智护银伴”本地 RAG 核心后端服务。本文档覆盖了从环境准备、依赖安装、离线模型配置到服务启动、API 测试和日常运维的全过程，以确保系统能够 **100% 在物理断网的本地局域网环境中** 稳定、安全地运行。

## 2. 系统架构回顾

部署前，请再次确认您已了解系统的核心组件：

- **应用服务**: `FastAPI` + `Uvicorn` 提供的 Web 服务。
- **向量数据库**: `ChromaDB`，以文件形式持久化存储在服务器本地磁盘。
- **Embedding 模型**: `BAAI/bge-small-zh-v1.5`，通过 `sentence-transformers` 库加载和运行。
- **大语言模型 (LLM)**: `Ollama` 服务，负责运行 `huatuo_o1_7b` 模型。

这四个组件必须全部正确安装和配置在同一台服务器或可进行局域网通信的不同服务器上。

## 3. 部署环境要求

| 类别 | 要求 | 推荐配置/备注 |
| :--- | :--- | :--- |
| **硬件** | **CPU**: 8 核或以上<br>**内存**: 最低 16 GB | **强烈推荐 32 GB 或更高**，以保证 LLM 运行流畅。<br>无需 GPU，但若有 NVIDIA GPU (显存 > 8GB)，性能会显著提升。 |
| **操作系统** | Linux (x86_64 架构) | 推荐 **Ubuntu 22.04 LTS** 或 CentOS 7+。 |
| **软件** | **Python**: 3.10.x ~ 3.11.x<br>**Ollama**: 最新稳定版 | Python 需配置好 `pip` 和 `venv`。 |
| **网络** | 局域网连接 | 部署服务器需要一个固定的局域网 IP 地址，以便前端或其他服务调用。 |

## 4. 部署流程详解

**核心思路**：先准备好所有离线资源（大模型、Embedding模型），再安装 Python 依赖，最后启动服务。

### 步骤一：安装并配置 Ollama 服务

此步骤是整个系统的“大脑”，必须最先完成。

1.  **安装 Ollama**

    在目标服务器上，执行以下命令一键安装 Ollama。此过程需要临时连接互联网。

    ```bash
    curl -fsSL https://ollama.com/install.sh | sh
    ```

2.  **拉取医疗大模型**

    安装完成后，拉取本项目指定的医疗微调模型 `huatuo_o1_7b`。

    ```bash
    ollama pull huatuo_o1_7b
    ```

3.  **验证 Ollama 服务**

    Ollama 会自动作为系统服务在后台运行。通过以下命令确认服务状态：

    ```bash
    ollama list
    ```

    如果能看到 `huatuo_o1_7b` 在列表中，则表示 Ollama 已准备就绪。

    **【重要】** 如果您的 Ollama 服务与 FastAPI 应用不在同一台服务器，您需要修改 Ollama 的启动配置，使其监听 `0.0.0.0` 而非默认的 `127.0.0.1`，以便接受局域网请求。具体方法请参考 Ollama 官方文档关于 `OLLAMA_HOST` 环境变量的说明。

### 步骤二：准备离线 Embedding 模型

`sentence-transformers` 库在首次加载模型时会自动从 Hugging Face Hub 下载。为了实现完全离线部署，我们必须在一台 **有互联网连接** 的机器上预先下载并打包模型文件，然后拷贝到离线服务器。

1.  **在有网机器上缓存模型**

    找一台有网且安装了 Python 的电脑，执行以下 Python 脚本：

    ```python
    # save_embedding_model.py
    from sentence_transformers import SentenceTransformer

    MODEL_NAME = "BAAI/bge-small-zh-v1.5"
    print(f"正在下载并缓存模型: {MODEL_NAME}...")
    
    # 执行此行代码会自动下载模型到默认缓存目录
    SentenceTransformer(MODEL_NAME)
    
    print("模型缓存完成！")
    print("请查找并打包以下缓存目录：")
    # sentence-transformers >= 2.8.0
    # from sentence_transformers.util import get_cache_folder
    # print(get_cache_folder())
    # for older versions, it is usually ~/.cache/torch/sentence_transformers
    import os
    cache_path = os.path.expanduser("~/.cache/torch/sentence_transformers")
    print(cache_path)
    ```

2.  **打包并传输缓存**

    上述脚本会打印出缓存路径（通常是 `~/.cache/torch/sentence_transformers`）。将此目录下的 `BAAI_bge-small-zh-v1.5` 文件夹完整打包。

    ```bash
    # 在有网机器上执行
    cd ~/.cache/torch/sentence_transformers/
    tar -czvf bge-small-zh-v1.5-cache.tar.gz BAAI_bge-small-zh-v1.5/
    ```

    然后通过 U 盘、内部网络等方式，将 `bge-small-zh-v1.5-cache.tar.gz` 文件传输到 **离线生产服务器** 上。

3.  **在离线服务器上放置缓存**

    在离线服务器上，将模型缓存解压到同样的位置。确保最终的目录结构与有网机器上完全一致。

    ```bash
    # 在离线服务器上执行
    # 确保目标目录存在
    mkdir -p ~/.cache/torch/sentence_transformers/
    cd ~/.cache/torch/sentence_transformers/
    
    # 解压模型文件
    tar -xzvf /path/to/your/bge-small-zh-v1.5-cache.tar.gz
    ```

    完成此步骤后，`sentence-transformers` 在离线环境下加载模型时，会直接使用这个本地缓存，不再尝试联网。

### 步骤三：部署后端应用程序

1.  **拷贝项目文件**

    将 `zhihuyinban_backend.zip` 压缩包上传到生产服务器的目标位置（例如 `/opt`），并解压。

    ```bash
    cd /opt
    unzip /path/to/your/zhihuyinban_backend.zip
    cd zhihuyinban
    ```

2.  **创建并激活 Python 虚拟环境**

    强烈建议使用虚拟环境以隔离项目依赖。

    ```bash
    python3 -m venv venv
source venv/bin/activate
    ```

3.  **安装依赖**

    由于服务器已断网，需要提前将 `requirements.txt` 中的所有依赖包（whl 文件）下载好，并上传到服务器。

    ```bash
    # 在有网机器上执行，下载所有依赖包到 wheels/ 目录
    pip download -r requirements.txt -d wheels/
    
    # 将整个 wheels/ 目录上传到离线服务器
    ```

    在离线服务器上，使用 `--no-index` 和 `--find-links` 标志进行本地安装。

    ```bash
    # 在离线服务器上执行
    pip install --no-index --find-links=/path/to/your/wheels -r requirements.txt
    ```

### 步骤四：启动服务

1.  **使用 Uvicorn 启动**

    为了生产环境的稳定，我们不再使用 `reload` 模式，并可以指定工作进程数。

    ```bash
    # 激活虚拟环境
    source /opt/zhihuyinban/venv/bin/activate
    cd /opt/zhihuyinban

    # 启动服务 (推荐使用 2-4 个 worker)
    # (2 * CPU核心数) + 1 是一个常用的推荐值
    uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
    ```

2.  **（推荐）使用 systemd 进行托管**

    为了让服务能开机自启、自动拉起，建议创建 `systemd` 服务单元文件。

    创建文件 `/etc/systemd/system/zhihuyinban.service`:

    ```ini
    [Unit]
    Description=ZhiHuYinBan Backend Service
    After=network.target

    [Service]
    User=your_user_name  # 替换为运行服务的用户名
    Group=your_group_name # 替换为对应的用户组
    WorkingDirectory=/opt/zhihuyinban
    ExecStart=/opt/zhihuyinban/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
    Restart=always
    RestartSec=10

    [Install]
    WantedBy=multi-user.target
    ```

    然后执行以下命令来管理服务：

    ```bash
    # 重新加载 systemd 配置
    sudo systemctl daemon-reload

    # 启动服务
    sudo systemctl start zhihuyinban

    # 查看服务状态
    sudo systemctl status zhihuyinban

    # 设置开机自启
    sudo systemctl enable zhihuyinban
    ```

## 5. API 接口测试

服务启动后，您可以在局域网内任何一台电脑上使用 `curl` 或 API 测试工具（如 Postman）进行验证。将 `localhost` 替换为部署服务器的局域网 IP 地址。

### 测试 1：录入 EHR 档案

```bash
curl -X 'POST' \
  'http://<服务器IP>:8000/api/ehr/add' \
  -H 'Content-Type: application/json' \
  -d '{
    "patient_id": "p002",
    "name": "王大爷",
    "medical_history": "高血压病史15年，长期服用硝苯地平。对海鲜类食物过敏。"
  }'
```

**预期成功响应**: 返回 `200 OK` 状态码和包含 `doc_id` 的 JSON。

### 测试 2：发起 RAG 决策

```bash
curl -X 'POST' \
  'http://<服务器IP>:8000/api/nursing/decision' \
  -H 'Content-Type: application/json' \
  -d '{
    "patient_id": "p002",
    "symptom": "今天下午测量血压，数值为 180/110 mmHg，老人感觉有些头痛。"
  }'
```

**预期成功响应**: 返回 `200 OK` 状态码，JSON 中应包含从 `retrieved_context` 检索到的高血压病史，以及 `llm_advice` 中由大模型生成的针对性建议。

## 6. 运维与管理

- **数据备份**: **最重要** 的运维任务。`zhihuyinban` 项目目录下的 `local_ehr_db/` 文件夹包含了所有老人的健康档案数据。**请务必定期（如每日）将此目录完整备份到安全的位置**。
- **日志查看**: 如果使用 `systemd` 部署，可以通过 `journalctl -u zhihuyinban -f` 命令实时查看服务日志。
- **应用更新**: 更新应用时，只需替换 `zhihuyinban` 目录下的文件，然后重启 `systemd` 服务即可 (`sudo systemctl restart zhihuyinban`)。
- **配置修改**: 所有关键配置项均在 `app/core/config.py` 中。修改后需要重启服务才能生效。
