# tech-spec-review-rule-server

技术规范书审查的**规则服务端**：规则库、向量索引与规则入库管道。

客户端（DeepSeek Harness 插件 [`dsh-tech-spec-review`](https://github.com/bronya19c818/dsh-tech-spec-review)）
只通过 HTTP 访问这里，不持有数据库连接串，也不持有嵌入模型。

## 为什么规则库在服务端

| 理由 | 说明 |
|------|------|
| 规则是共享资产 | 一次入库、全员同一份。放在各自机器上会版本漂移 |
| 索引必须一致 | 向量列维度与模型固定，查询向量必须落在同一空间 |
| 嵌入模型跟着索引走 | 客户端换对话模型不影响别人，换嵌入模型会让索引失效 |
| 减少客户端体积 | 不必每台机器都装 PostgreSQL（约 40MB） |

## 组成

```
server.py            启动入口（serve / stop / status / info / import）
app/main.py          Registry API（原有 /v1/rules/* 与 /v1/basic-rules/*）
app/semantic.py      嵌入调用 + pgvector 召回 + 分块写入
db/postgres.py       嵌入式 PostgreSQL 启动与复用
db/sql/*.sql         建表与迁移（含 rule_embeddings）
pipeline/            规则入库：清洗（rule_clean）→ 发布 → 建索引
setup.bat            首次准备（独立 venv + 依赖）
start.bat/stop.bat/status.bat
```

## 快速开始

```bat
setup.bat      :: 首次：用宿主 Python 3.12 建独立 venv 并装依赖
:: 编辑 .env，至少填 TSR_EMBEDDING_API_KEY
start.bat      :: 后台启动，等就绪后打印状态
status.bat     :: 查看服务状态
stop.bat       :: 停止（连同 PostgreSQL 子进程）
```

首次启动会 `initdb` 并执行建表脚本，约 90 秒；之后秒起。数据库在 `data/pg`。

## 导入规则

服务需先运行。规则 Markdown 必须含 `- 规则状态：...`，双规则域由 `规则域` 字段或
`规则类型 ∈ {basic_structure, basic}` 判定。

```bat
.venv\Scripts\python.exe server.py import <规则 md 文件或目录>
```

流程：清洗并推导身份 → `POST /v1/rules/publish` 或 `/v1/basic-rules/publish`
→ 按 ROUTE 分块 → `POST /v1/semantic/chunks`（向量在服务端算）。

说明文档等非规则文件会被清洗阶段按"缺少规则状态字段"拒收，属正常保护。

## 接口

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/health` | 存活与数据库连通 |
| GET | `/v1/service-info` | 嵌入模型、维度、索引规模、生效规则数 |
| POST | `/v1/rule-packages/list` | 已发布规则目录（仅元数据） |
| POST | `/v1/rules/get`、`/v1/basic-rules/get` | 取完整规则正文 |
| POST | `/v1/rules/publish`、`/v1/basic-rules/publish` | 发布规则（按 content hash 幂等，正文变化自动升版） |
| POST | `/v1/rules/semantic-search` | 语义召回候选分块 |
| POST | `/v1/semantic/chunks` | 写入/替换某个规则身份的索引分块 |
| POST | `/v1/object-scopes/resolve` | 物资类型别名解析 |

## 安全模型

- 默认只绑 `127.0.0.1`，外部不可达。
- **对外监听必须设 `RULE_SERVER_TOKEN`**，否则 `serve` 直接拒绝启动——写接口能改规则与索引，
  未鉴权的局域网暴露等于把审查依据交出去。设了令牌后，除 `/health` 外都要
  `Authorization: Bearer <token>`；客户端设置里的「服务令牌」填同一个值。
- 单次请求体默认上限 32MB（`RULE_SERVER_MAX_BODY_BYTES`）。
- 服务端不存对话模型密钥；客户端不存嵌入模型密钥。

## 配置

见 `.env.example`。关键项：

| 变量 | 说明 |
|------|------|
| `RULE_SERVER_HOST` / `_PORT` | 监听地址与端口，默认 `127.0.0.1:8010` |
| `RULE_SERVER_TOKEN` | 对外监听时必填的访问令牌 |
| `RULE_SERVER_DATA_DIR` | 数据目录，默认 `./data` |
| `TSR_EMBEDDING_BASE_URL` / `_MODEL` / `_API_KEY` / `_DIMENSIONS` | 嵌入服务（OpenAI 兼容 `/embeddings`） |

## 换嵌入模型

索引与模型绑定。换模型要一次做完：改 `.env` 的模型与维度 → 重建数据库
（停服务、删 `data/pg`、重启）→ 重新 `import` 全部规则。否则新旧向量不可比。

## 已知边界

- 只支持单实例：嵌入式 PostgreSQL 按数据目录独占，不支持多进程共用同一 `data/pg`。
- 没有备份机制：`data/pg` 即全部状态，需要备份就直接复制该目录（停服务后）。
- 规则版本只保留当前版本正文的历史表结构，未做旧版本清理策略。
