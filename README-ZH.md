<div align="center">

<img src="./frontend/src/assets/logo/beemarketbot-logo.jpg" alt="BeeMarketBot" width="360"/>

# BeeMarketBot

**预测万物。** 上传一份文档，提出一个问题，即可获得一份基于 AI 智能体社会模拟的报告。

[Español](./README.md) | [English](./README-EN.md) | 中文

</div>

## 功能

BeeMarketBot 根据一份文档构建一个模拟世界，并让这个世界自行演化，从而回答
"如果……会怎样？"的问题。

你只需上传素材（报告、数据，或 PDF、MD、TXT 格式的任意文本），并用自然语言
描述你想了解的问题。系统会分五步完成剩下的工作：

1. **图谱构建。** 从文档中提取实体和关系，构建一个带记忆的知识图谱，按语义检索。
2. **环境配置。** 根据这些实体生成智能体的画像，并交给它们各自的记忆和配置。
3. **模拟。** 智能体共同生活在两个社交平台上（一个类似 Twitter，一个类似
   Reddit），在你设定的轮数内相互反应，记忆随发生的事情不断更新。
4. **报告。** 报告智能体借助查询工具回溯整个模拟过程，写出包含答案的报告。
5. **交互。** 你可以与模拟世界中的任意智能体对话，也可以和报告智能体深入探讨。

你只需要上传素材、描述问题。BeeMarketBot 返回一份报告，以及一个可以继续提问的
模拟世界。

界面提供西班牙语（默认）、英语和中文，模型会用所选语言回答。

模拟会大量调用模型。建议先从较少的轮数开始（少于 40 轮），之后再逐步增加。

## 访问

每个页面和每个 API 路由都要求登录。没有公开注册，也没有默认密码。访问权限
分为三级：

| 级别 | 权限 |
|---|---|
| `admin` | 全部权限，另外可以管理账户（`/users`） |
| `editor` | 创建并运行模拟，生成报告 |
| `viewer` | 只读 |

首个管理员只需创建一次，在项目目录下执行：

```bash
# 使用 Docker：仅显示一次初始密码
docker compose exec -T beemarketbot sh -c "cd backend && uv run --frozen python -m app.auth create-user --username <用户名> --role admin --generate-password"

# 不使用 Docker：在终端中输入密码
cd backend && uv run python -m app.auth create-user --username <用户名> --role admin
```

首次登录时必须修改该密码。之后可以在管理员的菜单中打开 **用户** 页面创建账户，
也可以使用[命令行](#账户管理)。

## 使用 Docker 部署

需要 Docker 和 Docker Compose v2。会启动两个容器：应用和图数据库。

```bash
git clone https://github.com/npalmae/BeeMarketBot.git
cd BeeMarketBot

# 1. 配置
cp .env.example .env
#    编辑 .env：模型、embeddings 以及 SECRET_KEY（openssl rand -hex 32）

# 2. 构建并启动（应用 + FalkorDB）
docker compose up -d --build

# 3. 创建首个管理员（见「访问」）

# 4. 用你的真实配置检查图谱记忆
docker compose exec beemarketbot sh -c "cd backend && uv run --frozen python scripts/validate_graph_memory.py"
```

应用读取项目根目录下的 `.env`，开放端口 `3000`（界面）和 `5001`（API）。如果
端口被占用，可在 `.env` 中设置 `WEB_PORT` 和 `API_PORT`。FalkorDB 只能从本机
访问，地址为 `127.0.0.1:6379`（可用 `FALKORDB_PORT` 更改端口）。

数据保存在容器之外：`./backend/uploads`（项目、模拟、报告、记忆状态和账户）以及
`falkordb-data` 数据卷（图谱）。更新时再次执行 `docker compose up -d --build`。

## 从源码部署

| 工具 | 版本 | 用途 |
|---|---|---|
| Node.js | 20.19 及以上（或 22.12 及以上） | 界面 |
| Python | 3.11 或 3.12 | 后端 |
| uv | 较新版本 | Python 环境 |
| FalkorDB | 6.0.x（或 Neo4j 5.26 及以上） | 记忆使用的图数据库 |

```bash
# 依赖（根目录 + 前端 + 后端）
npm run setup:all

# 图数据库（仅首次）
docker run -d --name falkordb -p 127.0.0.1:6379:6379 -v falkordb-data:/data falkordb/falkordb:6.0.4

# 界面和后端
npm run dev
```

界面位于 `http://localhost:3000`，API 位于 `http://localhost:5001`。配置同样
使用根目录下的 `.env`。

## 配置

所有变量都在 `.env` 中设置；`.env.example` 对每一项都有说明。

| 变量 | 是否必需 | 用途 |
|---|---|---|
| `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL_NAME` | 是 | 语言模型。任何兼容 OpenAI API 格式的提供商都可以。 |
| `GRAPH_DB_URL` | 是 | 图数据库：FalkorDB（`falkordb://host:6379`）或 Neo4j（`neo4j://user:password@host:7687`）。使用 Docker 时已自动设置。 |
| `EMBEDDING_MODEL`、`EMBEDDING_DIMENSIONS` | 是 | 把文本转换成向量的模型，以及向量的维度。 |
| `SECRET_KEY` | 服务器上必需 | 为会话签名。不设置时会在启动时生成一个，每次重启后所有人都需要重新登录。 |
| `EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL` | 否 | 仅当 embeddings 由与模型不同的提供商提供时使用。 |
| `GRAPH_LLM_*` | 否 | 图谱抽取使用的另一个模型，以及记忆的其他设置。 |
| `LLM_TEMPERATURE`、`LLM_EXTRA_BODY` | 否 | 只接受固定温度、或需要自有参数的提供商。 |
| `LLM_BOOST_*` | 否 | 为并行模拟再配置一个提供商，以获得更多并发调用。 |
| `ADMIN_USERNAME`、`ADMIN_PASSWORD` | 否 | 启动时创建首个管理员，仅在还没有任何账户时生效。 |
| `SESSION_HOURS`、`COOKIE_SECURE`、`CORS_ORIGINS` | 否 | 会话时长（24 小时）、仅通过 HTTPS 发送 cookie、允许调用 API 的外部来源。 |

## 账户管理

命令在 `backend/` 下以 `uv run python -m app.auth <命令>` 的形式执行。

| 命令 | 作用 |
|---|---|
| `create-user --username <用户名> --role <级别>` | 创建账户；首次登录时必须修改密码。 |
| `list-users` | 列出账户。 |
| `set-password --username <用户名>` | 指定新密码；下次登录时必须修改。 |
| `set-role --username <用户名> --role <级别>` | 更改访问级别。 |
| `disable --username <用户名>` | 禁止该账户登录。 |
| `enable --username <用户名>` | 重新允许该账户登录。 |

使用 Docker 时在容器内执行：

```bash
docker compose exec -T beemarketbot sh -c "cd backend && uv run --frozen python -m app.auth list-users"
```

密码永远不会作为参数传递：在终端中输入、从标准输入读取（`--password-stdin`），
或者自动生成（`--generate-password`，只显示一次）。

账户保存在 `backend/uploads/auth/users.db` 中，与其他数据放在一起，密码以
scrypt 哈希保存。登录失败次数有限制。通过纯 HTTP 访问时密码是不加密传输的：
如果应用可以被可信网络之外访问，请在前面加上 HTTPS。

## 开发

```bash
# 后端测试
cd backend && uv run python -m pytest -q tests

# 构建界面
cd frontend && npm run build

# 修改组件后重新生成界面样式
cd frontend && node scripts/build-web2-theme.mjs
```

测试从不写入真实的数据目录。其中有五个测试会针对真实的图数据库检查记忆，
仅在设置了 `GRAPH_MEMORY_TEST_URL` 时运行。

```
backend/app/api           API 路由
backend/app/auth          账户、会话和访问级别
backend/app/graph_memory  图谱记忆（Graphiti）
backend/app/services      本体、图谱、模拟和报告
backend/scripts           模拟运行脚本和记忆校验
backend/tests             后端测试
frontend/src              界面（Vue 3）
locales                   界面文案和消息文本
```

## 许可证与来源

BeeMarketBot 是自由软件，采用
[GNU Affero General Public License v3.0](./LICENSE)。它衍生自 666ghj 的
[MiroFish](https://github.com/666ghj/MiroFish)。社会模拟基于
[OASIS](https://github.com/camel-ai/oasis)（camel-ai），图谱记忆基于
[Graphiti](https://github.com/getzep/graphiti)。
