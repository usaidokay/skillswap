# 换技 SkillSwap · 后端

FastAPI + SQLite，单进程即可运行。前端目前是静态雏形（根目录 `index.html`，数据在 localStorage），本后端是它的服务端对应实现，接口一一对应前端已有的交互。

## 运行

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app:app --reload --port 8787
```

- 接口文档：http://localhost:8787/docs
- 首次启动自动建表并灌入 14 位示例用户
- 环境变量：`SKILLSWAP_DB`（数据库路径）、`SKILLSWAP_ORIGINS`（CORS 白名单，逗号分隔）

## 测试

```bash
.venv/bin/python -m unittest tests.test_api
```

覆盖整条主流程：游客注册 → 匹配 → 发起交换 → 打卡到完成 → 互评 → 时间币结算 → 消息。

## 结构

| 文件 | 职责 |
| --- | --- |
| `db.py` | 表结构、连接、时间币唯一入账口 `add_coins` |
| `matching.py` | 匹配引擎，纯函数，不依赖数据库 |
| `seed.py` | 品类表与示例用户 |
| `app.py` | HTTP 接口 |

## 建模要点

**一次交换 = 一份契约 + 若干条「谁教谁什么」的边。**
双向互换 2 条边，三角互换 3 条边，时间币约课 1 条边——所有形态共用同一套状态流转（pending → active → done / cancelled）和同一个打卡接口，新增形态（例如一对多小组课）只需放宽边数校验。

**互换必须闭环。** `mutual` / `chain` 创建时校验「授课方集合 = 学习方集合 = 参与方集合」，保证每个人都既教又学。

**时间币只有一个入账口。** 余额与账本在同一事务内变动，任何时候 `SUM(ledger.delta) = users.coins`。

**匹配结果可解释。** 每条匹配带 `reasons`，前端直接展示，不做黑箱推荐。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/auth/guest` | 游客注册，返回 token，赠 3 枚时间币 |
| GET / PATCH | `/api/me` | 我的资料 |
| PUT | `/api/me/skills` | 整体替换两张清单 |
| GET | `/api/ledger` | 时间币账本 |
| GET | `/api/categories` · `/api/skills` · `/api/users/{id}` | 发现 |
| GET | `/api/matches` | 双向互补 / 三角互换 / 时间币约课 / 想向我学的人 |
| POST / GET | `/api/swaps` | 发起 / 列出契约 |
| POST | `/api/swaps/{id}/accept` · `/cancel` | 确认 / 退出 |
| POST | `/api/swaps/{id}/legs/{leg}/checkin` | 完成一节并打卡，时间币形态在此结算 |
| POST | `/api/swaps/{id}/review` | 互评 |
| GET | `/api/threads` · GET / POST `/api/threads/{peer}/messages` | 消息 |

## 尚未做

- 正式账号体系（现为游客 token）
- 打卡双方确认（现为任一方打卡即生效）
- 爽约押币与自动赔付
- 示例用户的自动确认 / 自动回复仅为让雏形流程可走通，接入真实用户后移除
