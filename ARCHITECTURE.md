# AI 短剧工厂 — 架构文档

> 本文档是系统设计与开发的主依据。Claude Code 开发时以此为唯一架构参考。

## 一、系统定位

全自动 AI 短剧制作系统。从公版古籍发现选题，到剧本创作、分镜设计、图片/视频生成、配音、合成成片，全流程自动化。

核心设计原则：

1. **系统是核心，项目是数据** — 系统代码（drama/）不依赖任何具体项目的内容或路径。三官是第一个项目，以后可以有任意个。
2. **三层分离** — 调度层（状态机）、创意层（LLM Agent）、执行层（API 脚本）各司其职。
3. **文件系统通信** — 所有模块通过文件系统交换数据，无进程间通信、无消息队列。
4. **断点续跑** — 每个任务单元的状态持久化到 YAML 文件，进程中断后从断点恢复。
5. **渐进可用** — 每个模块独立可用，不需要全部完成才能跑。填一个模块测一个。
6. **零外部框架依赖** — 不依赖 Hermes 或任何 Agent 框架。纯 Python + openai SDK + ffmpeg。

## 二、三层架构

```
┌─────────────────────────────────────────────────────┐
│                   Orchestrator                       │
│            (Python 状态机 / 主循环)                    │
│         读状态 → 决定下一步 → 派发任务                  │
└──────────────────────┬──────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
┌───────▼──────┐ ┌─────▼──────┐ ┌────▼───────┐
│   创意层      │ │  执行层     │ │  质检层     │
│  (LLM Agent) │ │ (Python)   │ │ (LLM Agent)│
│              │ │            │ │            │
│ discovery    │ │ sourcing   │ │ visual_qa  │
│ writer       │ │ text2img   │ │ director   │
│ storyboard   │ │ img2video  │ │            │
│              │ │ audio      │ │            │
│              │ │ compose    │ │            │
└──────────────┘ └────────────┘ └────────────┘
        │              │              │
        └──────────────┼──────────────┘
                       │
                ┌──────▼───────┐
                │   文件系统     │
                │  + 状态文件    │
                └──────────────┘
```

### 调度层 (Orchestrator)

- 性质：Python 进程，状态机，不是 LLM
- 职责：读状态文件 → 判断每个任务单元当前阶段 → 决定下一步调谁 → 派发任务 → 收结果 → 更新状态
- 不做任何创意判断，不做任何 API 调用
- 支持并行：集间流水线 + 集内镜头组并行

### 创意层 (Agents)

需要 LLM 推理的环节。每个 Agent 是一个 Python 类，封装 system prompt + 输入构建 + 输出解析。

| Agent | 职责 | 输入 | 输出 |
|-------|------|------|------|
| discovery | 评估公版作品的短剧改编潜力 | 语料库中的作品文本 | 评估报告 (YAML) |
| writer | 按框架写单集剧本 | 框架 + 人物圣经 + 原著 | 分集剧本 (Markdown) |
| storyboard | 拆分镜 + 写提示词 | 剧本 + 角色描述卡 | 分镜脚本 (Markdown) |
| visual_qa | 画面质检（需 vision 模型） | 生成的图片/视频 + 角色卡 + 分镜 | pass/fail + 修正建议 |
| director | 单集成片终审 + 处理升级问题 | 成片 + 剧本 + 分镜 | approved / 修改指令 |

质检隔离原则：
- visual_qa 和 director 用独立的 LLM 调用，不加载 writer/storyboard 的 system prompt
- visual_qa 只看最终输出物（图片/视频），不看生成过程中的 prompt
- 生产 Agent 不看质检 Agent 的内部推理，只看质检报告结论

### 执行层 (Executors)

纯 API 调用 + 重试逻辑，不涉及 LLM。

| Executor | 职责 | 输入 | 输出 |
|----------|------|------|------|
| sourcing | 从古籍网站抓取公版作品 | 数据源配置 | 文本文件 |
| text2img | 文生图 | 文生图 prompt + 角色参考图 | 图片文件 |
| img2video | 图生视频 | 分镜图 + 图生视频 prompt | 视频片段 |
| audio | AI 配音 + BGM | 台词文本 + 视频片段 | 音轨文件 |
| compose | 后期合成 | 视频片段 + 音轨 | 成片 |

## 三、完整制作流水线

```
[1] Sourcing          从古籍网站抓取公版作品 → corpus/
     ↓
[2] Discovery         LLM 评估改编潜力 → discovery_report.yaml
     ↓
[3] 人工选题           用户从候选中选择 → 创建项目目录 + project.yaml
     ↓
[4] Framework         人工或 LLM 辅助搭建框架 → 01_框架/整体框架.md
     ↓ (手动触发，不在自动管线内)
[5] Writer            按框架写分集剧本 → 03_剧本/{幕}/ep{N}.md
     ↓
[6] Storyboard        拆分镜 + 写提示词 → 04_分镜/{幕}/ep{N}_storyboard.md
     ↓
[7] Text2Img          逐镜头文生图 → shots/{ep}_{shot}.png
     ↓
[8] Visual QA         画面质检 → pass / fail (退回重生成)
     ↓
[9] Img2Video         逐镜头图生视频 → shots/{ep}_{shot}.mp4
     ↓
[10] Visual QA        视频质检 → pass / fail
     ↓
[11] Audio            配音 + BGM → audio/{ep}.wav
     ↓
[12] Compose          FFmpeg 合成成片 → 07_成片/{ep}.mp4
     ↓
[13] Director         单集终审 → approved / 退回
     ↓
     进入下一集 (流水线)
```

步骤 1-3 是选题阶段，步骤 4 是框架搭建（当前由人工完成，未来可加 framework agent），步骤 5-13 是单集生产循环。

## 四、状态管理

### 状态文件层级

```
项目状态
├── 项目级: projects/{项目}/project.yaml          项目配置
├── 集级:   projects/{项目}/.state/ep{N}.yaml      单集生产状态
└── 镜头级: 集级状态文件内的 shots 数组             单镜头状态
```

### 集级状态文件格式

```yaml
# projects/三官/.state/ep01.yaml
episode: ep01
act: 第一幕_灭门
updated_at: "2026-06-20T21:00:00"

script:
  status: approved        # pending | drafting | reviewing | approved | rejected
  file: 03_剧本/第一幕_灭门/ep01.md
  attempts: 1
  cost_tokens: 8500

storyboard:
  status: approved
  file: 04_分镜/第一幕_灭门/ep01_storyboard.md
  attempts: 1
  cost_tokens: 12000
  shot_count: 18

shots:
  - id: ep01_shot01
    scene: "商府内堂"
    text2img:
      status: approved    # pending | generating | qa_pass | qa_fail | approved
      file: 05_美术/shots/ep01_shot01.png
      prompt_file: 04_分镜/第一幕_灭门/ep01_shot01_t2i.txt
      attempts: 2
      cost: 1.0
    img2video:
      status: qa_fail
      file: 05_美术/shots/ep01_shot01.mp4
      prompt_file: 04_分镜/第一幕_灭门/ep01_shot01_i2v.txt
      attempts: 3
      cost: 6.0
      qa_notes: "手指变形，需重试"
      last_attempt: "2026-06-20T20:30:00"

audio:
  status: pending
  file: 06_音频/ep01.wav

composite:
  status: pending
  file: 07_成片/ep01.mp4

director_review:
  status: pending
  result: null
  notes: null
```

### 状态枚举

任务级状态（script, storyboard, audio, composite, director_review）：
- `pending` — 未开始
- `drafting` — Agent 正在处理
- `reviewing` — 等待质检
- `approved` — 通过，可进入下一环节
- `rejected` — 被质检打回，需重做

镜头级状态（text2img, img2video）：
- `pending` — 未开始
- `generating` — 正在调用 API 生成
- `qa_pass` — 质检通过
- `qa_fail` — 质检失败，需重试
- `approved` — 通过（qa_pass 后由调度器标记）
- `escalated` — 超过重试上限，升级处理

### 断点续跑

Orchestrator 启动时：
1. 扫描 `projects/{项目}/.state/` 下所有 YAML
2. 跳过 `director_review.status == approved` 的集
3. 对未完成的集，找到第一个 `pending` 的环节继续
4. 对 `generating` 状态的任务（进程中途断了），重置为 `pending` 重新执行

## 五、并行策略

### 集间流水线

```
时间 →
ep01: [剧本✓] [分镜✓] [镜头生产......] [音频] [合成] [终审]
ep02:          [剧本✓] [分镜✓]          [镜头生产......] ...
ep03:                   [剧本✓] ...
```

ep01 镜头生产进行中时，ep02 的剧本和分镜可以并行开始。

### 集内镜头并行

同一集的镜头按场景分组，同场景的镜头可以并行文生图（共用参考图和场景设定）。

```python
PARALLEL_EPISODES = 2    # 同时推进几集的生产
PARALLEL_SHOTS = 3       # 单集内并行几个镜头的生成
```

### 不可并行的约束

- 同一集的 audio 依赖全部镜头视频完成
- 同一集的 compose 依赖 audio + 全部视频完成
- 同一集的 director_review 依赖 compose 完成
- 不同集之间无依赖

## 六、重试与升级

### 重试预算

| 任务类型 | 最大重试 | 原因 |
|---------|---------|------|
| 静态文生图 | 5 | 成本低，一致性可控 |
| 简单动态视频 (走/坐/看) | 5 | 动作简单，成片率高 |
| 复杂动态视频 (打斗/舞蹈) | 8 | 成片率低，需更多尝试 |
| 对口型镜头 | 3 | 口型匹配难，超限降级为画外音 |

### 升级处理

超过重试预算的任务标记为 `escalated`，上报 director Agent 决策：
- 简化动作重写分镜
- 降级处理（静态图 + 旁白替代）
- 暂停该集，等待人工干预

### 实现约束（避免设计落空）

以下是当前代码踩过的坑，实现时必须满足，否则升级/重试形同虚设：

1. **重试预算按类型取值**：镜头需带 `type` 字段（如 `static` / `simple` / `complex` / `lip_sync`），调度器据此从 `max_retry` 取对应预算。img2video **不得复用 text2img 的预算**。
2. **升级路径必须可达**：挑选待处理镜头的逻辑（`find_pending_shot` 等）**必须能 surface 已耗尽重试的镜头**，把它交给升级分支；否则耗尽镜头被过滤掉 → 既不重试也不升级 → 该集 `all_shots_approved` 永远为假 → 整集静默卡死。
3. **重试上限只有一处真相**：判定"是否还能重试"的阈值不能在多处硬编码（如别处写死 `< 5`），必须统一读 `project.yaml` 的 `max_retry`。

## 七、成本追踪

每个任务记录实际消耗：

```yaml
cost_summary:
  llm_tokens:
    writer: 8500
    storyboard: 12000
    visual_qa: 6000
    director: 3000
  api_calls:
    text2img:
      count: 45
      cost_cny: 22.5
    img2video:
      count: 28
      cost_cny: 56.0
  total_cny: 78.5
```

> 实现状态（2026-06-21 更新）：**已接线**。`state.py` 的 `new_episode_state` 含 `cost_summary` 字段；`StateManager.add_cost` 把每环节消耗写回该集；`CostTracker` 已接进 Orchestrator（`record_llm`/`record_api`，运行末 `save_report` 到 `09_制作日志/cost_report.yaml`）。LLM token 经 `llm.price_per_1k_tokens` 折算成 ¥ 计入 `total_cny`。

### 成本监测与偏离预警

`config.yaml` 的 `cost_monitor` 配基线（成本占比 = 行业共识：视频≈77%/图≈19%/LLM≈4%；token 占比 = 架构估算），运行结束时 `CostTracker.check_deviation` 比对实际占比，相对偏离超 `deviation_threshold`（默认 ±50%）经 Notifier 预警；空数据（离线全 0）不报。

> 注意：基线目前是**估算**。接真实模型前，离线短路的环节（如 visual_qa）实际占比为 0，会触发误报——接真实数据前建议把 `cost_monitor.enabled` 设 false 或将基线标为"待校准"。

单集成本估算（基于 references/three_core_challenges.md）：
- 文生图：~50元/集（20镜头 × 5次 × 0.5元）
- 图生视频：~200元/集（20镜头 × 5次 × 2元）
- LLM token：~10元/集
- 合计：~260元/集，80集约 2万元

## 八、项目配置

### project.yaml

```yaml
# projects/三官/project.yaml
name: 三官
source_work: 聊斋志异·商三官
source_author: 蒲松龄
source_dynasty: 清
episodes: 80
episode_duration: "1.5-2min"
aspect_ratio: "9:16"       # 竖屏

# 目录映射（系统通过这个找到项目内文件）
paths:
  framework: 01_框架/整体框架.md
  characters: 02_人物/
  scripts: 03_剧本/
  storyboards: 04_分镜/
  art: 05_美术/
  audio: 06_音频/
  output: 07_成片/
  state: .state/
  reference: reference/

# 幕结构
acts:
  - name: 第一幕_灭门
    episodes: [1, 12]
  - name: 第二幕_潜入
    episodes: [13, 35]
  - name: 第三幕_对决
    episodes: [36, 60]
  - name: 第四幕_代价
    episodes: [61, 80]

# 制作参数
production:
  shots_per_episode: 15-20
  max_retry:
    text2img: 5
    img2video_simple: 5
    img2video_complex: 8
    lip_sync: 3
```

## 九、全局配置

### config.yaml

```yaml
# 全局系统配置，与具体项目无关

llm:
  provider: volcengine
  base_url: "https://ark.cn-beijing.volces.com/api/coding/v3"
  api_key: "${ARK_CODING_API_KEY}"
  model: "glm-latest"
  vision_model: "glm-4v"          # visual_qa 用的 vision 模型
  max_tokens: 4096
  temperature: 0.7

# 执行层 API 配置
apis:
  text2img:
    provider: jimeng             # 即梦
    api_key: "${JIMENG_API_KEY}"
    model: "jimeng-3.0-pro"
    cost_per_call: 0.5
  img2video:
    provider: kling              # 可灵
    api_key: "${KLING_API_KEY}"
    model: "kling-2.1"
    cost_per_call: 2.0
  audio:
    provider: edge_tts           # 先用免费的
    voice: "zh-CN-XiaoxiaoNeural"
  # 音频可后续切换为即梦配音

# 调度配置
orchestrator:
  parallel_episodes: 2
  parallel_shots: 3
  tick_interval: 10              # 秒
  state_dir: ".state"

# 通知配置（全部可选，不配也能跑）
notify:
  telegram:
    bot_token: "${TELEGRAM_BOT_TOKEN}"
    chat_id: "${TELEGRAM_CHAT_ID}"
  # hermes_api_url: "http://localhost:7777"   # 可选

# 语料库
corpus:
  dir: "corpus"
  sources:
    - name: 聊斋志异
      url: "https://ctext.org/liaozhai-zhiyi"
    - name: 阅微草堂笔记
      url: "https://ctext.org/yuewei-caotang-biji"
    - name: 搜神记
      url: "https://ctext.org/soushenji"
    - name: 子不语
      url: "https://ctext.org/zibuyu"

# FFmpeg
ffmpeg:
  path: "ffmpeg"
  default_codec: "libx264"
  default_crf: 23
```

## 十、目录结构总览

```
short_drama/
├── ARCHITECTURE.md              ← 本文档
├── README.md
├── config.yaml                  全局配置
├── pyproject.toml
│
├── drama/                       系统核心包
│   ├── __init__.py
│   ├── config.py                配置加载
│   ├── orchestrator.py          调度器（状态机主循环）
│   ├── state.py                 状态文件读写
│   ├── llm.py                   LLM 调用封装
│   ├── notify.py                通知模块
│   │
│   ├── agents/                  创意层（LLM Agent）
│   │   ├── __init__.py
│   │   ├── base.py              Agent 基类
│   │   ├── discovery.py         选题评估
│   │   ├── writer.py            编剧
│   │   ├── storyboard.py        分镜
│   │   ├── visual_qa.py         画面质检
│   │   └── director.py          导演终审
│   │
│   ├── executors/               执行层（API 脚本）
│   │   ├── __init__.py
│   │   ├── base.py              Executor 基类
│   │   ├── sourcing.py          古籍抓取
│   │   ├── text2img.py          文生图
│   │   ├── img2video.py         图生视频
│   │   ├── audio.py             配音
│   │   └── compose.py           合成
│   │
│   ├── prompts/                 Agent system prompts
│   │   ├── discovery.md
│   │   ├── writer.md
│   │   ├── storyboard.md
│   │   ├── visual_qa.md
│   │   └── director.md
│   │
│   └── utils/
│       ├── __init__.py
│       ├── cost_tracker.py      成本追踪
│       └── retry.py             重试逻辑
│
├── templates/                   模板
│   ├── character_card.yaml      角色描述卡模板
│   ├── t2i_prompt.yaml          文生图 7 段式模板
│   ├── i2v_prompt.yaml          图生视频 5 段式模板
│   ├── project.yaml             项目配置模板
│   └── state.yaml               状态文件模板
│
├── corpus/                      公版语料库
│   ├── 聊斋志异/
│   ├── 阅微草堂笔记/
│   └── ...
│
├── projects/                    项目数据
│   └── 三官/
│       ├── project.yaml
│       ├── 01_框架/
│       ├── 02_人物/
│       ├── 03_剧本/
│       ├── 04_分镜/
│       ├── 05_美术/
│       ├── 06_音频/
│       ├── 07_成片/
│       ├── 08_质检/
│       ├── 09_制作日志/
│       ├── reference/
│       └── .state/
│
└── references/                  方法论参考
    ├── ai_manga_drama_pipeline.md
    ├── agent_architecture.md
    └── three_core_challenges.md
```

## 十一、模块开发优先级

按依赖顺序，每个模块独立可测：

| 优先级 | 模块 | 依赖 | 可验证标志 |
|--------|------|------|-----------|
| P0 | config.py | 无 | 能加载 config.yaml，环境变量替换 |
| P0 | state.py | config.py | 能读写 ep01.yaml，断点恢复逻辑 |
| P0 | llm.py | config.py | 能调通火山引擎 API，返回文本 |
| P1 | orchestrator.py | state.py, config.py | 空转跑通：读状态→打印"该调XX"→不真调 |
| P1 | agents/base.py | llm.py | 基类能跑通：构建消息→调LLM→解析输出 |
| P1 | executors/base.py | config.py | 基类能跑通：任务队列读写 |
| P2 | agents/writer.py | base.py, state.py | 输入框架→输出单集剧本 |
| P2 | agents/storyboard.py | base.py, state.py | 输入剧本→输出分镜脚本 |
| P2 | executors/text2img.py | base.py | 输入prompt→生成图片文件 |
| P3 | agents/visual_qa.py | base.py, llm.py | 输入图片→输出pass/fail |
| P3 | executors/img2video.py | base.py | 输入图+prompt→生成视频 |
| P3 | executors/audio.py | base.py | 输入台词→生成音频 |
| P3 | executors/compose.py | base.py | 输入视频+音频→合成成片 |
| P4 | agents/director.py | base.py | 输入成片→输出approved/修改指令 |
| P4 | agents/discovery.py | base.py | 输入作品文本→输出评估报告 |
| P4 | executors/sourcing.py | base.py | 输入数据源→抓取文本到corpus/ |
| P5 | notify.py | config.py | 发送通知到 Telegram |
| P5 | utils/cost_tracker.py | 无 | 累加成本，输出报告 |
| P5 | utils/retry.py | 无 | 重试装饰器 |

## 十二、Agent 接口规范

所有 Agent 继承 BaseAgent，统一接口：

```python
from drama.agents.base import BaseAgent

class WriterAgent(BaseAgent):
    system_prompt_file = "drama/prompts/writer.md"

    def build_messages(self, context: dict) -> list[dict]:
        """从上下文构建 LLM messages"""
        framework = context["framework"]
        characters = context["characters"]
        episode_num = context["episode_num"]
        act = context["act"]
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"框架：\n{framework}\n\n人物：\n{characters}\n\n请写第{episode_num}集（{act}）的剧本。"}
        ]

    def parse_output(self, response: str, context: dict) -> dict:
        """解析 LLM 输出为结构化结果"""
        return {
            "script": response,
            "file": f"03_剧本/{context['act']}/ep{context['episode_num']:02d}.md"
        }
```

BaseAgent 提供统一能力：

```python
class BaseAgent:
    system_prompt_file: str  # 子类指定

    def __init__(self, config):
        self.llm = LLMClient(config.llm)
        self.system_prompt = self._load_prompt()

    def run(self, context: dict) -> dict:
        """主入口：构建消息 → 调 LLM → 解析输出"""
        messages = self.build_messages(context)
        response = self.llm.chat(messages)
        result = self.parse_output(response, context)
        result["cost_tokens"] = self.llm.last_usage
        return result

    def build_messages(self, context: dict) -> list[dict]:
        raise NotImplementedError

    def parse_output(self, response: str, context: dict) -> dict:
        raise NotImplementedError
```

### Orchestrator ↔ Agent 数据契约（务必遵守）

> 上面的 WriterAgent 仅为示意。Agent 实际拿到的 `context` 由 Orchestrator 构建，键名以本契约为准。历史上因未写清此契约，writer 读 `context["episode_num"]` 而 orchestrator 未传，导致运行时 KeyError。

**Orchestrator 传入 context 必含字段：**

| key | 类型 | 说明 |
|-----|------|------|
| `project` | ProjectConfig | 用 `get_path()` 取项目内路径 |
| `episode` | str | 集 ID，如 `ep01` |
| `episode_num` | int | 集号，如 `1`（writer/storyboard 拼文件名需要） |
| `act` | str | 幕名，如 `第一幕_灭门` |
| `state` | dict | 该集完整状态 |
| `shot` | dict | 仅镜头级任务（visual_qa / 升级）传入 |
| `extra` | dict | 额外参数（如升级原因 `reason`） |

> `episode_num` / `act` 也可统一从 `state` 取（`state["episode_num"]` / `state["act"]`）。无论走哪条，**契约必须单一明确**，agent 与 orchestrator 不能各按各的假设。

**Agent 返回 result 契约：**

- **不要包含 `status` 字段** —— 任务状态由 Orchestrator 决定并写入。若 result 带 `status`，会与 `update_task(ep, task, status="approved", **result)` 撞成"重复关键字参数"错误（`got multiple values for keyword argument 'status'`）。
- 只返回业务字段：writer → `{file}`；storyboard → `{file, shot_count, shot_ids}`；visual_qa → `{pass, notes, sub_task}`；director → `{approved, notes, ...}`。
- `cost_tokens` 由 `BaseAgent.run()` 统一补充，子类不必管。

## 十三、Executor 接口规范

所有 Executor 继承 BaseExecutor：

```python
class BaseExecutor:
    def __init__(self, config):
        self.config = config

    def run(self, task: dict) -> dict:
        """主入口：执行任务 → 返回结果"""
        raise NotImplementedError

    def validate_input(self, task: dict) -> bool:
        """检查输入是否完整"""
        raise NotImplementedError
```

Executor 接收的 task 由 Orchestrator 从状态文件构建：

```python
# text2img 的 task 示例
task = {
    "shot_id": "ep01_shot01",
    "prompt": "中景，商三官（鹅蛋脸细长眉...），站立抚琴，赵府偏厅...",
    "reference_images": ["05_美术/角色参考/商三官_正面.png"],
    "output_path": "05_美术/shots/ep01_shot01.png",
    "negative_prompt": "毁容，面部扭曲，肢体变形，多余手指，模糊"
}
# 返回
result = {
    "success": True,
    "file": "05_美术/shots/ep01_shot01.png",
    "cost": 0.5,
    "attempts": 1
}
```

## 十四、Orchestrator 调度逻辑

```python
class Orchestrator:
    def __init__(self, config, project_path):
        self.config = config
        self.project = load_project_config(project_path)
        self.agents = {
            "writer": WriterAgent(config),
            "storyboard": StoryboardAgent(config),
            "visual_qa": VisualQAAgent(config),
            "director": DirectorAgent(config),
        }
        self.executors = {
            "text2img": Text2ImgExecutor(config),
            "img2video": Img2VideoExecutor(config),
            "audio": AudioExecutor(config),
            "compose": ComposeExecutor(config),
        }

    def run(self):
        """主循环"""
        while True:
            states = self.load_all_states()
            actions = self.plan_actions(states)
            if not actions:
                log("所有任务完成，退出")
                break
            for action in actions:
                self.execute_action(action)
            sleep(self.config.tick_interval)

    def plan_actions(self, states) -> list[Action]:
        """核心调度逻辑：遍历所有集的状态，找出可执行的动作"""
        actions = []
        for ep_state in states:
            if ep_state.director_review.status == "approved":
                continue  # 已完成

            # 阶段1: 剧本
            if ep_state.script.status == "pending":
                actions.append(Action("agent", "writer", ep_state))
            elif ep_state.script.status == "approved" and \
                 ep_state.storyboard.status == "pending":
                actions.append(Action("agent", "storyboard", ep_state))

            # 阶段2: 镜头生产 (分镜通过后)
            elif ep_state.storyboard.status == "approved":
                shot = self.find_next_pending_shot(ep_state)
                if shot:
                    if shot.text2img.status == "pending":
                        actions.append(Action("executor", "text2img", ep_state, shot))
                    elif shot.text2img.status == "qa_pass" and \
                         shot.img2video.status == "pending":
                        actions.append(Action("executor", "img2video", ep_state, shot))
                    elif shot.text2img.status == "qa_fail" and \
                         shot.text2img.attempts < max_retry:
                        actions.append(Action("executor", "text2img", ep_state, shot))
                    elif shot.img2video.status == "qa_fail" and \
                         shot.img2video.attempts < max_retry:
                        actions.append(Action("executor", "img2video", ep_state, shot))
                    elif shot.text2img.attempts >= max_retry or \
                         shot.img2video.attempts >= max_retry:
                        actions.append(Action("agent", "director", ep_state, shot, "escalation"))

            # 阶段3: 后期 (全部镜头通过后)
            elif self.all_shots_approved(ep_state):
                if ep_state.audio.status == "pending":
                    actions.append(Action("executor", "audio", ep_state))
                elif ep_state.audio.status == "approved" and \
                     ep_state.composite.status == "pending":
                    actions.append(Action("executor", "compose", ep_state))
                elif ep_state.composite.status == "approved" and \
                     ep_state.director_review.status == "pending":
                    # 终审:auto 模式 → director agent;review 模式 → Action("review","director",...)（见 §十四之二）
                    actions.append(Action("agent", "director", ep_state))

        # 并行度控制
        return self.apply_parallelism_limit(actions)
```

### 结果应用契约（_apply_*_result，关键）

- **镜头初始化**：storyboard 返回的 `shot_ids` 是**字符串列表**；Orchestrator 必须用 `new_shot_state(shot_id)` 转成完整镜头 dict 再 `add_shot`，不能把字符串直接塞进 `shots`（否则后续 `find_pending_shot` 对字符串取下标崩溃，且该崩溃发生在 `plan_actions` 内、不在 try 保护中 → 进程退出）。
- **status 单一来源**：`update_task` 的 `status` 由 Orchestrator 显式给定，agent result 不含 `status`（见 §12 契约）。
- **Executor cost 一致性**：所有 executor 返回含 `cost` 字段（已统一，含 audio/compose）。

### 并行度：让 parallel_shots 真正生效

上面 `plan_actions` 的镜头分支若每集只取"下一个"镜头，则每 tick 每集最多产出 1 个镜头动作，`PARALLEL_SHOTS` 形同虚设。要让集内镜头并行：镜头分支需**枚举多个可执行镜头**（上限 `parallel_shots`）一次性产出多个 Action，再由执行层并发处理（asyncio / 线程池）。同理 `apply_parallelism_limit` 的镜头切片才有意义。

> 实际签名说明：上面伪代码为示意。实际 `Action` 为 `(type, name, episode, state, shot=None, extra=None)`，`type ∈ {"agent","executor","review"}`。终审为 `Action("agent","director",ep,state)`；镜头升级为 `Action("agent","director",ep,state,shot,{"reason":...})`。

## 十四之二、协作模式与人审（轻量聊天式）

每个环节可在 `project.yaml` 的 `production.stage_modes` 配协作模式：`auto`（默认，自动跑）或 `review`（人审）。当前已对 `director` 终审接入，可推广到其他环节。

**状态流转（review 模式的 director 终审）：**

```
composite=approved
  → director_review: pending
  → (mode=review) Orchestrator 产 Action("review","director",ep,state)
  → _execute_review: 写 director_review=reviewing + 通过 ReviewChannel/Notifier 推送待审包
  → 集挂起（plan 不再产该集动作；主循环提示"等待人工审核"）
  → 人提交回复（CLI --review-reply 或后续 IM provider）
  → 下轮 plan 先 _drain_review_replies: 取回复 → 意图解析 → 写 approved / rejected
  → approved 则完成；rejected 则停（用 --reset-review 复活为 pending）
```

**关键组件（`drama/review.py`）：**
- `ReviewChannel`：收人审回复的通道抽象（post/poll/ack）。`FileReviewChannel` 为零依赖文件实现（回复写 `.state/reviews/<ep>__<stage>.reply.txt`）；Telegram 等 IM 作为后续可插拔 provider。
- `parse_review_reply(text)`：规则意图解析 → `{decision, targets, reason}`。**保守原则**：识别不到明确"通过"即判 reject（不误放行）。`parse_with_llm` 为有真 LLM 时的升级（应对"把台词改短点"类复杂意图）。

**CLI：**
- `--review-reply <ep> <stage> "<回复>"`：提交人审回复
- `--reset-review <ep>`：把 `rejected`/`reviewing` 的集重置为 `pending` 重新激活（解决打回后无复活入口）

**当前边界**：reject 仅记录意见 + 停，不自动级联重做（"改哪个环节"待后续）；`_execute_review` 目前写死 director 终审语义，推广到 storyboard/visual_qa 等需泛化 `_review_package`。

## 十五、通知设计

通知完全可选，零配置也能跑：

```python
class Notifier:
    def __init__(self, config):
        self.channels = []
        if config.has("telegram"):
            self.channels.append(TelegramNotifier(config.telegram))
        if config.has("hermes_api_url"):
            self.channels.append(HermesNotifier(config.hermes_api_url))

    def send(self, message: str, image_path: str = None):
        for ch in self.channels:
            try:
                ch.send(message, image_path)
            except Exception as e:
                log(f"通知发送失败 ({ch.__class__.__name__}): {e}")
        # 没有任何 channel 也不报错，只写日志
        if not self.channels:
            log(f"[通知] {message}")
```

通知触发点：
- 单集制作完成
- 镜头超过重试上限被升级
- 全剧制作完成
- 错误异常

## 十六、开发约定

1. 所有文件路径用 pathlib.Path，不用字符串拼接
2. 所有配置通过 config.yaml + project.yaml，不硬编码
3. API key 通过环境变量引用（${VAR} 语法），不写明文
4. 每个 Agent/Executor 可独立测试，不依赖 Orchestrator
5. 状态文件是人类可读的 YAML，方便手动检查和修改
6. 日志用 Python logging，不用 print
7. 错误处理：API 调用失败不 crash，记日志 + 更新状态为 pending 等下轮重试
