# AI 短剧工厂

全自动 AI 短剧制作系统。从公版古籍发现选题，经剧本创作、分镜设计、图片/视频生成、配音、合成，全流程自动化。

## 快速开始

```bash
# 安装依赖
pip install -e .

# 配置环境变量
export ARK_CODING_API_KEY="your-key"
export JIMENG_API_KEY="your-key"
export KLING_API_KEY="your-key"

# 查看系统状态
python -m drama.orchestrator --project projects/三官 --status

# 启动制作（开发模式，前台跑）
python -m drama.orchestrator --project projects/三官 --verbose

# 只跑某一集
python -m drama.orchestrator --project projects/三官 --episode ep01

# 只跑某个环节（调试用）
python -m drama.orchestrator --project projects/三官 --episode ep01 --stage storyboard

# 人审（环节在 project.yaml 的 production.stage_modes 配为 review；默认 director）
python -m drama.orchestrator --project projects/三官 --review-reply ep01 director "通过"
python -m drama.orchestrator --project projects/三官 --reset-review ep01   # 复活被打回的集

# 发现新选题（注意：discovery 目前没有独立 CLI 入口，下面命令尚不可用，待实现）
# python -m drama.agents.discovery --corpus corpus/聊斋志异/
```

## 核心概念

- **系统是核心，项目是数据** — drama/ 是系统代码，projects/ 下每个目录是一个项目。系统不依赖任何具体项目的内容。
- **三层分离** — 调度层（Orchestrator 状态机）、创意层（LLM Agent）、执行层（API 脚本）。
- **文件系统通信** — 所有模块通过文件系统交换数据，无消息队列。
- **断点续跑** — 每个任务的状态持久化到 YAML，中断后自动恢复。

## 文档

- [ARCHITECTURE.md](ARCHITECTURE.md) — 完整架构文档（开发主依据）
- [references/](references/) — AI漫剧制作方法论、Agent架构设计、核心难点方案

## 项目结构

```
short_drama/
├── drama/           系统核心包
├── templates/       模板（角色卡、提示词）
├── corpus/          公版语料库
├── projects/        项目数据（三官等）
└── references/      方法论参考
```

## 技术栈

- LLM: 火山引擎 GLM（config.yaml 配 `glm-latest`，vision 用 `glm-4v`）
- 文生图: 即梦 3.0 Pro（执行层待对接，当前为 stub）
- 图生视频: 可灵 2.1（执行层待对接，当前为 stub）
- 配音: Edge TTS (免费，已实现) → 可切换即梦配音（待实现）
- 合成: FFmpeg（拼接+合音已实现，字幕/调色待加）
- 编排: Python（目标 asyncio 并行；**当前为同步串行**）
