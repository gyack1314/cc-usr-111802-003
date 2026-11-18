# 近海渔场数据约定

observation、reference、rule 三种记录分别表示观测、参照和区域规则。metric、region、unit 共同表示现有单期可比口径，来源版次与取得时间独立保留。

## 温度剖面热风险扩展记录

热风险分层只承认**本站**资料；邻近海区的观测只登记不采用，任何深度层缺测都保留“缺测”状态，不得以邻区异常补成本站实测。

- `observation` / `reference`（剖面口径）：payload 增加 `depth_min`、`depth_max`（米，闭区间）。观测给 `value`；参照给历史参照区间 `range_min`、`range_max`。无深度字段的记录仍走原海面单指标链路，两条链路口径分离。
- `habitat`：渔种活动深度带。payload 含 `metric`、`region`、`species`、`depth_min`、`depth_max`。
- `thermal_rule`：区域热风险捕捞规则。payload 含 `metric`、`region`、`unit`、`minimum_coverage`（栖息深度带内参照层最小覆盖率）、`medium_delta`、`high_delta`（相对参照区间中点的偏离阈值）、`action_low`、`action_medium`、`action_high`（各层级对应动作）。
- `review`：补采或质量复核的复议登记。payload 含 `region`、`reason`、`changed_inputs`（受影响的观测记录 id）、`before_known_at`、`after_known_at`。复议结果保留前后两份报告指纹、逐层旧口径与新口径。

## 分层口径

1. 以 `habitat` 深度带为界，选出与深度带交叠的本站剖面参照层，参照层数为应覆盖深度层数 `required`。
2. 每层用同区域、同口径且深度交叠的**最新版本站观测**匹配；无实测即记为“缺测”，并在 `uncertainty_reasons` 注明不以邻区补值。
3. 逐层偏离 `delta = 实测值 - (range_min + range_max) / 2`：`delta >= high_delta` 为高，`>= medium_delta` 为中，否则为低；同时给出实测是否落在参照区间内的 `in_range` 标志。
4. 覆盖率 `covered / required < minimum_coverage` 时整体判为“信息不足”且不给动作；否则取各层最高层级，并套用规则对应动作。
5. 每个评估、每个深度层、每条复议都携带来源 `sha256`，报告整体生成 `fingerprint`，已发布建议可凭指纹与来源回溯原始资料、旧口径和复议原因。
