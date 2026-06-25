# Vibe Coding Log

> 本系统(drama/)的 AI 辅助开发会话日志,**倒序**(最新在上)。
> 与 `projects/三官/07_制作日志`(拍剧的制作日志)无关。
> 每条记:**做了什么 · 关键决策与取舍 · 改动文件 · 学到 / 遗留**。

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
