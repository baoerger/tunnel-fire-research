# G5-SR 执行补充协议批准记录

## 结论

用户于 2026-08-23 批准按执行补充协议 v1 继续。批准版为 `config/g5_sr_execution_supplement_v1.json`，精确 SHA-256 为 `c3fe3963d6eaedd1cc3fbb65c73a0bcf57cb260fd2f6fa9c40d3371f40640a55`。父科学协议仍为 `config/symbolic_regression_v1.json`，SHA-256 为 `453c6a9dbc6ba19d096c9ae6dfb6836b82df3f4ef8501b17a41e60f12dc21a8b`。正式执行器必须同时核验两份协议，任一哈希漂移即停止。

本次批准不授权新增或重跑 FDS，也不授权生成、运行或读取锁箱。锁箱继续保持 `SEALED_UNREAD_UNRUN`。

## 精度说明

SR5 采用的同步时间块表不是均值、置信区间或抽稀后的摘要。生成器对每条已接受开发运行的冻结稳态窗口执行步长为 1 个样本的全部循环移动块；同一个 `time_block_id` 在所有空间测点上使用完全相同的源时间索引，并以可往返的 17 位有效数字保存二进制双精度值。因此，按批准的 SR5 分层时间块 Bootstrap 使用该表时，不损失协议所需的时间块信息。

该表只对冻结的 SR5 分析等价，不宣称能够替代原始 `_devc.csv` 进行任意新的时序分析。

## 已生成并验证的制品

- 同步时间块表：`derived/development/g5_sr_synchronized_time_block_profiles.csv`
- 表 SHA-256：`428cf96c8958cce70d401b8e8cc5d8ad1a56fddc19431e4476756ec05787b667`
- 规模：27 个运行、1,633 个同步时间块、80,017 行、14,696,463 字节
- 来源清单：`reports/g5_sr_synchronized_time_block_manifest.json`
- 清单 SHA-256：`f209b739b50ef74ac3848c024b2cc7ff82eeae66360948979aac585487b48055`
- 生成器：`src/analysis/build_g5_sr_time_blocks.py`

生成时逐项通过了运行身份、冻结窗口覆盖、源文件哈希、T90 空间坐标、冻结块长、同步索引、循环回绕、复合主键、完整行数和精确复读校验。对 1,323 个工况/测点组合重新平均全部循环时间块后，与冻结曲线均值的最大绝对差仅为 `4.55e-13 K`，远低于 `1e-9 K` 的一致性容差。27 份原始 `_devc.csv` 继续由 `runs/` 忽略规则留在本机，不进入 Git。

## 当前执行状态

两张冻结开发表和 SR5 同步时间块输入均已就位，完整预检返回 `READY_FOR_G5_SR_SEARCH`；dry-run 返回初始 490 个 trial、条件追加 245 个 trial、主搜索最多 735 个 trial，且 `lockbox_used=false`。正式 PySR 搜索尚未启动，`A_SR` 和 `A_active` 尚未生成。下一步是运行初始 490-trial 搜索。
