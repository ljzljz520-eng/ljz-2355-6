# policy-hub 企业制度资料库

文档站按**岗位 / 设备 / 场景**过滤，权限服务计算可见范围；数据库保存制度**生效区间、
例外与签收**；岗位继承与例外冲突时有**确定性优先级**并输出**适用推导链**；
知悉签收、培训完成、作业授权是**三个独立状态**。

## 模块

| 模块 | 职责 |
|---|---|
| `policy_hub/db.py` | SQLite 模式：生效区间、规则/例外、签收、培训、授权、离线令牌、预计算集 |
| `policy_hub/resolver.py` | 适用性裁决（优先级 + 推导链）、版本重叠裁定、历史时点岗位回溯 |
| `policy_hub/lifecycle.py` | 签收 / 培训 / 授权三个独立状态机 |
| `policy_hub/documents.py` | 文档站三维过滤 + 可见范围；公开页白名单元数据 |
| `policy_hub/offline.py` | 离线附件有限期令牌，校验时重估在职与可见性 |
| `policy_hub/precompute.py` | 按岗位预计算适用集，与请求时求值对比 |
| `policy_hub/projects.py` | 项目登记（绝不自动生成安全规则） |

## 冲突优先级（确定性，不允许"拼出全部命中条款让员工猜"）

1. **主体更具体者优先**：用户级 > 本岗位 > 上级岗位（沿继承树逐级衰减）；
2. **同主体层级**：例外（exception） > 基础（base）；
3. **仍并列**：`valid_from` 晚者优先，再按 id 大者优先；
4. **版本有效期重叠**：`effective_from` 新者优先，其次 `version_no` 高者优先。

每次裁决返回唯一结论 + 推导链（`Resolution.explain()`）：哪些规则命中、谁胜出、
谁因何让位、哪些例外已过期但留痕。示例：

```
[winner]     规则#2 本岗位[焊工] 例外-排除 [2026-01-01~2026-02-01] —— 裁决胜出：【不适用】
[superseded] 规则#1 上级岗位(第1层)[焊接车间] 基础-纳入 —— 优先级低于规则#2，让位
=> 结论：不适用
```

## 三个独立状态（一次点击阅读 ≠ 培训 ≠ 授权）

| 状态 | 绑定 | 失效条件 |
|---|---|---|
| 知悉签收 `acks` | **具体版本** | 签收途中发布新版本 → 原签收置 `stale`，须重读新版 |
| 培训完成 `trainings` | **培训资源** | 链接撤销或超出有效期 → 完成记录不再有效 |
| 作业授权 `authorizations` | 前置两者 | 可撤销、可过期；校验时前置条件必须**持续**成立 |

`check_authorization` 在作业前重新校验：授权有效 **且** 当前版本已签收 **且**
培训链接仍有效。因此制度再修订或培训链接失效会立即回收作业资格。

## 预计算适用集 vs 请求时求值

| 维度 | 按岗位预计算 | 请求时求值 |
|---|---|---|
| 读性能 | O(1) 查集合，适合列表/目录页 | 每请求沿继承树评估，较慢 |
| 新鲜度 | 规则/岗位/例外变更后**必须重建**，否则脏缓存 | 永远最新，调岗立即生效 |
| 用户级例外 | 不覆盖（只算岗位维度） | 完整支持 |
| 落地用法 | 文档站列表、目录加速 | 授权、离线校验等一切决策点 |

`precompute.compare()` 可检测 `missing_in_precomputed`（漏算）与
`stale_in_precomputed`（脏数据/用户级例外差异），用于缓存失效事件的回归验证。

## 调岗、撤权、离职与离线附件

- `position_history` 保存调岗区间：请求时求值立即用新岗位；`position_at(as_of)`
  支撑历史时点追溯（当时岗位 + 当时版本）；
- 离线令牌带 TTL；`validate_offline` 每次都重估：过期、已撤销、**用户已离职**、
  调岗后可见性回收，均拒绝——离职员工的旧缓存即使未过期也即时作废。

## 验收场景 → 测试对照

| 验收场景 | 测试 |
|---|---|
| 例外到期，继承恢复且留痕 | `TestExceptionExpiry` |
| 制度有效期重叠，确定性裁定 | `TestVersionOverlap` |
| 签收时版本更新，旧签收作废 | `TestAckVersionChange` |
| 离职用户旧缓存即时作废 | `TestOfflineGrant.test_departed_user_token_rejected` |
| 培训链接失效，授权被回收 | `TestTrainingLinkInvalid` |
| 旧制度按历史时点追溯 | `TestTransferAndHistory` |
| 公开页只呈现有权元数据 | `TestPublicCatalog` |
| 项目不自动生成作业安全规则 | `TestProjectNoAutoRules` |
| 调岗立即生效 / 撤权 | `TestTransferAndHistory` / `TestRevokeAuthorization` |
| 三状态不可互相替代 | `TestThreeStatesDistinct` |
| 推导链（同级例外优先、用户级优先） | `TestDerivationChain` |
| 预计算 vs 请求时一致性 | `TestPrecomputeVsRequest` |
| 文档站三维过滤 + 可见范围 | `TestDocSearch` |

## 运行

```bash
cd policy-hub
python3 -m unittest discover -v   # 17 个验收测试
python3 demo.py                   # 场景演示（推导链、三状态、离线令牌、公开页）
```
