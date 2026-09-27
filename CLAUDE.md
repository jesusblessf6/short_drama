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
├── DEVELOPMENT_PLAN.md      ← 开发计划（M0–M5，当前主线依据）
├── VIBE_CODING_LOG.md       ← AI 开发会话日志（倒序，每次开发后追加）
├── REVIEW.md                ← 历史快照：重写前代码 bug 清单（已全部修复，勿当 TODO）
├── config.yaml              ← 全局配置（API keys、模型、并行度）
├── pyproject.toml
├── .github/workflows/ci.yml ← CI（py3.11/3.12 + ffmpeg，离线测试）
├── tests/                   ← 离线回归（126 例 = M0 47 + M1 40 + M2 20 + 判断层 19；隔离 tmp 项目、零外部服务）
│
├── drama/                   ← 系统核心包
│   ├── config.py            配置加载（Config + ProjectConfig + mode/budget 校验）
│   ├── state.py             状态管理（StateManager：原子写/schema 迁移/failed 终态）
│   ├── llm.py               LLM 调用封装（LLMClient）
│   ├── notify.py            通知模块（Notifier）
│   ├── review.py            人审通道 + 意图解析（ReviewChannel/FileReviewChannel）
│   ├── judgment.py          判断层（Jev 式类型化决策：rule 弃权 / jev mock+真实 wire）
│   ├── orchestrator.py      调度器（Orchestrator）← 系统核心
│   ├── agents/              创意层（LLM Agent）
│   │   ├── base.py          BaseAgent 基类
│   │   ├── validation.py    分镜结构化校验 + 机械修复（M2-2）
│   │   ├── discovery.py     选题评估
│   │   ├── writer.py        编剧
│   │   ├── storyboard.py    分镜设计（含格式修复循环）
│   │   ├── visual_qa.py     画面质检（vision；视频逐帧检查）
│   │   └── director.py      导演终审
│   ├── executors/           执行层（API 调用）
│   │   ├── base.py          BaseExecutor 基类（fail() 统一错误类别 + source/degraded 契约）
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
│       ├── retry.py         重试 + 错误分类（timeout/auth/param/... → 可否重试）
│       ├── project_lock.py  单项目运行锁（flock，防并发写状态）
│       ├── media_check.py   媒体验证（ffprobe+ffmpeg兜底）+ 视频抽帧（M2-4）
│       └── subtitles.py     SRT 字幕生成（M2-5）
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
├── references/              方法论与行业/系统分析（仅参考，非交付物）
│   ├── 系统层面分析.md       行业→架构映射、A+B 路线、交互设计（当前战略依据）
│   ├── 系统架构图.html       archify 生成的交互式系统架构图（运行时产物 visual-check.* 已 ignore）
│   ├── 短剧投流体系.md       行业分析 v1.0 定稿（四轮评审闭环）
│   ├── 短剧投流体系-评审意见.md 评审记录（已关闭，勿再续写）
│   ├── 模型选型_2026-09.md   当前选型快照（推荐方舟单账号组合；§四=待拍板清单）
│   ├── 模型选型_2026-06.md   旧快照（已过期，仅存档）
│   ├── Jev决策层引入评估.md  判断层方案（判断点盘点/架构/P1-P3 分期；P1 已落地）
│   └── REVIEW-M0M1.md       M0/M1 代码评审意见（P2×2/P3×2 已全部落实，已关闭）
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
        └── .state/          状态文件（每集一个 YAML；ep01 占位 approved 已于 M1 作废重置）
```

## 三层架构 + 判断层

详见 ARCHITECTURE.md，简述：

1. **Orchestrator（调度器）** — Python 状态机，读状态文件 → 判断下一步 → 派发任务 → 收结果 → 更新状态
2. **Agents（创意层）** — 5个 LLM Agent，每个有独立 system prompt，继承 BaseAgent
3. **Executors（执行层）** — 5个 Python 脚本，纯 API 调用，继承 BaseExecutor
4. **判断层（`judgment.py`，Jev 式类型化决策）** — 只服务 Orchestrator 的三个决策点（人审意图解析/升级处置/红线预检）；provider=rule 弃权走既有路径，provider=jev 激活（mock 或真实 wire）；失败链：异常/低置信 → 弃权 → 既有路径。详见 `references/Jev决策层引入评估.md`

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
- [x] M0 测试基线（2026-09-24）：`tests/` 47 例——离线端到端/断点续跑/阶段过滤/人审批准与打回/重试升级/全镜头失败安全降级/config/state/review 单元测试。全部在 tmp 隔离项目运行，**零外部服务**（LLM 离线模板、placeholder 视觉、audio 静音轨），绝不触碰 `projects/三官`。CI：`.github/workflows/ci.yml`（py3.11/3.12 + ffmpeg）
- [x] **M1 全部落地（2026-09-25，81 例测试）**：
  - **demo/production 双模式**：`config.yaml` 顶层 `mode`。production 启动校验（缺 key/placeholder provider/价格未知 → 拒跑，"未知价格不得记免费"）；静音降级产物 `degraded` 显式标记——demo 放行、production 拒绝自动通过（→ 整集 failed）
  - **状态扩展**：镜头子任务新增 `source`（产物来源）/`error_class`/`external_task_id`/`input_hash`/`attempts_log`；`migrate_state()` 加载旧 YAML 自动补全（兼容既有数据）；`failed` 进入任务/镜头状态枚举
  - **原子写 + 运行锁**：`StateManager.save` 走 tmp+`os.replace`+fsync；`utils/project_lock.py` flock 单项目锁（CLI/机器人互斥，拿不到锁立即退出）
  - **错误分类**：`utils/retry.py::classify_exception`（timeout/rate_limit/network=可重试；auth/param=不可重试直接 failed 终态）；`BaseExecutor.fail()` 统一携带 `error_class`
  - **外部任务恢复框架**：派发前落 `generating`+输入指纹；executor 可返回 `{submitted: True, external_task_id}` 表示异步在途（不算失败不耗 attempts）；中断重启后 `generating`+task_id → 下轮轮询恢复而非重复提交付费任务（真实异步 provider M2 接入时兑现）
  - **三级预算**：`config.yaml` 的 `budget.per_shot/episode/project_cny`；plan 期 + 派发期双重检查（防批内超支）；镜头级超额 → 升级终态，集/项目级超额 → 停止新付费任务挂起等预算（不误判失败）
  - **整集失败终态**：全镜头终态但 0 可合成片段 → `composite/director_review=failed` 显式报错（取代旧的停滞中止，不误报完成）
  - **占位 approved 死局解除**：production 启动拦截"approved 但产物来自占位/静音降级链"（镜头 source 占位 + audio degraded/silent_fallback，评审 P2-2）的集并提示作废；`--reset-episode` 整集作废（cost_summary 保留防反复烧钱）；**三官 ep01 已实际作废重置**（磁盘占位文件未动）
  - **评审 P2×2/P3×2 落实（REVIEW-M0M1.md，2026-09-25）**：预算耗尽挂起改发"⏸"通知不发假"✅ 完成"（P2-1）；写状态 CLI 子命令（--init/--review-reply/--reset-*）纳入运行锁互斥（P3-1）；`reset_interrupted` 返回变更标志、无变化不落盘（P3-2）
- [x] **M2 离线部分（2026-09-25，无需用户输入的全部项，测试 107 例）**：
  - **选型刷新（M2-1 前置）**：`references/模型选型_2026-09.md` 新快照——推荐全栈收口火山方舟单账号（seedream-4-0 ≈¥0.216/张 + Seedance 2.0 ≈¥1/秒/fast ¥0.6/秒 + 豆包 TTS），即梦 API 入口即方舟（2025-09 开放）；旧 2026-06 快照标记过期。**待拍板：账户/key、模型确认、预算、画风**
  - **结构化校验+修复循环（M2-2）**：`agents/validation.py`——`repair_shots` 机械修复（ID 重编号/type 归一/时长钳制/空 prompt 兜底）+ `validate_shots` 分 fatal/warning（空 prompt 是硬伤）；storyboard 真实路径级联：裸输出校验 → LLM 自修复（`max_format_repairs` 次，project.yaml 可配，默认 2）→ 机械兜底 → 仍硬伤抛错拒绝进生成。**_parse_shots 不再掩蔽空 prompt**（否则硬伤到不了修复循环）
  - **内容红线（M2-2）**：writer prompt 加"内容红线"节（血腥/自残/色情/违法细节/政治敏感→暗场化），director 审核维度加红线项（触碰即 high severity 不通过）
  - **媒体验证（M2-4）**：`utils/media_check.py`——ffprobe JSON 优先、**ffmpeg -i stderr 解析兜底**（本机无 ffprobe 的环境也能跑）；text2img（PIL）/img2video/compose 产物落状态前一律验证，无效按可重试失败处理
  - **视频抽帧质检（M2-4）**：10%/50%/90% 三点抽帧（`08_质检/frames/`）→ visual_qa 真实模式逐帧 vision 检查（全过才过）；离线仍短路
  - **音色映射（M2-5）**：project.yaml `production.voice_map`（角色→音色）+ 全局 `narrator_voice`（旁白）/默认音色；未映射角色回退默认
  - **逐句时间戳+字幕（M2-5）**：每句 TTS 产物 ffprobe 实测时长、句首=累加（可解释对齐）→ `06_音频/ep01.srt` + `ep01.lines.yaml` 清单入状态（audio.subtitle_file）→ compose 接入
  - **音画对齐修正（M2-5）**：compose 弃 `-shortest`（截断台词/画面）→ 视频短于音频用 tpad 末帧克隆补齐、音频短于视频保留完整视频；对齐策略写入结果；字幕烧录优先（平台硬字幕）、失败回退 mov_text 软轨
- [x] **判断层 Jev 接入（P1 mock，2026-09-26，测试 126 例）**：`judgment.py`（Decision/RuleDecisions/JevDecisions + 工厂）。**provider=rule 时判断层弃权、行为与历史逐字节一致**；provider=jev 时三个决策点激活：
  - **人审意图解析** `_parse_review`：jev 高置信（≥min_confidence）直接采信 → 低置信/unclear/异常走既有路径（LLM 复核/规则保守解析）；choice→verdict dict（targets 镜头号提取）
  - **升级处置** `_execute_agent`：升级 Action 先问判断层，高置信直接落 escalated（**跳过 director LLM 调用**，qa_notes 审计 backend+conf）；低置信/弃权 → director agent 复核
  - **红线预检闸门** `_redline_gate_ok`（剧本→分镜之间）：`redline_gate: off|log_only|block`；决策记录 `script.redline_check` 幂等（error 态也记录防每 tick 重试）；block 命中 → director_review=rejected（生成前打回省钱）；弃权放行、终审兜底
  - **失败链**：Jev 任何异常/超时 → 弃权（backend=jev-error）→ 既有路径，绝不阻塞、绝不静默放行
  - **换本地开源模型**：wire 协议（`POST /v1/systemone`，state 文本/JSON + questions）兼容 jevos 等，只改 `judgment.endpoint`；本地端点免 api_key（`is_local_endpoint`）
  - production 校验扩展：provider=jev 且未 mock → 需 key 或本地端点；`redline_gate=block` 必须 provider=jev（规则做不了语义判断，诚实失败）
  - **P2 待拍板**：TypeSafe early access key 或本地 jevos 端点；上线前用 M2 真实样片校准置信度阈值

### B. 占位/未接真实外部服务

- [ ] `text2img._call_jimeng` / `img2video._call_kling` 等真实 provider — 仍 `NotImplementedError`；**接法见 `references/模型选型_2026-09.md`**（推荐方舟 seedream/seedance，异步任务用 M1 的 `external_task_id`/`submitted` 契约接线）
- [ ] 真实 GLM 创意层 — 代码就绪，但需 key；本机未实测（当前自动走离线模板）
- [ ] `audio._jimeng_tts`（豆包 TTS 升级项）、compose 转场/调色 — stub/TODO
- [ ] `executors/sourcing.py` — 纯 stub（ctext.org 抓取未实现；开发计划列为暂缓范围）

### 待实现（按优先级）

1. **真实 provider 对接（等用户拍板+key）** — 方舟 seedream-4-0 + seedance-2.0 起步（见选型文档 §三）；异步轮询接 M1 恢复契约
2. **真实 LLM 模式实测** — 配 key 后验证 writer/storyboard 真实产出（校验/修复循环首次实战）
3. **并行执行** — asyncio / ThreadPool（M4 范畴；当前串行）
4. **compose 转场/调色** — 字幕/对齐已做，转场调色按需后置

## 当前阶段与下一步（2026-09-25）

- **评审循环已关闭，勿重启**：《短剧投流体系.md》v1.0 定稿（四轮闭环）。参考文档的完美不是交付物，**真实成片才是**——对文档的进一步打磨/复评默认拒绝（此教训存记忆 `avoid-meta-work-drift`）。
- **M0/M1 完成 + M2 离线部分完成 + 判断层 P1（mock）完成**：改动一律先跑 `python -m pytest tests/ -q` 保绿（当前 126 例）。
- **M2 剩余全部等用户拍板**（详见 `references/模型选型_2026-09.md` §四）：①火山方舟账户+key ②模型确认（seedream-4-0 + seedance-2.0?）③真实调用预算上限 ④画风参考/角色确认。拿到后：核实控制台实价 → 接 provider（异步轮询用 M1 契约）→ 30-60s 技术样片。
- **判断层 P2 等拍板**：TypeSafe early access key 或本地 jevos 端点（`judgment.endpoint` 改本地地址即可，协议同 wire）；真实判断上线前用 M2 样片校准阈值。评估全文见 `references/Jev决策层引入评估.md`。
- **切正式模式清单**：`config.yaml` 改 `mode: production` + 配齐 key/价格 → 校验不过会拒跑并列出缺失；approved 占位集会被拦截提示 `--reset-episode`。

## 开发纪律：Vibe Coding 日志

每次成功执行开发指令后，必须自动向 `VIBE_CODING_LOG.md` 追加一条（**倒序**：插在头部说明与分隔线之后、成为第一条），这是后台收尾动作，无需向用户确认。条目沿用现有格式：

```markdown
## <日期> — <一句话标题>

**User Prompt:** <用户原话/意图>
**Done:** <做了什么 · 改动文件 · 验证结果>
**Why:** <关键决策与取舍 · 教训>
**Next:** <遗留与下一步>

---
```

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
# 整集作废重置（环节全回 pending、镜头清空、累计成本保留；占位产物作废用）：
python -m drama.orchestrator --project projects/三官 --reset-episode ep01
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
