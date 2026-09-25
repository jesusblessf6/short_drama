# Jev 决策层引入评估（2026-09-26）

> 结论：**可行，建议引入为第四层"判断层"**——不是替换现有三层，而是在 Orchestrator 的决策点后面插一个可插拔的判断服务。预期收益在**判断延迟/成本**与**自动化率**；主瓶颈（生成排队分钟级）不因此改变。
> 依据：TypeSafe AI 官方博客、awesome-jev 收录生态、jevos（开源替代）wire 协议实测文档。

## 一、Jev 是什么（一句话）

不是聊天模型：输入**非结构化状态（文本或 JSON）+ 类型化问题**，返回**带校准置信度的类型化决策**（布尔/选项/打分）。端到端 ~70-500ms（前沿 LLM 的 1/40-1/200），输入 $0.042/MTok、输出免费，无自由文本输出（无幻觉）。开源替代 `jevos-1b` 可本机 CPU 跑 yes/no（50-220ms，免费）。

**关键限制**：暂不支持图像输入（官方 Doom 演示"基于文本结构化状态，not on images (yet…)"）；Jev 云端处于 early access（waitlist）。

## 二、我们系统的判断点盘点（逐个适配度）

| 判断点 | 现状 | 适配 | 理由 |
|---|---|---|---|
| 人审回复意图解析 `_parse_review` | 正则规则，只认固定句式，僵硬 | ✅✅ **首选** | 自由文本 → `{approve/reject+targets/unclear}` + 置信度；低置信 → 挂起保守处理，不误放行（M3 审核闭环的地基） |
| 升级处置 `_apply_escalation` | director LLM 橡皮章或人工 | ✅✅ **首选** | 镜头失败史+错误类别（state JSON）→ Choice{simplify/downgrade/skip/manual}；低置信 → manual 安全默认。省 1 次 LLM 调用/升级，且决策更稳定 |
| 内容红线预检（M2-2 红线目前只是 prompt 软约束） | 无硬闸门，烧完生成费才发现过不了审 | ✅✅ **价值最大** | 剧本/分镜文本 → Boolean"触碰红线？" + 置信度；storyboard 之前拦截。省的不是判断钱，是**整集生成费用**（对应开发计划"烧钱前过审"） |
| QA 聚合决策（帧级结果 → 整镜头过/不过） | 硬规则"全过才过" | ✅ 可做 | 文本状态聚合判断；但规则已够用，收益小，P2 顺带 |
| 画面质检帧级 pass/fail（visual_qa） | 真实模式 vision LLM 逐帧 | ❌ **暂不** | Jev 无图像输入。等官方支持后重评（M4 候选择优同理观察） |
| 错误分类 `classify_exception` / 重试预算 | 规则 | ❌ 保持 | 确定性、免费、已测试锁定；Jev 收益为负 |
| director auto 终审 | 正式模式=人审 | ⚠️ 观望 | 可做"高置信自动过/低置信转人审"预筛，但正式终审本就人工，等 M3 后按实际人审负担再定 |

## 三、架构修改方案：新增第四层"判断层"

### 原则

- **判断是调度职责**：判断层只被 Orchestrator 调用，不进 Agents（创意生成）也不进 Executors（执行）。
- **可插拔 + 失败链**：`规则 → Jev → 低置信升级人工/LLM`，任何一环异常回落规则，绝不静默放行、绝不阻塞管线。
- **零 key 承诺不破**：demo/离线默认规则后端，行为与现状逐字节一致。
- **判断可审计**：每次决策（backend/decision/confidence/耗时）写入 state（复用 attempts_log 风格）。

### 新模块 `drama/judgment.py`

```python
class DecisionClient(Protocol):
    def parse_review_intent(reply, context) -> Decision      # approve/reject/unclear + targets
    def escalation_resolution(shot_state) -> Decision        # simplify/downgrade/skip/manual
    def redline_risk(script_text) -> Decision                # boolean + confidence
class RuleDecisions:      # 现行行为（正则解析、固定处置），离线默认
class JevDecisions:       # POST {endpoint}/v1/systemone，state=结构化 dict，questions 按上述三方法映射
                          # choice/score 用 Jev 云；jevos 本地仅 boolean（红线预检可用它兜底）
class Decision:           # value + confidence + backend + raw（写入 state）
```

置信度阈值 `min_confidence`（默认 0.9）：低于 → 升级路径（人审挂起 / manual 终态），这就是 Jev 生态的 accept/reject/escalate 标准模式，也是 M3/M4 降低人工介入的核心旋钮。

### Orchestrator 接线点（3 处，均为"决策点后插一层"）

1. `_parse_review`：规则 → Jev → 低置信保持 reviewing 挂起（现状：规则硬解析）。
2. `_apply_escalation`：升级处置由 Jev Choice 决定（现状：director LLM 橡皮章）；低置信 → manual。
3. `_plan_episode` 剧本 approved 之后、storyboard 之前：红线预检闸门——demo 只记日志，production 拦截（`director_review=rejected`，notes 注明 redline）。

### 配置与校验

```yaml
judgment:
  provider: rule          # rule | jev
  endpoint: "https://api.typesafe.ai"   # 或 jevos 本地 http://127.0.0.1:8017
  api_key: "${JEV_API_KEY}"
  min_confidence: 0.9
  redline_gate: log_only  # off | log_only | block
```

production 校验扩展：`provider: jev` 时缺 key 拒跑；`redline_gate: block` 时 provider 不得为 rule（规则做不了语义判断，诚实失败）。

### 测试

- 现有 107 例不动（RuleDecisions 保持行为）。
- 新增：wire 格式适配（mock 传输）、置信度阈值升级路径、Jev 异常回落规则、红线闸门三态（off/log_only/block）。

## 四、效率收益的诚实估算

- **判断延迟**：升级处置/意图解析从秒级 LLM 调用 → ~0.3s；但主瓶颈是生成排队（分钟级），端到端体感变化有限。
- **判断成本**：每次 LLM 判断（~¥0.01-0.1）→ ~¥0.0003；量级便宜但绝对值小。
- **真正的大头**：①红线预检拦下不该烧钱的整集（省生成费，非判断费）；②**自动化率**——置信度阈值让"高置信自动决定、低置信转人工"可调，是 M3 审核闭环、M4 三集连续生产减少人工介入的机制基础。
- demo/离线模式零变化、零成本。

## 五、风险与对冲

| 风险 | 对冲 |
|---|---|
| Jev 云 early access（waitlist）、拿 key 周期未知 | 判断层可插拔；红线预检可先用 jevos 本地 yes/no 兜底 |
| 第三方云供应商风险 | wire 协议已有开源实现（jevos），abstraction 保留 rule/LLM-wrapper 槽位 |
| 中文短剧审美/红线判断的准确率未经验证 | **P2 上线前先用 M2 真实样片做基线对比**（误判率/置信度校准），不达阈值不上 |
| 无图像输入 | visual_qa 维持 vision LLM；M4 择优观察官方路线图 |

## 六、分期

- **P1（可立即做，零 key）**：`judgment.py` 抽象 + 三处接线 + RuleDecisions（行为不变）+ 测试。抽象先落地，Jev 到位即插。
- **P2（等拍板：TypeSafe early access key 或 jevos 本地端点）**：JevDecisions 适配；意图解析/升级处置切 Jev；红线预检从 log_only → block。**上线前用 M2 样片校准。**
- **P3（观察）**：M4 多候选择优、图像输入支持后重评 visual_qa。

## 依据

- [TypeSafe AI: Introducing System One Models & Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev)（70-500ms、$0.042/MTok、无图像输入、early access）
- [awesome-jev](https://github.com/yibie/awesome-jev)（分类/路由、验证/护栏、评分、agent 决策等 400+ 实践生态；accept/reject/escalate 模式）
- [feder-cr/jev (jevos-1b)](https://github.com/feder-cr/jev)（`POST /v1/systemone` wire 协议：state 支持文本/JSON、多问题共享一次读取、`{"answers": {name: {noul: 0.9}}}`；本机 CPU 50-220ms）
