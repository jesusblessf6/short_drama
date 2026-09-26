# Vibe Coding Log

> 本系统(drama/)的 AI 辅助开发会话日志,**倒序**(最新在上)。
> 与 `projects/三官/07_制作日志`(拍剧的制作日志)无关。
> 每条记:**做了什么 · 关键决策与取舍 · 改动文件 · 学到 / 遗留**。

---

## 2026-09-26 — 判断层 Jev 接入 P1（mock 落地）：三决策点激活、失败链闭环（测试 126 例）

**User Prompt:** "先 mock jev 的接入配置，后面也可能会换本地的开源判断型模型"（承接上轮 `references/Jev决策层引入评估.md` 的 P1）。

**Done:** 判断层全量落地（新增 `tests/test_judgment.py` 19 例，107→126 全绿）：
- **`drama/judgment.py`**：`Decision`（value/confidence/backend + 弃权语义）、`RuleDecisions`（**有意全弃权**——现有路径已覆盖 rule 语义，复制逻辑=双份真相）、`JevDecisions`（TypeSafe wire 协议 `POST /v1/systemone`，mock=true 走确定性启发式且**响应 wire 格式保真**——解析/阈值/回落代码全部真实受测）、`build_decisions` 工厂。
- **三处接线**（均在 Orchestrator，判断不进 Agents/Executors）：①`_parse_review` 高置信采信、unclear 弃权走保守复核（choice→verdict dict 契约转换）；②升级处置先问判断层、高置信直接落 escalated **跳过 director LLM 调用**（qa_notes 审计 backend+conf）；③红线预检闸门（剧本→分镜之间）`redline_gate: off|log_only|block`，决策记录 `script.redline_check` 幂等，block 命中 → 生成前打回。
- **失败链**：Jev 任何异常 → 弃权（jev-error）→ 既有路径；置信度 < min_confidence 视同弃权——绝不阻塞、绝不静默放行。
- **换本地模型**：endpoint 参数化，jevos 同 wire 协议，本地端点免 key（`is_local_endpoint`）；production 校验：jev 未 mock 需 key 或本地端点、`block` 必须 provider=jev（规则做不了语义判断，诚实失败）。
- 配置：`judgment:` 段（默认 provider rule / mock true / log_only——**默认行为与历史逐字节一致**，107 例旧测试零改动全过）。

**Why:** 三个教训：①mock 的价值在"格式保真"——模拟响应按真实 wire 格式给，客户端解析代码才是真测试；②mock 置信度要和默认阈值协调（0.88 < 0.9 会让"高置信自动决策"路径永远测不到，先暴露为测试失败）；③测试助手的 kwargs 分流要显式（`max_retry` 是 project 参数被误吞进 judgment 配置，默认预算触发停滞检测提前中止——状态指纹不含 attempts，重试轮次看起来"无进展"）。

**Next:** P2 等拍板（TypeSafe key 或本地 jevos 端点，`judgment.endpoint` 改地址即可）；真实判断上线前用 M2 真实样片校准阈值。M2 主体仍等用户输入（方舟 key/预算/画风）。

---

## 2026-09-25 — M2 无需拍板部分全量落地：选型刷新+校验修复+媒体验证+抽帧+音色+字幕+对齐（测试 107 例）

**User Prompt:** "现在按照计划开发吧。无需拍板的需求都做掉。"（中途中断两次，"重试重试"/"重试"续跑）。

**Done:** M2 中不依赖用户输入的全部工作（M0 47 + M1 40 + M2 20 = 107 例全绿）：
- **选型刷新（M2-1 前置）**：web 调研后写 `references/模型选型_2026-09.md`——关键发现：即梦 API 已开放且入口就是火山方舟（2025-09），故推荐全栈收口方舟单账号（seedream-4-0 ≈¥0.216/张、Seedance 2.0 ≈¥1/秒、豆包 TTS ≈¥1.3/千字），单集生成成本预估 ≈¥55-90；旧 2026-06 快照标记过期。仅产推荐，拍板项（账户/模型/预算/画风）列清单等用户。
- **结构化校验+修复循环（M2-2）**：新 `agents/validation.py`（repair_shots 机械修复 + validate_shots fatal/warning 分级）；storyboard run() 重写为级联：裸输出校验→LLM 自修复（max_format_repairs，默认 2）→机械兜底→仍硬伤抛错。**关键教训：_parse_shots 原本自己掩蔽空 prompt，校验永远看不到硬伤、修复循环成死代码——掩蔽兜底必须从解析层移除，交给修复级联**。
- **内容红线（M2-2）**：writer prompt 加红线节（血腥/自残/色情/违法细节→暗场化叙事），director 审核维度加红线项（开发计划 M2-2 的合规前置措施）。
- **媒体验证（M2-4）**：新 `utils/media_check.py`。**本机根因排障：/Users/wing/env/ffmpeg 只有单个 ffmpeg 二进制、无 ffprobe → 新验证一接入就把全部占位视频判死（9 例挂）**。解法：resolve_ffprobe（配置→ffmpeg 同目录→PATH）+ `ffmpeg -i` stderr 解析兜底，CI（apt ffmpeg 有 ffprobe）与本机两路都通。
- **视频抽帧质检（M2-4）**：10%/50%/90% 三点抽帧到 `08_质检/frames/`；visual_qa 真实模式逐帧 vision 检查、全过才过；调度器 `_run_visual_qa` 组装 frames 上下文；离线仍短路。
- **音色映射（M2-5）**：project.yaml `production.voice_map`（三官已配商三官/商士禹）+ 全局 narrator_voice；调度器注入 audio task。
- **逐句时间戳+字幕（M2-5）**：新 `utils/subtitles.py`；audio 逐句 ffprobe 实测时长→累加句首（可解释对齐）→ `ep01.srt`+`ep01.lines.yaml`；audio.subtitle_file 入状态→compose 取用；静音降级同样产字幕（degraded 不变）。
- **音画对齐修正（M2-5）**：compose 弃 `-shortest`→tpad 末帧克隆补齐（视频<音频）/保留完整视频（音频<视频），对齐策略入结果；字幕烧录优先、缺 libass 回退 mov_text 软轨；compose 产物过 ffprobe 验证。
- 配套：conftest `_edge_tts` mock 对齐新签名 `(lines, out, cfg, voice_map)->(bool, timings)`；新增 `tests/test_m2.py` 20 例。

**Why:** 三个教训值得记：①"兜底掩蔽"与"校验修复"天然冲突——解析层兜底会让校验失明，容错必须放在修复级联的末端而非解析层；②本机 ffprobe 缺失教会我们：验证逻辑要么自带降级路径，要么第一次接入就会把绿灯全变红灯；③lavfi color 源加 `d=1` 会让源只有 1 秒（-t 拉不长源），测试生成器踩过。

**Next:** M2 剩余全部等用户拍板（选型文档 §四：方舟账户+key、模型确认、预算上限、画风参考）→ 到位后核实控制台实价、接 provider（异步轮询用 M1 契约）、做 30-60s 技术样片。

---

## 2026-09-25 — 落实 M0/M1 评审意见：P2×2 + P3×2 全清（测试 87 例）

**User Prompt:** "看看评审意见"（评审会话产物 `REVIEW-M0M1.md`：六项声明属实，P2×2/P3×2/nano×2）。

**Done:** 逐条核实后全部落实（测试 81→87 例）：
- **P2-1**：`_run_loop` no-action 分支新增 `elif self._budget_notified` → 发"⏸ 预算耗尽挂起"，不再发假"✅ 全部完成"；测试断言通知文本（`test_budget_block_never_sends_fake_success`，monkeypatch notifier 收集消息）。
- **P2-2**：`_placeholder_approved` 闸门补查 audio 链（`degraded=true` 或 `source in (None, silent_fallback)`）——堵住"demo 真实 provider 试跑→切 production 成片静音"的漏网路径；补 3 例（静音链拦/旧态无 source 拦/全真实不误伤）。
- **P3-1**：main() 中 `--init/--init-episode/--review-reply/--reset-review/--reset-episode` 五个写状态子命令纳入 ProjectLock 互斥（run 自身已锁；--status 只读不放）；子进程 e2e 测试：持锁 → reset 拒执行且状态未变 → 释放 → 成功。
- **P3-2**：`reset_interrupted` 返回变更标志，主循环仅变更时 save；`test_state.py` 旧契约断言同步更新。
- nano×2 按评审意见不改（fsync 父目录可接受；retry 装饰器预留待 M2 兑现）。

**Why:** 评审指出的共同盲区是"测试断言了状态层、没断言用户可见语义"——P2-1 的测试因此直接盯通知文本而非状态；P3-1 锁的入口放 main()（reset_* 本身无锁语义，进程退出即释放 flock，不留死锁）。

**Next:** M2 前置（非本轮问题，重申）：刷新 `references/模型选型_2026-06.md`；真实异步 API 接入时配轮询间隔。等待用户 M2 输入（key/预算上限/画风）。

---

## 2026-09-25 — 评审 M0/M1 代码提交（新开代码评审轮，评审意见落 REVIEW-M0M1.md）

**User Prompt:** "评审一下新的提交"。

**Done:** 识别新提交为代码（非行业文档）：`bc4219c`（M0 测试基线+CI）、`fe789e3`（CLAUDE.md）、`3eaa4ce`（M1，重点）。通读 M1 全部改动源码（orchestrator/state/config/retry/project_lock/executors）+ 测试断言抽查；本机实跑全套 **81 passed/23.7s**；磁盘核查三官 ep01 重置状态。评审意见落 `REVIEW-M0M1.md`（新文件，不混历史快照 REVIEW.md）。结论：六项声明全部属实、架构上"单一决策路径"贯彻得好；发现 **P2×2**——①预算阻断下发假"✅ 全部完成"通知（状态没错、通知语义假成功，测试只断言了状态）；②`_placeholder_approved` 闸门只查 shots 不查 audio 静音降级链（demo 真实 provider 试跑→切 production 的文档化工作流会漏网）——加 P3×2（CLI 子命令绕锁、每 tick 无差别重写状态）+ nano×2。

**Why:** 两个 P2 的共同点是"测试断言了状态、没断言用户可见语义"（通知文本/闸门覆盖面）——M1 测试在状态层很扎实，但出口消息和防线下游（audio）是盲区。评审轮次与对象切换时显式开新文档（代码评审与行业文档评审分离），沿用上轮约定。

**Next:** P2×2 建议 M2 前顺手清（各一条测试）；P3 与 M2 批次合并；M2 前置仍需刷新模型选型文档 + 配置轮询间隔。

---

## 2026-09-25 — M1 全量落地：真实调用与恢复能力（六项全完成，测试 81 例）

**User Prompt:** "本会话是开发会话" → "按计划继续吧"（按 `DEVELOPMENT_PLAN.md` M1 执行）。

**Done:** M1 六项工作全部实现并通过测试（47→81 例，新增 `tests/test_m1.py` 34 例）：
1. **demo/production 双模式**：`config.yaml` 顶层 `mode`；`Config.validate_production()` 启动校验（placeholder/缺 key/价格未知→拒跑）；静音降级显式标记 `degraded`——demo 放行、production 拒绝自动通过并整集 failed。
2. **状态扩展**：镜头子任务加 `source/error_class/external_task_id/input_hash/attempts_log`；`migrate_state()` 兼容旧 YAML（ep01 实测迁移）；`failed` 入枚举。
3. **原子写 + 运行锁**：`save` 走 tmp+`os.replace`+fsync；新增 `utils/project_lock.py`（flock，拿不到锁立即退出，防 CLI/机器人双写）。
4. **错误分类**：`classify_exception()`（timeout/rate_limit/network/unknown=可重试；auth/param=failed 终态）；`BaseExecutor.fail()` 统一携带。
5. **外部任务恢复框架**：派发前落 `generating`+input_hash；executor 契约支持 `{submitted, external_task_id}` 异步在途；中断重启后凭 task_id 轮询恢复、不重复提交付费任务（真实 provider M2 兑现）。
6. **三级预算**：`budget.per_shot/episode/project_cny`；plan 期+派发期双检（修掉"同轮批量派发用旧快照超支"缺陷）；镜头级超额→升级终态、集/项目级→挂起不误判。**三官 ep01 占位 approved 已实际作废重置**（`--reset-episode`，磁盘文件未动，cost 保留）；production 启动还会拦截占位链 approved 集防死局复发。
- 行为变更：全镜头升级→显式整集 failed 终态（取代停滞中止），`test_retry_escalation.py::TestAllShotsFail` 断言已更新锁定新契约。
- 文档：CLAUDE.md 同步（目录树修掉 drama/ 重复块、M1 状态、切正式模式清单）。

**Why:** 单一决策路径——执行时预算拦截不直接落终态而是留给下轮 plan 走 director 升级；作废保留 cost_summary（钱已花，防反复作废烧钱）；降级拒绝只在 production（demo 静音降级是离线 e2e 的腿，砍了基线就断）。

**Next:** M2（需用户输入：provider key/预算上限/画风参考；先刷新 `references/模型选型_2026-06.md`）；M1 框架中 `submitted` 轮询在真实异步 API 接入时需配轮询间隔（当前占位同步完成，不触发停滞检测）。

---

## 2026-09-24 — M0 开发启动：测试基线 47 例 + CI + 文档校准

**User Prompt:** 用户纠正"怎么变成在写文章了"（已存记忆 avoid-meta-work-drift）→ "如果评审收口的话，直接开始进入开发阶段，能做多少做多少"。

**Done:** 按 `DEVELOPMENT_PLAN.md` M0 执行，自评估 M0 全部四项完成：
- **tests/ 新建 47 例**（6 文件）：离线端到端（auto 全流程产出真实 mp4 + 产物/路径/成本校验）、断点续跑（分段执行、已完成环节不重做、generating 中断重置）、阶段过滤、人审全流程（review 挂起/approve/打回镜头定位/保守 reject/reset 复活再送审）、重试与升级（预算按 shot.type 取值、耗尽升级 Action、escalated 终态收敛、升级后整集继续出片）、全镜头失败安全降级、config/state/review 单元测试、CLI 子进程冒烟。
- **隔离纪律**：全部 tmp_path 隔离项目 + conftest 零外部依赖（LLM offline:true 空 key、视觉 placeholder、edge_tts 打桩失败→ffmpeg 静音轨）。实测 `projects/三官` 零改动。
- **CI**：`.github/workflows/ci.yml`（py3.11/3.12 矩阵 + apt ffmpeg + pip install 依赖检查 + pytest）。
- **文档校准**：README 加测试命令；CLAUDE 实现状态补 M0 基线条、待实现 #7 注明下限行为已被测试锁定。

**Why（M0 任务 2 行为核实结论 + 新发现）:**
- 核实通过：重试判定唯一处 `_plan_shot_action`；升级路径可达且收敛（escalated 镜头不进成片、整集照常终审通过）；断点续跑只补缺不重做；人审保守解析不误放行；reset_review 走公开接口可复活。
- 新发现①：audio executor 对**未知 provider** 直接返回失败、不走静音降级（与 edge_tts 异常会降级不一致）——但未知 provider 属配置错误，响亮失败反而合理，记录待定未改。
- 新发现②（CLAUDE #7 实证）：全 i2v 升级 → compose 0 片段 → 失败 → 停滞检测 3 轮中止；不误报完成 ✓、无显式"整集失败"终态（留 M1+）。
- 教训：升级判定测试必须先把 text2img 置 approved——我首次写错暴露的正是真实守卫"i2v 必须先有图"。

**Next:** M0 完成，下一步 M1：demo/正式模式区分（正式禁静默降级）、外部任务提交/轮询/恢复、预算字段、原子写状态、ep01 占位 approved 状态作废（M1.6）。真实 provider 接入仍等用户 key/账户/预算输入。

---

## 2026-09-24 — 第四轮评审《短剧投流体系.md》——v1.0 定稿核验，评审关闭

**User Prompt:** "有新的提交"。

**Done:** 通读 v1.0（相对 v0.11 变动 5 处），核验 R1/R2 全部落实：R1 §9.1 加跨厂商口径注 + §十四回查条目，且超出建议——补下载侧自洽旁证（1148 万×12≈1.4 亿 vs 1.86 亿）、"毛 vs 净收入"假设明确标"未核实"、给出"勿互换混用"引用纪律；独立复算新增内容全部算术/一致性通过；无新引入问题。评审文档加「第四轮核验（收尾）」节并关闭：四轮闭环、累计 12 项问题全部清零、v0.9→v1.0 定稿成立。

**Why:** R1 的处理是本轮亮点：修订会话没有停在"加注"，而是先做分歧定位（下载侧自洽→分歧在收入侧）再提假设并标注未核实——与文档自身口径纪律同构。四轮后无新发现即主动关闭，避免为关而关的无限复审。

**Next:** 评审流程结束。后续如再动该文档（四个覆盖缺口增补、§十四三条待核实回查），按 v1.x 开新评审轮，不复用本已关闭的意见文档结构。

---

## 2026-09-24 — 第三轮评审 R1–R2 核实 + 落实（v1.0 定稿，三轮闭环）

**User Prompt:** "有新的评审"。

**Done:** 核实 R1–R2 全部属实，落实（`短剧投流体系.md` → **v1.0 定稿**）：R1 §9.1 加 ReelShort 2025 收入跨厂商口径差异注（点点全年内购 3.32 亿美元 vs Sensor Tower 月度年化 ≈5.2 亿；独立复算张力成立，补旁证"下载侧自洽、分歧集中在收入侧"，毛流水 vs 扣分成净收入口径差为量级推断、已标明未核实），§十四 待核实加回查条目；R2 两处笔头勘正（头部"落实完毕"日期 09-23→09-24、版本历史引文对齐实文"自此"）。按评审结论标 v1.0，版本头与版本历史记录三轮闭环。评审文档加"第三轮处理结果"节。

**Why:** R1 是"修复动作本身制造新一致性风险"规律的又一实例——3.32 亿在来源清单、4348 万在表格时各自无害，G4 升入正文同节才成同页冲突；处置采评审建议 (a) 口径注而非 (b) 回查原文，回查转待核实跟踪（与 12.8 万部条目同模式）。毛 vs 净口径假设只作量级推断入注、不写成结论——5.2 亿 ×0.7 ≈3.6 亿接近 3.32 亿，但商店覆盖/Web 端支付等因素未排除，须回查原始报告。

**Next:** 投流体系 v1.0 定稿，三轮评审闭环。遗留非阻塞：§十四 待核实 3 条（12.8 万部/95%、ReelShort 收入口径、漫剧出海定量）+ 四个覆盖缺口（音乐版权优先，涉 compose/BGM）。改动未提交 git。

---

## 2026-09-24 — 第三轮评审《短剧投流体系.md》（核验 v0.11）——必改项清零

**User Prompt:** "有新提交，请评审"。

**Done:** 通读 v0.11（相对 v0.10 变动 7 处），核销 G1–G4 全部落实：G1 §三项目模块名泛化且根因被定位（上轮 grep 漏搜模块名）、G2 来源清单加存疑标记、G3 预测标记勘正为 7 处（独立重数相符）、G4 枫叶互动数据升入 §9.1。**新发现 R1**：ReelShort 2025 收入正文内不自洽——`:326` Sensor Tower"2025-05 单月 4348 万美元"年化 ≈5.2 亿 vs `:331` 点点数据"全年 3.32 亿"（月均 2767 万，且 +96.58% 增长下缺口应更大）；系 G4 升入正文后两数才同节并存，属跨厂商估算口径差异，按文档自身纪律应补注。另有 R2 两处笔头（`:4`"落实完毕"日期应为 09-24；`:622` 引文措辞与实物不一）。结果追加至评审文档「第三轮评审」节。

**Why:** R1 印证一个复审规律：**修复动作本身会制造新的一致性风险**——数据从来源清单（孤立）升入正文（同节并存）后，原本无害的跨源差异变成了同页冲突。第三轮对 G4 新增块做同节交叉核对，正是为此；只核销清单不查新增内容，R1 就漏了。

**Next:** 三轮闭环完成，v0.11 ≈ v1.0 候选，可定稿。遗留非阻塞：R1 口径注 + R2 笔头（下轮顺手）、四个覆盖缺口（音乐版权优先）、§十四两条待核实。改动未提交 git。

---

## 2026-09-24 — 第二轮评审 G1–G4 核实 + 落实（v0.11）

**User Prompt:** "复评发现新问题，请查阅"。

**Done:** 逐条核实 G1–G4 全部属实，全部落实（`短剧投流体系.md` → v0.11）：G1 §三"对 AI 短剧工厂的启示"（`writer`/`storyboard`/`executor` 模块名）泛化为"对 AI 内容流水线的启示"+ 加系统层面分析指引；G2 来源清单 95% 条目加存疑标记；G3 预测标记账目勘正（实为 7 处标记/4 节，原"5 处/§9.3"误）；G4 枫叶互动"营收 57.21 亿仍净亏 0.86 亿"等数据升入 §9.1 正文。头部定位声明由"自 v0.10 起"勘正为"自 v0.11 起"（v0.10 实际不符）。评审文档加"第二轮处理结果"节、第一轮清单补勘正注记。

**Why:** G1 是我上轮的失误——清残留时 grep 只搜"本项目|本系统|我们"、漏搜模块名与产品名，致使"不再含任何项目级内容"的自我声明失真。第二轮评审的方法论值得记下：**审计修订版新增的自我声明**（"零丢失""不再含"），而不只核销旧清单——绝对化声明本身就是新的未核实断言。教训：写"任何/全部/不再含"前，验证 grep 必须覆盖所有指代形态（模块名/产品名/代号/文件名）。

**Next:** 四个覆盖缺口（演员经济学/音乐版权/小程序生态/投流方组织形态）仍待新一轮调研，音乐版权优先（涉 compose/BGM）。改动未提交 git（工作区已积累：DEVELOPMENT_PLAN.md、评审意见、投流体系 v0.11、系统层面分析、本日志）。

---

## 2026-09-23 — 第二轮评审《短剧投流体系.md》（核验 v0.10 修订）

**User Prompt:** "已修订，再评审一下"。

**Done:** 通读 v0.10 全文，逐项核销上轮 6 条清单（全部落实，其中 2 项超出要求：中文在线–枫叶互动溯源式事实更新、§14"零丢失迁移+映射记录"）；交叉核实修订版两个自我声明——"零丢失迁移"属实（`系统层面分析.md:117-123` 四条逐一映射），"不再含任何项目级内容"**不属实**：§三 `:120` 仍有 `writer`/`storyboard`/`executor` 模块名与"AI 短剧工厂"残留（v0.10 自称清理两处、漏了第三处）。新发现：G1 该残留（必改）、G2 来源清单 95% 条目未带存疑标记、G3 预测标记计数写 5 处实为 7 处、G4 新来源"枫叶互动营收 57.21 亿仍净亏 0.86 亿"未升入 §9.1 正文。结果追加至 `短剧投流体系-评审意见.md`「第二轮评审」节并更新头部状态。

**Why:** 复审不只核销清单，还要验证修订版**新增的声明**——"零丢失""纯行业分析"这类自我声明若不审计，本身会成为新的未核实断言（G1 即由此抓出）。另确认修订会话对我上轮 P0 论证时间线错位的指正成立（治理 2026-04 起 vs 12.8 万是 Q1 数据），已在第二轮评审中明示。

**Next:** G1–G3 交修订会话处理（段落级小改）；G4 与四个覆盖缺口（音乐版权优先，涉及 compose/BGM）待下一轮调研。

---

## 2026-09-23 — 投流体系评审意见核查 + 修订清单全量落实（v0.10）

**User Prompt:** "有评审意见，请查阅"（他会话产出的 `短剧投流体系-评审意见.md`）→ 我核实评估后，"好的"（批准按清单落实修订）。

**Done:**
- 核查评审质量：抽查 8 处行号引用（:278/:407/:492-501/:336/:430-431/:201/:344/:12）全部吻合，分级克制，判定可信可执行。
- 修订 `短剧投流体系.md` 至 v0.10：① P0——§7.2"12.8 万部/95%"降级为定性、数字移入待核实；② 删除原 §十四（经核对 `系统层面分析.md` §六 已完整吸收原 4 条建议、零内容丢失），原 §十五→§十四；③ §9.1 中文在线–枫叶互动关系修正；④ 点众"上新"/九州"制播"口径标注；⑤ 预测值 5 处加"（预测）"标记 + 头部标记约定；⑥ 版本头改 vN+最后更新、九次增补移入文末"版本历史"节；⑦ 来源列表补钛媒体条目；⑧ 验证时发现评审漏标的两处项目指向残留（§5.2 标题"与本项目最相关"、§五"对本项目的含义"），一并改为行业表述——否则与新版本头"不再含项目级内容"声明自相矛盾。
- **联网核实的意外发现**：评审 P1"中文在线–ReelShort 措辞"实为**事实更新**——钛媒体/达摩财经 2026-08-05 报道：中文在线 2023 年已让渡枫叶互动控制权（持股 49.16% 仅第一大股东、Chapter LLC 表决权 51%），2026-05-29 枫叶互动在加州法院起诉中文在线，中文在线自认"财务投资人、非控股股东"。原文"ReelShort（枫叶互动/中文在线）"确已过时。
- 同步改：`系统层面分析.md`（"15 节"→"14 节"、§六 注明已删除）、`评审意见.md`（头部落实状态 + 清单逐项打勾带注记）。

**Why:** 评审接受照单执行，但其论证本身也过了一遍核查——发现 P0 用"治理后 350 部/日"对照 12.8 万存在时间线错位（治理 2026-04 起、数据是 Q1），落实时改用更强论证（峰值 2000+/日 × 91 天 ≈ 18 万，仅抖音单平台即可能超"全行业 12.8 万"），结论不变、论证更硬，修正已注记进评审清单。中文在线–枫叶互动关系靠 firecrawl 拿一手报道关闭，未采信记忆（我记忆停在"2018 收购"——收购史没错，但控制权 2023 已变，记忆半过期）。

**Next:** 可选增补 4 个覆盖缺口（演员经济学/音乐版权/小程序生态/投流方组织形态）未启动，需新一轮调研；音乐版权与 compose/BGM 环节直接相关，建议优先。行业文档正文自此回归"纯行业分析"定位。改动均未提交 git。

---

## 2026-09-23 — 评审《短剧投流体系.md》，评审意见落独立文档（仅评审，不改原文）

**User Prompt:** "你看下有没有问题"；随后明确"当前对话仅作评审，不涉及修改代码，需要将评审意见落到文档"。

**Done:** 通读 `references/短剧投流体系.md`（2026-06-22 第九次增补版，612 行），做内部一致性核查，评审意见落到新文件 `references/短剧投流体系-评审意见.md`，含 P0/P1/P2 分级 + 修订落实清单。原被评审文档未动。

**Why:** P0 只有一处——§7.2"12.8 万部/AI 占比 95%"：与 §11.4 日上新口径（峰值 2000+/治理后 350）互推对不上，95% 过于极端，虽原文有 ⚠️ 但仍是肯定句式，下游引用易丢警告，建议降级进 §十五待核实。其余为复核项（中文在线–ReelShort 关系措辞、点众"上新"/九州"制播"口径并列）、定位问题（§14 待迁出仍占正文节序）与覆盖缺口（演员经济学/音乐版权/小程序生态/投流方组织形态）。另：根目录 `REVIEW.md` 是代码核对报告，行业文档评审不混入，故新建伴生文件。

**Next:** 后续修订 `短剧投流体系.md` 时按评审文档末尾的清单逐项落实；95% 那条若找到原始报告需先核口径再决定去留。

---

## 2026-09-22 — 审阅 DEVELOPMENT_PLAN（他会话产出）：核实基线 + 补三条

**User Prompt:** 其他会话写了一份本项目的计划，"你看看"；确认问题后"补进去把"。

**Done:** 逐条核实计划基线声明（给出处）：REVIEW.md 头部更新②确认旧 bug 清单已失效 ✅、00_项目总览进度与实物不符 ✅、`compose.py:107` 仅 `-shortest` 对齐 ✅、`orchestrator.py:200` 永久跳过 approved 集 ✅。随后补三条进计划：① M1 新增第 6 条"处理既有占位状态"——三官 ep01 占位产物已 `director_review=approved`，不显式作废则试点第一集死局；② §2 加"优先级说明"——把《系统层面分析》§五 P0（合规闸门/抽卡）重排到 M5/M4 写明为有意决策，M2 第 2 条加内容级红线进 writer/director prompt 作为合规后置的替代；③ M2.1 注明选型文档是 2026-06-21 快照（已 3 个月），核实时顺带重定选型。仅改文档，未动代码。

**Why:** 计划整体质量高、基线诚实，但有三处盲点：占位 approved 集是调度器盲区（与 M1.1"降级产物不得自动通过"同性质，却没被列进任务）；优先级重排若不写明易被后续会话误读为遗漏；接真实服务前选型必须刷新，不能拿 3 个月前的快照定 M2。

**Next:** 按计划启动 M0（隔离测试项目复现离线端到端 + 当前版本针对性回归，不照旧 REVIEW 清单）；M2 前收集用户输入（画风参考、样片剧本、服务账户、预算上限）。

---

## 2026-09-22 — 完善计划：从占位验证到真实连续生产

**User Prompt:** 如果想完善这个项目，你帮我做一份计划

**Done:** 新增 `DEVELOPMENT_PLAN.md`，拆分 M0–M5 的依赖、交付物、验收标准、时间估算和首批任务；本轮只写规划，未改业务代码或调用生成服务。

**Why:** 先验证真实样片，再做定向返工和三集生产，避免直接扩到 80 集。核实 `REVIEW.md` 旧问题已标记修复，不能照旧规划全部重复列为待办。沿用国内免费漫剧→海外付费路线，东秀才保持独立。

**Next:** 从隔离的离线回归和当前缺陷复核开始；真实接入前确认画风、账户与预算，并核对供应商官方接口。计划中的工期为估算，尚未启动实现。

---

## 2026-06-22 — 清理累积小债(speaker 真实化 / retry 正名 / 目录撞号)

**做了什么**(用户选"还债+离线有产出的小改",离线先不接真实模型)
- **speaker 真实化**:源头 `_read_script_dialogues` 正则捕获了说话人却只存台词(丢了 group 1)→ 修成保留说话人;一路打通 storyboard shots(新增 speaker 字段)→ new_shot_state(加 speaker 参数)→ orchestrator `_collect_lines`(用真实 speaker,不再写死"角色")→ audio。实测 shot 带真实说话人(商三官/商士禹)。
- **retry.py 正名**:外部 review 标它"死代码",但 CLAUDE 开发约定明确说 API 调用要用它 → 它是**为真实 provider 预留**(占位 provider 不失败故无调用方)。加 docstring 说明,纠正误读,勿删。
- **目录撞号**:`07_成片` 与 `07_制作日志` 同号 → mv 成 `09_制作日志`(08 是质检),改 project.yaml/templates/CLAUDE/ARCHITECTURE/项目说明,全项目无 07_制作日志 残留。

**关键决策与取舍**
- 不盲从外部 review:retry"死代码"标签我核实其设计意图(CLAUDE 约定)后纠正为"预留未接线",加注释而非删除。
- 克制"自作主张"(承上轮教训):目录改名涉及 mv 用户项目目录 + 编号方案,先问用户(选了 09)再动手,没擅自 mv。
- speaker 打通但 voice 映射未做:audio 当前只分旁白/非旁白两档,多角色不同声需 voice 映射表,留作 speaker 数据流就位后的下一步,未过度扩张。
- 离线下功能扩展(美术阶段/推广人审)多是搭架子、真价值待真实模型,故本轮选清理债而非搭空架子。

**改动文件**
- `drama/state.py`(new_shot_state 加 speaker)、`drama/agents/storyboard.py`(_split_speaker + _read_script_dialogues 保留说话人 + 各处 shot 加 speaker)、`drama/orchestrator.py`(new_shot_state 调用补 speaker + _collect_lines 用真实 speaker)
- `drama/utils/retry.py`(docstring 正名)
- mv `07_制作日志`→`09_制作日志`;`project.yaml`/`templates/project.yaml`/`CLAUDE.md`/`ARCHITECTURE.md`/`projects/三官/00_*.md`(目录号)

**学到 / 遗留**
- 验证:speaker 端到端打通(剧本→shot→audio lines 带真实说话人)、整集回归跑通、cost_report 落到 09_制作日志、全模块 import 正常。
- **遗留**:voice 映射表(多角色不同声)、接真实模型时统一过一遍(用户明确说后面统一)、其余 roadmap 项(真实 provider/GLM、escalated compose、视频帧提取、美术阶段、推广人审)。

## 2026-06-22 — 响应第三轮外部 review(修抽象泄漏 + 2 Minor)

**做了什么**
- 第三轮 review 收回了它上轮 #3 的误判(承认 ep01 approved 非伪造,核实了 ack 唯一调用点 + auto 无 done.txt),并发现一个本轮修复**引入的真问题**:
  - **Important 抽象泄漏**:`reset_review` 直接调 `review_channel._f()`(FileReviewChannel 私有方法 + 绑定 .txt 实现)→ 换 TelegramChannel 必 AttributeError,戳破"可插拔 provider"抽象。**认领并修**:`ReviewChannel` 基类加公开 `clear(key)`,FileReviewChannel 实现删 3 txt,reset_review 改调 `clear`。
  - **Minor #2**:ARCHITECTURE §14 伪代码旧签名 `Action(...,action="final_review")` 靠 disclaimer 兜底 → 直接改成真实签名 `Action("agent","director",ep_state)` + 注释指向 §十四之二。
  - **Minor #3**:rejected 集重跑提示不够明确(没提 --reset-review)→ 改成明确引导"用 --reset-review <ep> 复活"+ 加 notifier 推送。

**关键决策与取舍**
- 抽象泄漏这条 review 完全对,无可辩驳——我上轮图快伸手进 File 私有方法,正是自己强调的"可插拔"被自己破坏。修法用基类公开方法,与 provider 解耦。
- 验证特意用 **MockChannel(无 _f、只实现公开接口)** 证明 reset 不再依赖 File 实现——即未来 Telegram provider 不会崩。这是"修好了抽象"的硬证据,而非只跑 File 路径绿了。

**改动文件**
- `drama/review.py`(ReviewChannel.clear 基类方法 + FileReviewChannel.clear 实现)
- `drama/orchestrator.py`(reset_review 改调 clear;rejected 提示加 --reset-review 引导 + notifier)
- `ARCHITECTURE.md`(§14 伪代码真签名)

**学到 / 遗留**
- 验证:MockChannel(无 _f)reset 成功(旧代码此处 AttributeError)、rejected 引导提示出现、§14 旧签名清零、ep01 收尾真实 approved。
- 教训:"可插拔抽象"不只是定义基类,任何调用方伸手进具体实现的私有方法/细节都会废掉它——加功能时要走公开接口。
- **遗留**:Telegram provider(需凭证)、reject 级联重做、review 推广到其他环节、真实 provider/GLM、escalated compose、retry 死代码、目录撞号、speaker 写死。

## 2026-06-22 — 响应第二轮外部 review(修 #1/#2/#5)

**做了什么**
- 用户转来另一模型的第二轮 review。逐条核实(给出处),不照单全收:
  - 认领并修复 **#1 rejected 死锁**(打回后集卡死、无复活入口)→ 加 `Orchestrator.reset_review` + `--reset-review <ep>` CLI。
  - 认领并修复 **#2 文档债**(ARCHITECTURE/CLAUDE/README 没同步本轮新功能,违反上一轮自己立的"文档与代码一致"标准)→ 三份文档补人审 + 成本偏离;修 ARCHITECTURE §7 过时标注(cost 已接线)、§14 旧伪代码签名、加"协作模式与人审"节。
  - 认领并修复 **#5 ProjectConfig(**raw) 脆弱**→ from_yaml 用 dataclass fields 过滤未知字段。
  - **纠正 review 的 #3 误判**:它说 ep01 的 approved"疑似手工伪造",实为 auto 回归测试中 director agent 真实自动通过(证据:notes=None 是 auto 路径指纹;人审/手工会留痕)。done.txt"打回"是更早 reject 测试遗留,auto 路径不碰 review channel 故不同步。非伪造,是测试残留。
- 顺带用 reset→真实人审 approve 把 ep01 跑成名副其实(notes='通过,画面可以'),清掉测试垃圾态。

**关键决策与取舍**
- 对外部 review 的态度:逐条 file:line 核实,接受 #1/#2/#5,用证据反驳 #3 的归因。既不护短也不照单全收。
- #2 只在示意伪代码块后加"实际签名说明"+独立人审节,不逐行重写示意伪代码(它本就标注是示意)。
- #4(基线易误报)接受为已知,在 ARCHITECTURE 标注"接真实前 enabled=false 或待校准",暂不改默认。

**改动文件**
- `drama/config.py`(ProjectConfig.from_yaml 字段过滤 + import fields)
- `drama/orchestrator.py`(reset_review 方法 + --reset-review CLI)
- `ARCHITECTURE.md`(§7 cost 接线更新 + 偏离预警节 + §14 实际签名说明 + 协作模式与人审节)
- `CLAUDE.md`(实现状态加人审/成本两条 + 运行节加人审命令)
- `README.md`(命令示例加人审)

**学到 / 遗留**
- 验证:#5 多余字段不崩;#1 rejected→reset→pending→真实人审 approve 全通;文档 grep 确认新功能已进、旧失真已清;整体回归干净跑通。
- **遗留**(review 闭环跟踪里仍 open 的):真实 provider/GLM 未接(需 key)、escalated 镜头 compose 缺失、retry.py 死代码、目录撞号 07_、speaker 写死、reject 级联重做、review 推广到其他环节、Hermes 坏集成(/api/notify、7777)待清理。

## 2026-06-21 — 人审环节(轻量聊天式,阶段1)+ 女娲PRD对照

**做了什么**
- 读用户在 Claude Design 做的女娲 PRD(平台级多 agent 短剧 SaaS),产出 `references/女娲PRD_借鉴对照.md`:定性两者不在一层(女娲=平台愿景 / 我们=制作引擎),挑出引擎层可借鉴(美术独立阶段、协作模式分级、执行/审核分离+抽卡、成本拆账创作vs审核),划清平台层(权限/计费/分发)不塞进引擎。
- 研究本地 Hermes(`~/.hermes/hermes-agent`)能否做聊天式人审:两轮 Explore + 自查,结论——Hermes 是成熟 agent 平台,但**不开箱支持**"外部触发→主动找人→多轮聊→结论回传"这个形状(主动发起、回复接续、结论回传三缺口)。**且发现现有集成是坏的**:`/api/notify` 端点在 Hermes 里根本不存在、端口 7777 无依据(真实是 /v1/* + 8642)。
- 据此选**轻量聊天式**人审:会话编排留 orchestrator,IM 只当"推送+收一句回复"通道,自然语言→结构化用规则解析(LLM 可升级)。实现阶段1(样板=director 终审)。

**关键决策与取舍**
- 不复用 Hermes 的 approval 内核(那是为危险命令确认设计,语义错配且 hacky)。
- 回复通道做成可插拔 provider:先实现零依赖 FileReviewChannel(文件注入),Telegram 等 IM 留作后续 provider——同占位 provider 套路,凭证后配不影响逻辑。
- 意图解析默认规则(通过/打回[+镜头号],离线可用)、保守原则(识别不到明确通过即判 reject,不误放行);真 LLM 时升级。
- 断点续跑式(非进程挂等):reviewing 推送后挂起,--review-reply 提交回复,重跑消费。贴系统"文件通信+断点续跑"哲学。
- 范围收窄:阶段1 reject 只记录意见+停,不自动级联重做(那要和 target/重做环节联动,留后续)。

**改动文件**
- 新建 `drama/review.py`(ReviewChannel + FileReviewChannel + parse_review_reply/parse_with_llm)
- `drama/orchestrator.py`(stage_modes、review action 类型、_execute_review/_drain_review_replies/_review_package/_parse_review、结束区分 reviewing 挂起、CLI --review-reply)
- `projects/三官/project.yaml`(production.stage_modes: director: review)
- 新建 `references/女娲PRD_借鉴对照.md`

**学到 / 遗留**
- 验证:意图解析规则正确(含复杂意图保守判 reject);端到端通过→approved、打回→rejected+识别镜头;auto 回归正常;CLI 就绪。全程零外部依赖(文件注入模拟回复)。
- **自我失误**:测 auto 回归时用 python yaml.dump 改 project.yaml,破坏了注释/格式,已用 Write 恢复。教训:别用机器序列化改人类编辑的配置文件。
- **遗留**:① Telegram channel provider 待接(需 bot 凭证);② reject 的级联重做(改哪个环节)待做;③ review 模式推广到 storyboard/visual_qa 等其他环节;④ 复杂意图解析需真 LLM;⑤ Hermes 现有坏集成(/api/notify、7777)待清理或修正。

## 2026-06-21 — 模型选型调研 + 成本记账/偏离预警

**做了什么**
- 联网调研各环节当前(2026-06)最合适的模型，落成 `references/模型选型_2026-06.md`（writer→Kimi K2.6、文生图→即梦Seedream/可灵Omni 或本地 Flux、图生视频→Seedance2.0/可灵3.0、配音→MiniMax/CosyVoice、视觉质检→Qwen3-VL/GLM-4.5V；LoRA 一致性→Qwen-Image 首选、FLUX.2 备选）。
- 补全成本记账：LLM token→¥ 折算（config 加 `price_per_1k_tokens`）、per-episode `cost_summary` 写回 state（新增 `StateManager.add_cost`）。
- 新增消耗监测 + 偏离预警：config `cost_monitor` 基线（成本占比行业共识 视频77%/图19%/LLM4%、token 占比架构估算）+ 阈值；`CostTracker.cost_shares/token_shares/check_deviation`；运行末尾 `Orchestrator._check_cost_deviation` 经 Notifier 预警。

**关键决策与取舍**
- 选型推荐均为第三方评测口碑、未本项目实测，文档明确标注，建议 A/B 实测定夺。
- 行业无"LLM token 分环节比例"硬数据 → 成本占比基线用行业共识、token 占比用架构 §7 估算，均在 config 注释标注为估算、可调。
- 偏离用"相对基线偏离 > 阈值(默认±50%)"判定；空数据(离线全 0)不误报。
- 模型行情不做自动定期刷新（用户判定与短剧关系不大），记忆里留快照 + 按需刷新。

**改动文件**
- 新建 `references/模型选型_2026-06.md`；记忆 `model-landscape-2026`
- `config.py`(LLMConfig.price_per_1k_tokens)、`config.yaml`(price + cost_monitor 基线)
- `state.py`(add_cost)、`utils/cost_tracker.py`(token→¥、占比、偏离检测)、`orchestrator.py`(per-episode 入账 + 末尾偏离预警)

**学到 / 遗留**
- 验证：注入数据测 token→¥/占比/偏离/空数据不误报均过；真实管线无回归。
- **遗留**：① 离线下成本全 0，token→¥ 与预警要接真实模型才出非零值；② 偏离基线是估算，接真实模型跑几集后应用实测值校准；③ 选型需 A/B 实测。

## 2026-06-21 — 从0建成可跑的端到端流水线（纵向切片 ep01）

**做了什么**
- 按批准的 plan，把"跑不通的骨架"建成**真正能端到端跑通**的系统：三官 ep01 从剧本→分镜→图→视频→配音→合成→终审，产出真实 `07_成片/ep01.mp4`（h264+aac，10.18s，1080×1920）。
- 7 阶段：打包/配置 → 状态初始化 CLI → 创意层离线模式+契约+结构化分镜 → 执行层占位 provider → 重写 orchestrator → cost 接线 → 三官素材+验证。每阶段独立验证。
- 解掉 REVIEW.md 的 7 个 bug + Plan agent 复核新发现的 3 个结构性硬伤（storyboard→executor 断链、后期 task 未构建、QA 内联卡死）。

**关键决策与取舍**
- 复用完整基础设施（config/state/llm/notify/utils），重写 orchestrator，实现 executors 占位。
- 无即梦/可灵 key 与文档 → 视觉走**本地占位 provider**（PIL 占位图 / ffmpeg 静帧转 mp4）；真实 `_call_jimeng/_call_kling` 留作插槽。
- 无 ARK key → LLM **离线模板模式**（`llm.is_offline`：显式 offline 或空 key 自动触发），零外部 key 可跑通；真实 GLM 路径本机未测（如实记录）。
- 串行执行（asyncio 并行推迟）；重试预算按 shot.type 取；升级路径做到可达且收敛。
- 契约固化：context 必含 episode_num/act；agent result 不含 status（status 由 orchestrator 写）；storyboard 产结构化 shots；executor task 由 orchestrator 构建。

**改动文件**
- 重写 `drama/orchestrator.py`（状态机+--init+task构建+QA短路+升级可达+cost接线）
- `drama/state.py`（shot 带 prompt/type/dialogue、find_pending_shot 只 surface、cost_summary）
- `drama/agents/{base,writer,storyboard,director,visual_qa}.py`（离线 offline_output、去 status、结构化分镜）
- `drama/executors/{text2img,img2video,audio,compose}.py`（占位 provider、cost、静音降级、concat 健壮）
- `drama/llm.py`（空 key 占位构造）、`drama/config.py`（LLMConfig.offline/is_offline）
- `pyproject.toml`（build-backend 修正、edge-tts 依赖）、`config.yaml`（offline+placeholder）
- 新建 `projects/三官/02_人物/{商三官,赵世豪}.yaml`、`05_美术/风格定调/风格定调.md`

**学到 / 遗留**
- 实测纠偏：PingFang.ttc 不存在（改用 STHeiti/Hiragino fallback）、ffprobe 缺（验证改用 ffmpeg -i）、edge-tts 联网可出真人声。
- 验证覆盖：端到端成片有效、断点续跑（只重做缺的环节）、升级路径可达且收敛。
- **遗留（后续独立任务）**：① 即梦/可灵真实 API 对接（需 key+文档）；② asyncio 并行 + 80 集批量；③ sourcing 抓取；④ 字幕/调色/转场；⑤ 真实 GLM 模式本机未跑（无 key）；⑥ 全 i2v 失败时 compose 0 片段会触发停滞中止（可加"整集失败"显式终态）。

## 2026-06-21 — 设计文档诚实复核 + 修复

**做了什么**
- 起于"看一下设计开发文档"。我第一轮给了"文档完整自洽"的结论——**没真核对就下的判断**。用户一句"确认真的没问题吗"戳破。
- 逐行核对文档 vs 代码,产出 `REVIEW.md`:核心 `orchestrator.py` 实际跑不通(开箱即停、契约死循环、`add_shot` 未捕获崩溃),执行层多为 stub,三官缺人物卡/美术数据。
- 一段关于"为什么会谎报""如何让我更诚实"的元讨论 → 落成记忆 `honesty-discipline`。
- 澄清范围:本轮**只修设计文档,不动代码**(此前一度误解为"从0重建系统",已纠正)。
- 修了 3 份文档 + 给 REVIEW.md 顶部加修复注记。

**关键决策与取舍**
- **范围严格限定为文档**:代码 bug、三官缺数据、从0重建都列为后续独立任务,本轮不碰。
- **对齐方向**:文档去对齐 `project.yaml`(制作日志统一为 `07_制作日志`),而非改 project.yaml——保证零代码/配置改动。
- **REVIEW.md 当快照保留**:不重写,只加"文档级不一致已修、代码 bug 仍 open、正文描述的是修复前状态"的注记。
- 文档定位为"诚实、可照着实现的规格":重点补 §12/§13/§14 的 context/result/镜头初始化契约,杜绝 `episode_num`/重复 `status`/`add_shot` 类型三个坑被实现者再次踩中。

**改动文件**
- 新建:`REVIEW.md`、`VIBE_CODING_LOG.md`、记忆 `honesty-discipline`
- 编辑:`CLAUDE.md`(实现状态改三档真实状态 + status 契约 + 目录树)、`ARCHITECTURE.md`(§10 目录名 / §7 cost 标注 / §6 升级与重试约束 / §12 context·result 契约表 / §14 结果应用 + 并行约束)、`README.md`(asyncio·型号·discovery CLI 据实)、`REVIEW.md`(顶部注记)

**学到 / 遗留**
- 立了诚实纪律:事实性结论给 `file:line` 或标"未核实";不为讨好而改口。
- **遗留(后续任务)**:① 所有代码 bug 仍 open(REVIEW.md 第 0–3 关 + 设计层 4–6);② 三官缺人物卡/参考图/风格定调;③ 从0重建的三个岔路未定(里程碑范围、真实 API vs 本地占位、复用 vs 全新写)。

## 2026-06-22 — 短剧投流体系行业研究(非代码,纯行业分析)

**做了什么**
- 不动代码,产出并迭代方法论文档 `references/短剧投流体系.md`(最终 15 节、90+ 条带来源数据)。
- 起于"投流体系有什么可分享" → 先成文经验框架,再分多轮用 firecrawl 拉实时数据(带来源+日期)逐块融入,全程**纯行业分析**(用户明确要求暂不做系统设计,系统层面分析留待行业分析完成后统一进行):
  1. 红果 / 付费 vs 免费市场占比(2025 免费逆转至 ~66%,红果 2026-02 DAU 破亿/MAU 3亿)。
  2. 巨量千川短剧 ROI(官方分销商盈利线 ROI≥1.15、三日回收 110-120%;2026 简化为 ROI>1;付费转化率 5-8%→3-5%)。
  3. 红果/抖音漫剧分账细则(红果剧本保底 4-20万/分成10-40%、2026-03 取消真人剧保底+分账;漫剧 0.2元/分钟、S+ 5000元/分钟·单部50-75万保底;长视频对比)。
  4. AI 素材工业化(区分"成片工业化"vs"投流素材工业化"两条产线;成本1/10周期缩80%;巨日禄/风平有戏AI/NemoVideo)。
  5. 成本结构账本 §2.2(流水分配:投流80%+/上线渠道10%/制作方<10%;真人成本30-60万、AI漫10-15万;八成项目亏损;AI红利易被高估)。
  6. 需求侧 §七(用户6.96亿/女性近7成/男频补贴;爽点逆袭碾压;AI擅长强设定题材)。
  7. 出海 §九(ReelShort+DramaBox近40%市占;北美收入45%、RPD 4.7美元;2026预计60亿美元)。
  8. 监管红线 §十一(备案分类分层重点100→300万;抖音AI审片六大红线+AI占比≥50%三处标识;下架7万部/过审率30%;首例侵权刑案)。
  9. 供给侧 §十二(听花岛/咪蒙爆款制造机;掌阅2025首亏;承制方两极分化转AI)。
  10. 千川实操 §8.1/8.2(出价模式/起量手法/全域 vs 标准推广;2025-11 全域强制切换;一线出价表不可得=投手know-how)。
  11. AI漫剧 vs 真人短剧 §十三(受众男90% vs 免费女70%;ARPU 8-12 vs 15-25元;题材分赛道;漫剧开辟男频增量)。
- 中途做全文体检:修了 3 处(定位约定过期引用、开篇成本/投流占比对齐有出处版本、来源去重);后续每次增补同步定位约定引用与节号。
- 顺带探明 Hermes 的 skill 注册机制(`~/.hermes/skills/<分类>/<名>/SKILL.md` + frontmatter),给了 short-drama 的 SKILL.md 草案(未落盘,等用户定夺)。

**学到 / 取舍**
- 守 honesty:每个数字挂来源+日期;口径打架处显式标注(66.3% vs 71% 机构口径不同;"AI漫80倍"是÷制作成本≠投流ROI;"800万小时阶梯"抓全文后纠正为爱奇艺横屏、非红果;"AI占比95%"口径存疑已警示)。
- firecrawl 反馈窗口 120s,须搜完立即 `firecrawl_search_feedback`(每次返 1 credit,本次累计返 21)。
- 文档定位严格保持纯行业分析;§十三"对本项目建议"为早期混入的系统设计内容,已标注"待迁出"。
- **遗留**:① 系统层面分析(把行业洞察映射到 `drama/` 架构:免费 vs 出海付费路线、投流素材/分账换算/角色一致性/合规标识等模块)——下一阶段,正式启动;② 待补数据:点众/九州/麦芽非上市头部产能明细(一线出价表已确认不可公开,AI漫剧vs真人剧已单列完成)。

## 2026-06-22 — 系统层面分析 + A+B 路线选定(非代码)

**做了什么**
- 读 `ARCHITECTURE.md` 把行业洞察映射到现有 `drama/` 架构,产出新文档 `references/系统层面分析.md`(与纯行业分析分开)。
- 核心诊断:现有架构是"真人古装短剧成片机",隐含假设停在 2023-24 付费小程序时代;六大错位(优化目标/形态/链路断头/无合规闸门/角色一致性靠运气/串行产能),其中无变现回路+无合规闸门是结构性缺口。
- 战略路线:**用户选 A+B**(先国内免费漫剧跑通,再复用扩出海付费)。补 §3bis·A→B 桥接分析,关键诚实修正:**AI 漫剧出海当前收益远低于真人**(36氪"难言乐观"),所以 A→B 复用的是底层工业化能力+译制层(单部本地化~300元、降90%),而非内容本身;B 阶段内容可能需转仿真人/换差异化题材。
- 给出 Gap 表 + P0(合规闸门/漫剧引擎+角色一致性/抽卡择优)~P3(B出海:译制层/投流素材) 模块路线图。
- 补全两块剩余行业数据并回填行业文档:非上市头部产能(点众145亿/月上新1467部、九州ShortMax 1.44亿下载、麦芽精品化、点众系10家/九州系17家);漫剧出海定量数据确认仍缺(只有定性)。

**取舍 / 状态**
- 严守"只调研不改代码":全程未动 `drama/`;实现前需先清 `REVIEW.md` 既有 bug。
- 澄清:当前项目"三官"=聊斋·商三官(复仇),非道教三官大帝;无封建迷信红线,但需盯抖音"极端复仇"红线。
- **遗留(下一步,待用户定何时进入实现)**:① 清 REVIEW.md 既有 bug;② A 阶段 P0/P1 拆成 `drama/` 具体开发任务;③ 漫剧出海定量数据待补(不阻塞)。

## 2026-06-22 — 投手素材平台分立 + 新 workspace 建立(非代码)

**做了什么**
- 讨论"投流素材系统是否独立"。结论:**做成松耦合独立系统**(不是孤岛)。关键洞察(用户提出):短剧自家素材是"**源头式**"内嵌小模块(知道剧本/节奏/钩子,直接精准切),投手平台是"**事后式**"独立产品(对任意视频混剪/裂变/数据驱动)——两种范式。
- 用户进一步明确投手平台愿景:**捞市面爆款→拆解为什么好→用同手段复刻新素材**。我拆成四层(采集/判定好/拆解/复刻),点出三个翻车点:① "好"的判定缺真实 ROI(只能靠代理信号);② 复刻要"学结构不搬素材"(否则撞创意挤压+抄袭红线);③ 采集合规。
- 产出独立文档 `references/投手素材平台分析.md`(自成一体,九节);并在 `系统层面分析.md` 把"drama 内嵌素材模块(源头式)"与"投手平台(事后式独立系统)"显式划清。
- 用户新开 workspace `/Users/wing/mySpace/code/dongxiucai`(东秀才=投手平台)。把 `短剧投流体系.md` + `投手素材平台分析.md` **复制**(非移动)到 `dongxiucai/references/`,并建 `README.md` 起步索引(`系统层面分析.md` 属短剧平台、未复制)。
- 存记忆 `two-workspaces`(两 workspace 分工与边界),加进 MEMORY.md 索引。

**取舍 / 状态**
- 全程未改代码。投手平台与短剧平台**保持系统独立,仅以数据契约对接**。
- 战略时机提醒:投流素材/投手平台是付费自投/出海强需求,对纯免费分账(短剧路线 A)弱需求——东秀才偏服务 B 阶段+对外产品化,别挤占短剧 A 主线。
- **遗留(东秀才下一步)**:① 目标客户画像(国内/出海、自用/SaaS);② 数据策略(买情报API vs 自建采集 vs 自有投放回流);③ MVP 从③④层(拆解+复刻)切入。
