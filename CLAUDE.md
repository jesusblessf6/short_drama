# CLAUDE.md — 开发指南

> 本文件指导 Claude Code 如何开发本系统。读完此文件 + ARCHITECTURE.md 即可开始编码。

## 项目概述

AI 短剧工厂：全自动 AI 短剧制作系统。从公版古籍发现选题，经剧本、分镜、图片/视频生成、配音、合成，全流程自动化。

**核心原则：系统是核心，项目是数据。** drama/ 下的代码不依赖任何具体项目的内容。

## 技术栈

- Python 3.11+
- OpenAI SDK（调火山引擎 GLM API，兼容 OpenAI 格式）
- PyYAML（配置和状态文件）
- httpx（API 调用）
- FFmpeg（视频合成，系统命令）
- edge-tts（免费 TTS）

## 目录结构

```
short_drama/
├── ARCHITECTURE.md          ← 架构文档（必读）
├── CLAUDE.md                ← 本文件
├── README.md
├── config.yaml              ← 全局配置（API keys、模型、并行度）
├── pyproject.toml
│
├── drama/                   ← 系统核心包
│   ├── config.py            配置加载（Config + ProjectConfig）
│   ├── state.py             状态管理（StateManager）
│   ├── llm.py               LLM 调用封装（LLMClient）
│   ├── notify.py            通知模块（Notifier）
│   ├── orchestrator.py      调度器（Orchestrator）← 系统核心
│   ├── agents/              创意层（LLM Agent）
│   │   ├── base.py          BaseAgent 基类
│   │   ├── discovery.py     选题评估
│   │   ├── writer.py        编剧
│   │   ├── storyboard.py    分镜设计
│   │   ├── visual_qa.py     画面质检（vision）
│   │   └── director.py      导演终审
│   ├── executors/           执行层（API 调用）
│   │   ├── base.py          BaseExecutor 基类
│   │   ├── sourcing.py      古籍抓取
│   │   ├── text2img.py      文生图
│   │   ├── img2video.py     图生视频
│   │   ├── audio.py         配音
│   │   └── compose.py       合成
│   ├── prompts/             Agent 的 system prompt
│   │   ├── discovery.md
│   │   ├── writer.md
│   │   ├── storyboard.md
│   │   ├── visual_qa.md
│   │   └── director.md
│   └── utils/
│       ├── cost_tracker.py  成本追踪
│       └── retry.py         重试逻辑
│
├── templates/               模板文件
│   ├── character_card.yaml
│   ├── t2i_prompt.yaml
│   ├── i2v_prompt.yaml
│   ├── project.yaml
│   └── state.yaml
│
├── corpus/                  公版语料库（sourcing 抓取的目标）
│
├── references/              方法论文档
│
└── projects/                项目数据
    └── 三官/                第一个项目
        ├── project.yaml     项目配置
        ├── 01_框架/
        ├── 02_人物/
        ├── 03_剧本/
        ├── 04_分镜/
        ├── 05_美术/
        ├── 06_音频/
        ├── 07_成片/
        ├── 08_质检/
        ├── 09_制作日志/
        ├── reference/
        └── .state/          状态文件（每集一个 YAML）
```

## 三层架构

详见 ARCHITECTURE.md，简述：

1. **Orchestrator（调度器）** — Python 状态机，读状态文件 → 判断下一步 → 派发任务 → 收结果 → 更新状态
2. **Agents（创意层）** — 5个 LLM Agent，每个有独立 system prompt，继承 BaseAgent
3. **Executors（执行层）** — 5个 Python 脚本，纯 API 调用，继承 BaseExecutor

## 当前实现状态

> 本节如实反映代码现状。**纵向切片已打通**：离线占位模式下，`三官 ep01` 可端到端产出真实 `07_成片/ep01.mp4`（验证记录见 `VIBE_CODING_LOG.md` 2026-06-21 条）。
> 离线零 key 跑通方式：`drama --project projects/三官 --init-episode ep01` 然后 `drama --project projects/三官 --episode ep01`（`config.yaml` 默认 `provider: placeholder` + 无 ARK key 自动 `llm.is_offline`）。

### A. 真实完整、可用

- [x] 基础设施：`config.py`、`state.py`、`llm.py`（含 vision）、`notify.py`、`utils/retry.py`、`utils/cost_tracker.py`（**已接线**）
- [x] 调度器 `orchestrator.py`（重写版）— 状态机可跑通；`--init`/`--init-episode` 初始化；context/result 契约修正；按 shot.type 取重试预算；**升级路径可达且收敛**；QA 离线短路；cost 接线；断点续跑。**串行执行**（asyncio 并行未做）
- [x] 创意层 `agents/`（writer/storyboard/visual_qa/director/discovery）— 含**离线模板模式**（`offline_output`）；storyboard 产**结构化 shots**；result 不含 status
- [x] 执行层占位 provider：`text2img`（PIL 占位图）、`img2video`（ffmpeg 静帧转 mp4）、`audio`（edge-tts 可跑 + 静音降级）、`compose`（ffmpeg 拼接+合音 + concat 回退）
- [x] 5 个 Prompt、模板、三官最小素材（`02_人物/` 角色卡、`05_美术/风格定调/`）
- [x] 人审环节（轻量聊天式）：`review.py`（`ReviewChannel`/`FileReviewChannel` + 规则意图解析，LLM 可升级）；`project.yaml` 的 `production.stage_modes` 配 `auto|review`；`director` 终审支持 review 模式（→ `reviewing` 挂起 → `--review-reply` 提交 → 续跑）；`--reset-review` 复活被打回的集。回复通道可插拔（Telegram 留插槽）
- [x] 成本记账与偏离预警：token→¥ 折算（`llm.price_per_1k_tokens`）、per-episode `cost_summary` 写回 state、`cost_monitor` 基线对比预警

### B. 占位/未接真实外部服务

- [ ] `text2img._call_jimeng` / `img2video._call_kling` 等真实 provider — 仍 `NotImplementedError`（无 key/文档），接通后把 `config.yaml` 的 `provider` 由 `placeholder` 改回 `jimeng`/`kling`
- [ ] 真实 GLM 创意层 — 代码就绪，但需 `ARK_CODING_API_KEY`；本机未实测（当前自动走离线模板）
- [ ] `audio._jimeng_tts`、`compose` 字幕/转场/调色 — stub/TODO
- [ ] `executors/sourcing.py` — 纯 stub（ctext.org 抓取未实现）
- [ ] `agents/visual_qa` 视频帧提取 — 真实模式下视频质检仍 TODO（离线已短路）

### 待实现（按优先级）

1. **即梦/可灵真实 API 对接** — 填 `_call_jimeng()` / `_call_kling()`，改 config provider
2. **真实 GLM 模式实测** — 配 `ARK_CODING_API_KEY`，验证 writer/storyboard 真实产出
3. **并行执行** — asyncio / ThreadPool（当前串行；`plan_shots` 已按 `parallel_shots` 产多动作，待并发执行层）
4. **sourcing 实现** — ctext.org 抓取
5. **视频帧提取** — visual_qa 真实视频质检
6. **compose 完善** — 字幕/转场/调色；即梦 TTS
7. **整集失败终态** — 全 i2v 升级时 compose 0 片段会触发停滞中止，可加显式"整集失败"状态

## 开发约定

### Agent 开发

1. 继承 `BaseAgent`
2. 定义 `system_prompt_file`（指向 `drama/prompts/` 下的文件）
3. 实现 `build_messages(context)` — 从上下文构建 LLM messages
4. 实现 `parse_output(response, context)` — 解析 LLM 输出为 dict
5. 需要图片输入时，重写 `run()` 使用 `self.llm.chat_with_image()`
6. 输出必须是 dict，**但不要包含 `status` 字段** — 任务状态由 Orchestrator 决定并写入；agent 返回 `status` 会与 `update_task(..., status=...)` 重复键冲突。只返回业务字段（如 `file` / `shot_ids` / `shot_count`）。详见 ARCHITECTURE §13。
7. 文件写入在 `parse_output` 中完成，返回相对路径

### Executor 开发

1. 继承 `BaseExecutor`
2. 实现 `run(task)` — 执行 API 调用，返回 dict
3. 实现 `validate_input(task)` — 检查输入完整性
4. 返回 dict 必须包含 `success: bool`
5. API 调用失败时返回 `{"success": False, "error": "..."}`
6. 不调 LLM，纯 API + 工具调用

### 状态文件

- 每集一个 YAML 文件，存在 `projects/<name>/.state/ep01.yaml`
- 状态枚举见 `state.py` 中的 `TASK_STATUSES` 和 `SHOT_STATUSES`
- Agent/Executor 不直接写状态文件，通过返回值由 Orchestrator 写入

### 配置

- 全局配置：`config.yaml`（API keys、模型、并行度）
- 项目配置：`projects/<name>/project.yaml`（路径、幕结构、制作参数）
- 环境变量用 `${VAR}` 语法，config.py 自动替换

## 运行

```bash
# 查看状态
python -m drama.orchestrator --project projects/三官 --status

# 运行全流程
python -m drama.orchestrator --project projects/三官

# 只跑某一集
python -m drama.orchestrator --project projects/三官 --episode ep01

# 只跑某个环节
python -m drama.orchestrator --project projects/三官 --episode ep01 --stage script

# 人审（环节在 project.yaml 配 stage_modes: <stage>: review）
# 跑到该环节会挂起等人审，下面提交回复后重跑续跑：
python -m drama.orchestrator --project projects/三官 --review-reply ep01 director "通过"
python -m drama.orchestrator --project projects/三官 --review-reply ep01 director "打回 镜头03 手不对"
# 被打回的集复活（rejected/reviewing → pending）：
python -m drama.orchestrator --project projects/三官 --reset-review ep01
```

## 环境变量

```
ARK_CODING_API_KEY=火山引擎API密钥
JIMENG_API_KEY=即梦API密钥
KLING_API_KEY=可灵API密钥
TELEGRAM_BOT_TOKEN=Telegram机器人token（可选）
TELEGRAM_CHAT_ID=Telegram聊天ID（可选）
```

## 注意事项

1. **不要在 drama/ 中硬编码项目路径** — 所有路径通过 ProjectConfig.get_path() 获取
2. **不要在 Agent/Executor 中直接写状态文件** — 通过返回值交给 Orchestrator
3. **LLM 输出解析要容错** — LLM 可能不按格式输出，parse_output 要有 fallback
4. **API 调用要有重试** — 使用 drama/utils/retry.py
5. **成本要追踪** — 每次 API 调用记录 cost，写入状态文件
6. **中断可恢复** — Orchestrator 重启时自动重置 "generating" 状态为 "pending"
