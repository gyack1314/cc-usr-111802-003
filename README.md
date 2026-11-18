# 近海渔场指标分析

项目保存近海渔场的来源版次，按资料取得时间比较单期指标并给出区域规则判断。evidence 保存来源，metrics 计算同口径偏离，context 解释当前区域规则，report 保存报告。样本是合成数据。

## 海面单指标链路

`evidence → metrics → context → report`：海面温度等无深度字段的观测按 metric/region/unit 同口径与参照比较，再由区域规则给“常态/关注”判断。

## 温度剖面热风险链路

`thermal.py` 让四类输入共同决定可解释的热风险分层：

- **温度剖面**（带 `depth_min`/`depth_max` 的 observation 与 range 参照）；
- **渔种活动深度**（`habitat`，决定必须覆盖哪些深度层）；
- **历史参照区间**（`range_min`/`range_max`，偏离按区间中点计算，并标注是否越界）；
- **捕捞规则**（`thermal_rule`：覆盖率下限、中/高偏离阈值、各层级动作）。

覆盖不足即判“信息不足”，不给停捕/限捕动作；缺测层逐层保留不确定性。**邻近海区的异常只登记在 `excluded_other_region`，绝不补成本站实测**，单位口径不符的本站剖面同样只登记不采用。

补采或质量复核通过 `review` 记录登记，`reconsider` 输出复议：前后层级与动作、逐层旧/新口径（含观测版次与取值）、复议原因、受影响输入，以及两份已发布报告的 `fingerprint` 和全部来源 `sha256`——管理站可据此追到原始资料与该调整的依据。

Python 3.11 以上，在项目根目录执行：

    python -m unittest discover -s tests -v
    python -m compileall -q climate_analysis tests
    python -m climate_analysis.cli inspect data/example.json

示例数据覆盖完整叙事：8 月覆盖率 2/4 → 信息不足（邻区异常被排除）；补采后 9 月全覆盖 → 高层级停捕；质量复核订正 10–30 米实测值后 10 月降为中层级限捕，两次复议均保留旧口径。

Dockerfile 可在 Linux 容器运行：docker build -t climate-analysis-003 .；docker run --rm climate-analysis-003。
