# A02：候选、指标与研究排序审查

审查日期：2026-09-19。范围：`D:/wangkai/workspace/OKXQuant`。

## 结论与权限边界

- 已通读下表列出的 12 个授权脚本，并只读核对其上游闭盘适配、成本模型、候选物化和现有测试。
- 修改仅限 A02 独占文件、新增 `tests/test_audit_a02_*.py` 和本报告。未撤销其他 agent 修改、未访问实际 `data`/`.env` 内容、未下单、未部署、未提交 Git、未再委派。
- **没有新增策略过滤，没有提高 RR/成交量/方向/追价门槛，没有停用策略，没有强制下单，没有根据少量盈亏调参。** 错误格式、非有限值、错误单位及研究记账修复不被当作新的交易优势条件。
- 同时保留执行候选 ID 的快照绑定与研究信号 ID 的生命周期区别；没有为了让 ID 不变而允许旧候选复用于新报价。

## 已审文件清单

| 文件（均在 scripts/） | 已核对内容 | 本轮修改 |
|---|---|---|
| `entry_candidates.py` | sealed 15M/1H、三个 setup、方向、目标、成本、horizon、ID、物化与最终报价复核 | 时间戳/来源时钟验证 |
| `scalp_candidates.py` | sealed 1M/5M/15M、突破/回踩/转折、ATR、预测目标、成本、ID | 生成时间数字类型规范化 |
| `scalp_ranking.py` | 保留全集的启发式排序、RR/价格量纲、回退排序 | 方向几何、非有限 ATR、价格缩放 |
| `scenario_entry.py` | shadow 4H/1H/5m、冻结定义、后续触发、缺口/过期、成本 | 非字典 K 线转显式数据错误，注明 close timestamp |
| `entry_opportunities.py` | shadow 15M、小时 pivot 确认时点、回测/观察时间、终态与 ID | pivot 确認时间、终态原因不丢失、只读连接关闭 |
| `signal_data.py` | 原始 OKX 开盘时间、confirm、收盘/未来排除、连续性、新鲜度 | 无损时间戳验证、零周期/布尔 OHLCV 防错 |
| `calculus_engine.py` | 顺序/列位置、导数/积分、方向汇总、单位、概率评分语义 | 序列不再压缩错位、汇总方向修正、评分语义透传 |
| `factor_library.py` | 15M/1H 指标、RSI、ATR、打分、缓存与采集调用 | RSI 边界、绝对价格 ATR 精度、概率语义 |
| `scalp_research.py` | 观察起点、15/30/60m、first touch/MFE/MAE、因果性、落库 | 亚毫秒边界与乱序处理、版本标识 |
| `entry_research.py` | 5m→15m/1H/4H、特征可见时间、tick rounding、训练/测试、资金费 | 无修改；补充前缀因果测试 |
| `leverage_research.py` | 1/3/5 倍研究、固定资金表述、算术对照、非执行性质 | 无修改；资金语义交 A01 确认 |
| `trade_quality.py` | 样本持仓区间、退出观测、证据缺口、成本对账边界 | 选择真正最新样本、只读连接关闭 |

## 既有候选链路和语义（仅说明，并非新增条件）

### 分钟引擎

`strategy_engine == demo_scalp_v2` 才走分钟候选。1M/5M/15M 均通过 `verified_bars`：正向 OHLC、连续闭盘、同一来源时点，最多保留 24 根。使用 14 个 true range 的价格单位 ATR。

- 1M：前六根区间突破，或上一根反向 K 线后的回踩确认；当前根自身方向必须一致。
- 5M：5/12 均线结构，**或**新近确认的转折，不要求等待慢均线全部交叉。
- 15M：八根均值偏向与 5M ATR 比较。
- 既有量能、触发保持、追价 `.6 ATR1M` 均保持；止损为两根极值加缓冲并保留 `1.1 ATR1M` 距离；目标距离 `max(4 ATR1M, 3 ATR5M)`，明确是 **projection**，不是已观察阻力/支撑。
- 多头按 ask、空头按 bid 构造。RR 的分子/分母均为每基础单位价格差；参考成本包括 maker 开仓、taker 平仓、slippage 和 spread，不乘杠杆，不代表保证 maker 成交。

### 15M / 波段候选

`pullback_reclaim`、`closed_range_breakout`、`range_reversion` 三类及既有宏观/过度延伸规则不变。已收盘 1H 的 7/20 均值用于趋势；目标是此前十二根小时 K 线区间极值（排除最新小时），不能抬高目标来过 RR。不同 setup 的结构/ATR 止损定义未改。

`horizon == scalp` 只是持有管理分类，不能把 legacy 15M 路径冒充 1M 执行循环。`catalog`、`expand_selection`、`validate_live_quote` 仍仅产生/验证建议，`order_authorized` 保持 false，最终资金和执行由 A01/A03 负责。

### 排序与研究

- `scalp_ranking` 的六项权重保持 20/35/15/10/10/10。无 minimum score，不删候选。坏几何或坏数值导致**整个候选池恢复原参考排序**，而不是偷偷混入不可比较的零分。
- 排序的 taker/taker 成本比准入的 maker/taker 参考更保守；两者不是盈亏概率。
- `scenario_entry` 的 `ts_ms` 是 **收盘边界**，原始交易所 candle `[0]` 是 **开盘边界**。冻结定义不能在后续触发时偷改；创建 K 线不允许同时触发自己。
- `entry_opportunities` 的 ID 代表同一研究机会，不是可执行授权；历史触发的 retest 使用当前已收盘观察，不能把当前 stop/quote 当作原触发时已经知道的值。
- `entry_research` 在每一根 5m 观察点通过 `bisect_right(..., at)` 只取已经闭合的高周期数据；先处理旧 setup 再建立新 setup。测试改变未来价格后，所有变更时点前的信号完全相同。
- OHLC shadow 观察不是实际成交或盈利回测；同根同时止损/止盈标为 ambiguous，缺口不猜测首次触碰。

## 已修复问题与严重性

严重性：P1 为可能改变有效方向/数理解释的高风险正确性问题；P2 为数据契约、研究归因或边界错误；P3 为健壮性/资源问题。不声称已在真实账户观察到对应损失。

| ID | 严重性 | 原问题 | 修复与边界 |
|---|---|---|---|
| A02-01 | P1 | 多周期正方向票数占优、正加速度在 0～0.05 区间时，三元表达式却返回 `BEAR_DECELERATING`；负方向弱负加速度则错误落入 MIXED | 分别按多空方向选 STABLE/DECELERATING；保留原加速阈值和投票规则 |
| A02-02 | P1 | `_finite` 删除价格序列内缺失/NaN/非正值，可能把多根时间跨度压成一根；成交量仍用原尾部导致错位；短行也被静默跳过 | 输入有错误时返回 `valid=False` 与原因；价格/高低/量长度保持对齐；raw timestamp-prefixed 9列不能冒充 stripped OHLC[V]。有效序列公式不变 |
| A02-03 | P1 | 全平 RSI 被算成约 99，单向上涨也未到 100；绝对 ATR 固定四位小数把低价币真实波动抹成 0 | 平盘/全涨/全跌分别 50/100/0；ATR 保留浮点价格单位精度，不改 RSI 周期、评分权重或准入阈值 |
| A02-04 | P2 | `int(timestamp)` 静默截断小数；`abs(NaN-as_of)>1` 为 false，可绕过来源时钟一致性判断；0m 为零宽度；布尔 OHLCV 被视作数字 | 共用有限、非负、整毫秒验证；拒绝无效格式；仍保留 public signal 的原一根发布延迟容忍，未新加连续性/新鲜度阈值 |
| A02-05 | P2 | scalp ID 将语义相同的 int/float `data_as_of` 序列化为不同 JSON，导致同一时点不稳定 | `created_at` 统一 `number`，与 swing 一致；行情变化/标的变化仍必须让 ID 失效，不取消快照绑定 |
| A02-06 | P2 | rank 用绝对值让反向 stop/target 得到正风险/收益；NaN ATR 可穿过 `min`；固定 `1e-12` 价格分母破坏低价缩放不变性 | 有向几何及有限 ATR；candle body 仅在真实区间为零时取 0；坏参考只回退排序，不删单；`scalp-ranking-v2` |
| A02-07 | P2 | shadow born 先截成毫秒，再向分钟取整；整分钟后不足 1ms 的观察错误包含已有部分分钟。倒序 bars 可能把后来的触碰记成第一次 | 从原始秒时点取分钟 ceiling；先按 close_ms 排序，验证分钟网格，仍排除 now 之后；`scalp-research-v2` |
| A02-08 | P2 | 研究机会终态在下一轮缺席时被改成 superseded/expired，原始 invalidated 原因丢失；pivot 只存峰值 K 线时间，未显式存其右侧确认时间 | 终态和原始原因永久保留；新增 `confirmed_close_ms`，不改变目标选择、门槛或机会 ID |
| A02-09 | P2 | 单周期概率字段已声明未校准，但汇总层/因子层丢失该语义；短样本 neutral 也没带声明 | 透传 `probability_calibrated=False` 和 heuristic-not-win-rate 语义；未新增概率门槛或调整 CDF 参数 |
| A02-10 | P2/P3 | 持仓样本入参若升序，退出证据使用第一个而非真正最新样本；`with sqlite3.connect` 只结束事务不保证关闭连接；shadow 非字典 row 抛 AttributeError | 区间内样本排序取最新，仍不借用平仓后观察；只读连接用 `closing`；畸形 row 归入数据不可用，不影响有效 shadow 流程 |

## 测试与可复现命令

```powershell
wsl /tmp/r20-review-venv/bin/python scripts/run_tests.py --pattern 'test_audit_a02_*.py'
```

- `tests/test_audit_a02_signals.py`：31 项新增回归（含多组 subTest），覆盖时间与数据对齐、long/short 及 1M/15M 候选继续产生、参考成本、稳定/不可转移 ID、价格缩放、方向、RSI/ATR、未来变更不污染历史、first-touch、最新平仓证据。
- `tests/test_audit_a02_compatibility.py`：只读复用 91 项既有候选/指标/shadow/研究契约测试；不改变原测试文件。合计 122 项。
- 执行器在一次性源码快照中运行，排除真实 data/日志/秘密；网络、DNS、子进程默认被测试 runner 禁止，没有绕过这些隔离。
- 最终按上述指定命令复跑：**122/122 全部通过**（31 项新增、91 项既有兼容）；新增测试单独运行亦 31/31 通过。并行期间曾因跨域 `profile_signature` 暂缺出现 1 项错误，随后由其他 agent 补齐定义，已复跑解除；A02 未改动该跨域文件。
- A02 文件 `git diff --check` 无空白错误。

## 跨域依赖（仅报告，不修改）

1. **并行集成事件，已解除**：`scripts/trading_prompt.py` 的 `compose` 在一次测试快照中调用尚未定义的 `profile_signature(profile)`，导致既有 prompt 兼容测试出现 `NameError`。随后其他 agent 补齐定义，最终 122 项全绿。A02 保留该兼容测试，未修改 prompt 文件；最终总集成仍应复跑，避免并发工作树截取半完成改动。
2. **A01 / A03：成本口径有意保留**。分钟准入包含 spread，legacy swing 参考准入未显式包含 spread；scenario shadow 使用双 slippage，而 common execution cost 是单 slippage budget。不得在本次审查里把统一成本口径等同于提高门槛。请核对最终网关/实际成交成本与展示字段，不把 maker 参考当成交保证。
3. **A01：杠杆研究的固定资金声明**。`leverage_research.py` 顶层声明 300 资金、1.5 单笔风险；实际 `evaluate(...pool_profile=True)` 调用当前 `capital_pool.Config()`、`Policy()`。两者今后变动时报告常量不应被误当成实际风险预算；本次没有跨域更改核心资金算法。
4. **数据适配所有者**：纯数学 OHLC[V] 已丢失时间戳与 confirm，不能单靠 `calculus_engine` 证明已收盘；须继续由 `public_market.signal_json` / 上游 adapter 保证时钟、确认字段和字段顺序。另有全局 `_SIGNAL_AS_OF` 及闭盘来源时钟共享机制，需要上游验证并发 frame 隔离；本次未改 `public_market.py`。
5. **主 agent / prompt**：请保留 continuation/breakdown 的未校准启发式含义，不能将 70% 等输出变成真实胜率或期望收益。`prompt_library` 未由 A02 修改。
6. **数据/账本所有者**：退出样本、费用对账属于观测证据，不可把 sampled MFE/MAE 当 tick 极值；`trade_quality` 没有改写官方 PnL、成交费或执行记录。

## 仍需实盘/前向验证的边界

- 不宣称修复提高收益或胜率；没有读取实际小样本盈亏来调参。
- 需观察交易所边界发布延迟、上游取数时钟/多线程一致性、报价在推理期间变化，以及实际 taker/maker、点差、滑点、资金费与保护订单行为。执行验证交 A03，资金与杠杆验证交 A01。
- 投影目标只说明固定波动率距离，不是未来必达价格；研究排序仍是启发式，不是实盘盈利验证。
- v2 研究代码不会自动修复已落库 v1 的历史 first-touch/部分分钟污染。研究库保留 case/rank 版本，汇总可能包含历史版本；解释前后表现必须按版本分开，不能直接比较混合累计数。本次未迁移或删除数据库。
- 20/10 天研究划分和 1/3/5 倍 arithmetic control 保持不变；短期研究、无清算模拟、非生产 LLM replay 的限制仍在，不能据此强制交易或提高杠杆。
