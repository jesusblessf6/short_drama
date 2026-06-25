# 代码核对报告 — 文档 vs 实现

> 复核日期 2026-06-21。结论:架构设计本身是好的、值得保留;但 ARCHITECTURE.md/CLAUDE.md 把"骨架"描述成了"已完整实现",核心 `orchestrator.py` 实际跑不通。
> 以下每条均逐行追到 `file:line`,属核实级。唯一**未核实**项:全部修复后整条链路能否真正产出成片(需真跑 + 真 API key)。
>
> **更新① 2026-06-21(文档修复轮后):** 本报告为快照。其中"小不一致"里**属于文档的项**(制作日志目录名 09/07、README 的 asyncio/型号、cost_summary 标注)已在文档修复轮修正于 `CLAUDE.md`/`ARCHITECTURE.md`/`README.md`;因此本报告正文仍写"§10 写 09_制作日志"等描述的是**修复前**状态。
>
> **更新② 2026-06-21(系统重写轮后):** **本报告所列代码 bug 已在重写中全部解决** —— 第 0–3 关(状态初始化、context/result 契约、add_shot 类型)与设计层 4–6(并行结构、重试预算按类型、升级路径可达、cost 接线)均已修复;`orchestrator.py` 已重写,执行层加占位 provider,创意层加离线模式。`三官 ep01` 现可端到端产出真实成片(离线占位模式)。验证记录见 `VIBE_CODING_LOG.md` 2026-06-21「从0建成可跑的端到端流水线」条。**本报告正文保留作为重写前的问题快照,不代表当前代码状态。** 仍未做:真实即梦/可灵对接、真实 GLM 实测、asyncio 并行、sourcing。

## 实际运行行为(按撞墙先后)

### 第 0 关 — 开箱即停,什么都不做
- 无任何 CLI/代码创建状态文件:`init_episode` / `new_episode_state` / `new_shot_state` 定义了但**全项目无调用方**。
- orchestrator 只 `load_all()` glob 已有 `ep*.yaml`,而 `.state/` 为空 → `load_all()=[]` → `plan_actions([])` 无动作 → 立即"所有任务完成,退出"。
- 证据:state.py:114 (`init_episode` 无调用方)、orchestrator.py:140/147、`.state/` 空目录。
- **现状:`python -m drama.orchestrator --project projects/三官` 啥也不干就退出。**

> 以下各关需手工塞入状态文件才会依次触发,且每关被前一关挡着。

### 第 1 关 — writer 阶段无限失败循环(被吞,非崩溃)
- `_build_agent_context` 只塞 `episode`/`state`,**无 `episode_num`**;`writer.parse_output` 读 `context["episode_num"]` → KeyError。
- 异常被 `execute_action` 的 try/except 吞掉 → script 永远 pending → 每 tick 重跑、**重新真实调用 LLM(真烧钱)**、再失败。
- 证据:orchestrator.py:276-288、writer.py:71、orchestrator.py:258。

### 第 2 关 — `update_task` 重复关键字(修了第1关才到)
- writer/storyboard 返回 dict 自带 `status`,orchestrator 又写 `update_task(ep, "...", status="approved", **result)` → `TypeError: got multiple values for keyword argument 'status'`。
- 同样被吞 → 死循环。
- 证据:writer.py:85、storyboard.py:79、orchestrator.py:307、orchestrator.py:309。

### 第 3 关 — `add_shot` 类型错(唯一真进程崩溃)
- storyboard 返回 `shot_ids` 为**字符串列表**;orchestrator `add_shot(ep, shot_id)` 把字符串塞进 shots;下一 tick `find_pending_shot` 对字符串取 `shot["text2img"]` → TypeError。
- **此处在 `plan_actions` 内,不在 try 保护中 → 进程崩溃退出。**
- 证据:storyboard.py:82、orchestrator.py:311、state.py:144-149、state.py:155、orchestrator.py:147。

## 设计层面(崩溃全修后仍错)

### 4. "集内镜头并行"是空的
- `plan_actions` 每 episode 追加一个 action 即 `continue` → 每集每 tick ≤1 动作;`_apply_parallelism` 的 `shot_actions[:max_shots]` 永远 ≤1,`parallel_shots` 配置死的。
- 执行仍是同步 `for` 循环;README 写"asyncio",代码无 asyncio。
- 证据:orchestrator.py:173-216、orchestrator.py:428、orchestrator.py:154-155、README.md:62。

### 5. 重试预算错配 + 升级路径不可达
- `find_pending_shot` 硬编码 `attempts < 5` → **重试耗尽的镜头对调度器隐形**,既不再重试也进不了升级分支(orchestrator.py:234/244 不可达);`all_shots_approved` 又因坏镜头永远 False → **整集静默卡死,无升级、无通知、无报错**。
- img2video 错用 text2img 的预算 `t2i_max`;project.yaml 的 `img2video_simple/complex/lip_sync` 全是死配置。
- `escalated` 状态枚举无人设置。
- 证据:state.py:161/163、state.py:172、orchestrator.py:225/241/244、project.yaml:36-38、state.py:16。

### 6. 成本追踪未接线
- `cost_tracker.py` 全项目无调用方;`new_episode_state` 无 ARCHITECTURE §7 的 `cost_summary` 字段。每任务有 `cost`/`cost_tokens` 字段但无聚合、无总成本输出。
- 证据:grep `cost_tracker` 无业务调用、state.py:19-54、ARCHITECTURE.md §7。

## 小不一致
- 日志目录:project.yaml:18 `logs: 07_制作日志/` vs 架构树 `09_制作日志`,且与 `07_成片` 撞号。
- visual_qa 在 executor 结果里**同步内联**触发(orchestrator.py:347),非独立状态阶段,与流水线图 [8][10] 把 QA 画成独立步骤的讲法不一(设计可接受,文档讲法不一)。

## 建议修复顺序(= 撞墙顺序)
0. **状态初始化**:加 `--init` CLI,用 `project.yaml` 的 acts 批量 `init_episode`,让系统有东西可跑。
1. **context 契约**:`_build_agent_context` 补 `episode_num`(或统一约定从 `state` 取),写成明确规范。
2. **result 契约**:统一 agent 返回 dict 不含 `status`,由 orchestrator 决定状态;或 `update_task` 改为接受单一来源。
3. **add_shot**:改为 `add_shot(ep, new_shot_state(shot_id))`,orchestrator 引入 `new_shot_state`。
4. **并行 + 升级 + 重试预算**:消除 `find_pending_shot` 与 `_plan_shot_action` 的双份真相;让升级路径可达;img2video 用正确预算;再谈真并行(asyncio/线程池)。
5. **成本聚合、文档校准**(把"已完成"措辞改为与代码现状一致)。
