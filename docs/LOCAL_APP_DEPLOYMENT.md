# 智护银伴 · 本地应用化部署说明

本说明面向试点演示、养老院院内部署和非开发者运维场景。

项目仍然保留 Docker / Uvicorn 等开发者部署方式，但商业化交付时不应要求养老院用户理解 Docker、端口、venv、uvicorn、环境变量等概念。因此新增 Windows 本地启动器，把常见部署动作封装成一个入口。

## 一、推荐启动方式

在项目根目录打开 PowerShell，执行：

```powershell
.\scripts\launch-local.ps1
```

启动器会自动完成：

1. 检查 Python；
2. 创建或复用 `venv`；
3. 安装 `requirements.txt`；
4. 初始化 `.env`；
5. 检查 Ollama；
6. 检查端口占用；
7. 启动 FastAPI 后端；
8. 等待 `/health` 就绪；
9. 自动打开管理端页面。

默认访问地址：

- 管理端：http://127.0.0.1:8000/
- 护工端：http://127.0.0.1:8000/nurse
- 健康检查：http://127.0.0.1:8000/health

## 二、常用参数

### 1. 快速启动，不重复安装依赖

首次启动成功后，后续可以使用：

```powershell
.\scripts\launch-local.ps1 -SkipInstall
```

### 2. 允许没有 Ollama 时启动

只演示入院、床位、护理记录、交接班等非 AI 业务模块时：

```powershell
.\scripts\launch-local.ps1 -AllowNoOllama
```

### 3. 局域网访问

如果需要同一养老院局域网内其他电脑访问：

```powershell
.\scripts\launch-local.ps1 -BindAddress 0.0.0.0
```

然后在其他电脑访问部署机器的局域网 IP，例如：

```text
http://192.168.1.10:8000/
```

注意：需要 Windows 防火墙允许该端口入站访问。

### 4. 修改端口

```powershell
.\scripts\launch-local.ps1 -Port 8010
```

## 三、部署诊断

如果启动失败，先运行：

```powershell
.\scripts\diagnose.ps1 -WriteReport
```

诊断脚本会检查：

- Python / pip / venv；
- 关键文件是否存在；
- `.env` 配置是否存在，并脱敏显示关键项；
- 端口占用；
- Ollama 状态；
- Docker 状态；
- 磁盘空间；
- `/health` 健康检查。

报告会生成在：

```text
logs/diagnose-YYYYMMDD-HHMMSS.txt
```

该报告可直接发给维护人员排查，敏感字段会被脱敏。

## 四、为什么新增本地启动器

传统源码部署暴露了过多工程细节：

- 用户要自己创建虚拟环境；
- 自己安装依赖；
- 自己复制 `.env`；
- 自己判断 Ollama 是否启动；
- 自己处理端口占用；
- 自己看日志；
- 自己确认 `/health` 是否正常。

养老院试点场景不应把这些复杂度交给院方。新增启动器的目标是把项目从“GitHub 代码仓库交付”推进到“本地应用化交付”：

> 日常像应用一样启动，故障时能一键诊断，联网时再做更新和维护。

## 五、当前边界

本启动器不是完整商业安装包，还没有实现：

- Windows 安装向导；
- 桌面快捷方式自动创建；
- 后台服务注册；
- 自动升级；
- 升级失败回滚；
- 数据备份/恢复图形界面；
- 模型自动下载和校验。

这些应作为后续商业化交付阶段继续补齐。

## 六、建议后续产品化路线

1. 将 `launch-local.ps1` 封装为 `智护银伴启动器.exe`；
2. 首次启动增加图形化初始化向导；
3. 增加本地服务守护和自动重启；
4. 增加升级前自动备份；
5. 增加版本回滚；
6. 增加桌面端壳应用，例如 Tauri / Electron；
7. 增加云端轻管控：授权、模板、更新、远程维护。
