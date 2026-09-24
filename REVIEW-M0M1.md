# 代码评审 — M0 测试基线 + M1 真实调用与恢复能力

> 评审日期 2026-09-25。评审对象：提交 `bc4219c`（M0：47 例离线回归 + CI）、`fe789e3`（CLAUDE.md 快照）、`3eaa4ce`（M1：demo/production 双模式、状态扩展、原子写+锁、错误分类、外部任务恢复、三级预算；重点）。
> 评审性质：**仅评审，不修改代码**（延续本会话约定）。结论落到本文，供后续修复轮逐条处理。
> 评审方法：通读 M1 全部改动源码（orchestrator/state/config/retry/project_lock/executors）+ 测试清单与关键断言抽查；**全套测试本机实跑**（81 passed / 23.7s）；三官 ep01 重置状态磁盘核查；与 VIBE 日志声明逐条对照。

---

## 总体结论

**M1 是一次高质量的落地，六项声明全部属实，测试真实通过。** 架构上最有价值的是"单一决策路径"原则的贯彻——重试/升级判定收敛到 `_plan_shot_action` 唯一一处（旧版 `find_pending_shot` 与 plan 双份真相导致静默卡死的教训被明确吸收），`failed` 显式终态取代"停滞中止"，预算拦截在执行层不动状态、留给下一轮 plan 走正规路径。发现 **2 个 P2（假"全部完成"通知、占位闸门不查 audio）+ 2 个 P3 + 1 个 nano**，均不阻塞 M2 启动。

## 声明核验（VIBE 日志 vs 实现）

| 声明 | 核验 | 判定 |
|---|---|---|
| demo/production 双模式 + 启动校验 | `config.py:151-170`（placeholder/缺 key/价格≤0/离线 LLM 四类拒绝），`TestModeValidation` 6 例 | ✅ |
| 状态扩展 + 旧 YAML 迁移 | `_SHOT_SUB_DEFAULTS` 五字段，`migrate_state` 就地补全；ep01 实测迁移后又经 reset | ✅ |
| 原子写 + 运行锁 | `state.py:173-183`（tmp+fsync+os.replace）；`project_lock.py`（flock EX\|NB，拿不到立即退） | ✅ |
| 错误分类：可重试/终态分流 | `retry.py:31-63`（httpx 精确 + 字符串兜底）；fatal→`failed` 终态 + `attempts_log` | ✅ |
| 外部任务恢复框架 | 派发前落 `generating`+`input_hash`；`submitted`+task_id 在途不结算 attempts；`reset_interrupted` 保 ID 不盲重置（防重复扣费） | ✅（框架级；占位同步不触发轮询，真实 API 下未验证——VIBE 已自认） |
| 三级预算 plan+派发双检 | `_generation_budget_block`（:395-417）+ 执行时 fresh 重载复检（:484-498）；e2e 测试 `test_budget_exhaustion_stops_new_paid_tasks` 盯**真实提交次数**（25 元预算恰好 2 次），断言收敛不误报 | ✅ 测试设计好 |
| 测试 81 例 | 本机实跑 `81 passed in 23.72s` | ✅ |
| 三官 ep01 占位 approved 已作废 | 磁盘核查：五环节全 pending、shots 清空、cost_summary 保留（占位成本为 0） | ✅ |
| 整集失败终态取代停滞中止 | `_fail_episode`（:426-436）双写 composite+director_review；plan 跳过 failed 集；`--reset-episode` 复活 | ✅ |

## 问题清单

### P2-1｜预算阻断下发出假"✅ 全部完成"通知

- 位置：`orchestrator.py:184-205`（no-action 分支）与 `:360-365`（预算阻断仅 `_notify_budget_once` 一次 💰）
- 场景：集/项目级预算耗尽 → 所有待产镜头 plan 返回 None → 无动作 → 退出分支既非 reviewing/rejected/failed → 发"✅ 项目当前可执行任务已全部完成"。状态层没错（`test_budget_exhaustion_stops_new_paid_tasks` 断言了 director_review 保持 pending），但**通知语义是假成功**——用户先收到 💰 再收到 ✅，极易误读。
- 建议：no-action 分支先查 `self._budget_notified` 非空（或状态显式标 `budget_blocked`），有预算阻断时改发"⏸ 预算耗尽挂起，调整 budget 后重跑"，不发 ✅。补一条断言退出消息的测试。

### P2-2｜`_placeholder_approved` 闸门只查 shots，漏 audio 降级链

- 位置：`orchestrator.py:222-244`
- 场景（正是文档化工作流）：demo 模式接真实付费 provider 试跑（demo 允许）→ audio 失败静音降级 `degraded=true / source=silent_fallback` 且 approved → 切 production。闸门只检查 shots 的 t2i/i2v `source`（真实），放行 → 该集被当完成，**正式成片静音**。
- 建议：闸门补查 `audio.degraded` 为真或 `audio.source in (None, "silent_fallback")`；composite 同理可查 `source`。防 demo→production 切换时的静音漏网。

### P3-1｜CLI 子命令绕过运行锁

- 位置：`orchestrator.py:911-929`（`--init`/`--review-reply`/`--reset-review`/`--reset-episode` 在 `run()` 之前 return，不经 `ProjectLock`）
- 风险：与运行中的调度器并发写同一状态文件可竞态（锁目前只保护 run 循环）。现实用法多为"停机再重置"，风险低，但锁的语义留了洞。建议：写状态的三条子命令也 `try: lock.acquire()`；至少在 `--help`/文档注明"先停调度器"。

### P3-2｜每 tick 无差别重写全部状态文件

- 位置：`orchestrator.py:178-180` + `state.py:262-277`
- `reset_interrupted` 后无条件 `save`，无变化的集也重写（updated_at 漂移、无谓 IO）。建议 `reset_interrupted` 返回是否变更，仅变更时保存。当前规模（几十集、循环短）无实际危害，属卫生问题。

### Nano（记录备查，不要求改）

1. `save` 原子写未 fsync 父目录——POSIX 崩溃后 rename 可能不持久；本项目规模可接受。
2. `retry.py` 装饰器当前无调用方——已有注释声明"为真实 provider 预留、勿删"（`retry.py:9-13`），处理得当；M2 接线时记得兑现。

## M0（`bc4219c`）顺带核验

- CI（`.github/workflows/ci.yml`）：3.11/3.12 矩阵、apt ffmpeg、`pip install -e ".[dev]"` 依赖检查、离线原则在注释中显式声明——配置合理。
- conftest 隔离：tmp_path 隔离项目 + LLM offline/视觉 placeholder/edge-tts 打桩失败→静音轨，与"三官零改动"声明一致（M1 提交中 `.state/ep01.yaml` 的变更是显式 reset 所致，非测试污染）。
- 未逐行复核 M0 全部 47 例断言（本轮重点 M1）；全套通过 + M1 关键断言抽查未见空转测试。

## 建议修复顺序

1. **P2-1 + P2-2**（一次小改：no-action 分支语义 + 闸门补 audio 查询，各补一条测试）——建议进 M2 前顺手清掉，两者都是"正式投产会踩"的坑。
2. P3-1/P3-2：卫生项，可与 M2 任意批次合并。
3. M2 启动前置（非本轮问题，重申 DEVELOPMENT_PLAN 约定）：刷新 `references/模型选型_2026-06.md`（已 3 个月）；外部任务轮询间隔需在真实异步 API 接入时配置（占位同步不触发，停滞检测覆盖不到该路径）。
