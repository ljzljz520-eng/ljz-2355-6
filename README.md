# 企业制度资料库（PolicyHub）

文档站按**岗位 / 设备 / 场景**过滤；权限服务在**请求时**计算可见范围并输出**适用推导链**；
数据库保存制度的**生效区间、例外、签收**等全部时间事实。零外部依赖（Python 3.11 标准库）。

## 运行

```bash
python3 run_demo.py --port 8080
# 测试
python3 -m unittest discover -s tests        # 45 个验收测试
```

## 1. 适用优先级（冲突不靠员工猜）

岗位继承与例外冲突时按以下固定优先级裁决，推导链中**每一条命中与被压制的来源都带理由展示**，
唯一获胜条目标记 `won`：

| rank | 来源 | 含义 |
|---|---|---|
| 0 | `USER_DENY` | 针对个人的排除（安全最高优先；含“无有效任职/离职”） |
| 1 | `USER_GRANT` | 针对个人的形入（人工白名单，窄于岗位规则，可压过岗位排除） |
| 2 | `POSITION_DENY` | 本岗或祖先岗的排除例外（压过一切岗位形入） |
| 3 | `POSITION_GRANT` | 本岗或祖先岗的形入例外 |
| 4 | `DIRECT` | 制度范围直接覆盖本岗 |
| 5 | `INHERITED` | 沿岗位树继承命中，**最近祖先优先**（距离越近越优先） |
| 6 | `NONE` | 默认不可见 |

- 安全倾向：`DENY` 在更宽层级不会被继承的 `GRANT` 绕过；仅**个人形入**这种更窄的人工例外可压过岗位排除。
- 例外绑定到**具体版本**，修订新版本不会让旧例外静默延续。
- 设备/场景不匹配的岗位命中以 `inert`（rank 98/99）保留在链中并解释原因，绝不参与裁决。

推导链示例（carol，行政岗，10 月有临时个人形入）：

```
GRANT v1  ← USER_GRANT（赢）
  [USER_GRANT]    carol 本月兼任应急物资盘点，需查阅 PPE（限期）        <= WINS
  [POSITION_DENY] 岗位 '行政办公室' 的排除例外: 不承担化学品搬运
  [INHERITED]     经岗位继承链命中祖先岗位 '公司'（距离 1）
```
例外 10-20 到期后再查，同一接口回落为 `POSITION_DENY`，并列出“例外已过生效区间，忽略”。

## 2. 三种状态严格分离

| 状态 | 表 | 触发 | 版本绑定 |
|---|---|---|---|
| 知悉签收 | `policy_acks` | 必须先开**读会话**（短期、一次性、校验内容哈希）再签收 | 修订后旧签收 `STALE` |
| 培训完成 | `policy_training` | **单次、限期**培训链接完成 | 修订后旧培训 `STALE` |
| 作业授权 | `work_authorizations`(+`auth_revocations`) | 管理员独立授予，区间有效、可随时撤销 | 按制度（不随阅读/培训产生） |

一次“点击阅读”只能满足签收；`/me/work-gate` 对四道门分别判定：
适用版本、已签收当前版、已培训当前版、持有有效授权——缺一不可。

## 3. 预计算岗位集 vs 请求时求值

- `precompute.rebuild` 按岗位折叠 DIRECT/INHERITED 与 POSITION_* 例外，快照 id 为
  `position_id:rule_epoch`；**不能**编码个人例外、设备/场景、在职、培训、授权——这些只在请求时套用。
- `/admin/compare` 同时跑两条路径并报告漂移（验收要求为零漂移）。
- 修订/例外/撤权都会 `bump_rule_epoch`，旧快照即 `miss`/`stale`；调岗改变 snapshot 的岗位部分。

## 4. 调岗 / 撤权 / 离职 / 离线

- 调岗：关闭旧任职区间、在新岗位重新推导；未显式保留的作业授权批量撤销。
- 离职：关闭任职、撤销全部作业授权、吊销全部离线令牌；此后任何请求（即使持有旧缓存/令牌）一律拒绝。
- 离线附件：限期令牌 + 快照哈希；兑换时**重新校验在职、当前可见性、版本仍有效、哈希一致**。
  令牌未到期但例外失效/调岗/修订都会切断访问（`OFFLINE_NO_LONGER_VISIBLE` 等）。

## 5. 版本与历史追溯

- 所有生效事实均为半开区间 `[valid_from, valid_to)`；修订只新增 `policy_versions` 行，旧行永不改写。
- 有效期重叠：选最新版本并在 `warnings` 明确报“有效期重叠”，不静默拼接。
- `/admin/history?at=...` 可按任意历史时点还原当时生效版本与正文文本。

## 6. 公开边界与“不自动生成规则”

- `/public/policies` 只返回显式标记 `public_meta` 的制度的**元数据**（编号/标题/类别/版本/区间），
  无正文、范围、例外、附件、哈希。
- 正文/附件接口先过实时适用判定，否则 403。
- 全库只有 `catalog.py` 能写入制度内容，且每条版本记录人工作者；API 不提供任何
  自动生成/推断作业安全规则的入口（有静态守卫测试）。

## API 摘要

```
GET  /public/policies
GET  /me/policies?username&equipment&scene&at&include_denied&client_snapshot_id
GET  /me/policy | /me/derivation | /me/status | /me/work-gate | /me/attachments | /me/offline
POST /me/read-session | /me/ack | /me/training-complete
POST /me/offline/issue | /me/offline/redeem
POST /admin/grant-work | /admin/revoke-work | /admin/issue-training
POST /admin/transfer | /admin/leave | /admin/rebuild-sets
GET  /admin/compare | /admin/history
```

## 模块

`clock`（可注入时间）· `db`（区间化 schema）· `org`（岗位树/任职史）·
`engine`（优先级+推导链+时点求值）· `catalog`（唯一人工创作边界）·
`states`（签收/培训/授权三态）· `offline`（限期离线）·
`precompute`（岗位集与漂移对比）· `service`（门面与编排）· `api`（薄 HTTP）。
```
