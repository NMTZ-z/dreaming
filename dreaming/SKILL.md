---
name: dreaming
description: 每日自动记忆巩固系统。从对话日志中解析、聚类、提炼记忆碎片，经时间验证后沉淀到长期记忆文件。当需要"记忆巩固"、"记忆沉淀"、"记忆整理"、"dreaming"时触发。支持碎片去重、跨天合并、MEMORY.md精准插入、WPS笔记同步。
---

# Dreaming Skill v4.0

> 每天凌晨自动从对话中提炼记忆碎片，经时间验证后沉淀到 MEMORY.md。

## 架构（V4 三阶段 + 同步）

```
对话日志 (chat/*.md)
    ↓ Phase 1: 浅睡（Light Sleep）— 解析 + 聚类
parse_chat_files(since_date) → 主题聚类 cluster_by_theme()
    ↓ Phase 2: REM — LLM 提炼 + 碎片合并
extract_fragments() → add_hashes(sha256[:16]) → deduplicate()
    → _resolve_event_date(内容解析日期) → save_fragments() → dreams.db (candidate)
    → consolidate_fragments() → 跨天同类碎片合并
    ↓ Phase 3: 深睡（Deep Sleep）— 时间验证 + 沉淀 + 同步
promote_fragments() → candidate → confirmed（满足条件时）
    → insert_fragments() → MEMORY.md
    → detect_skill_candidates() → skill_candidates.json
    → archive_stale_entries() → 归档过期碎片
    → generate_dreams_md() → DREAMS.md（倒序）
    → update_dreams_note() → WPS 笔记「记忆梦境」（增量追加）
```

## 定时任务

### 任务一：记忆梦境 Dreaming
- **Cron**: 每天 03:00
- **Prompt**: `执行今天的Dreaming流程（解析→聚类→LLM提炼→写入→时间验证→沉淀→WPS同步）。使用dreaming skill。`

### 任务二：记忆沉淀
- **Cron**: 每周一 04:00
- **Prompt**: `整理MEMORY.md，读取skill_candidates.json处理候选项，清理过时内容。`

## 文件结构

```
skills/dreaming/
├── SKILL.md
└── scripts/
    ├── dreaming/
    │   ├── __init__.py          # 导出 run_dreaming 等
    │   ├── main.py              # 主流程入口 run_dreaming()
    │   ├── parser.py            # 解析 chat 文件为记忆条目（含技术噪音过滤）
    │   ├── cluster.py           # 主题聚类（纯规则，不调 LLM）
    │   ├── extract.py           # LLM 提炼记忆碎片（含 6 条反偏差规则 + confidence）
    │   ├── storage.py           # dreams.db 读写 + DREAMS.md 生成（倒序） + schema 迁移
    │   ├── sleep.py             # 深睡处理器（recall/promote/consolidate/skill 检测/归档）
    │   └── memory_writer.py     # MEMORY.md 精准插入（token 上限 12000）
    └── wps_sync/
        ├── __init__.py          # 导出 update_dreams_note, get_wps_client
        ├── client.py            # WPS MCP 客户端（单例）
        └── sync.py              # WPS 笔记同步（增量追加，新日期插到最上面）
```

## 记忆文件

| 文件 | 路径 | 用途 | 生命周期 |
|------|------|------|----------|
| dreams.db | `<MEMORY_DIR>/knowledge/dreams.db` | 全量记忆碎片（SQLite） | 永久累积 |
| MEMORY.md | `<MEMORY_DIR>/MEMORY.md` | 长期记忆（confirmed 碎片沉淀） | Token 上限 12000 |
| DREAMS.md | `<MEMORY_DIR>/DREAMS.md` | 最近记忆摘要（注入对话上下文） | 14 天滑动窗口 |
| dreaming_state.json | `<MEMORY_DIR>/dreaming_state.json` | 运行状态（上次处理日期） | 运行时 |
| skill_candidates.json | `<MEMORY_DIR>/skill_candidates.json` | skill 候选检测结果 | Dreaming 检测时更新 |
| dreaming_note_id.json | `<MEMORY_DIR>/dreaming_note_id.json` | 当前 WPS 梦境笔记 ID | 同步时自动更新 |

## 执行流程（Agent 操作手册）

### 第一步：获取 prompts

```python
from scripts.dreaming.main import run_dreaming

result = run_dreaming()
# {"mode": "need_llm", "prompts": [...], "stats": {...}}
```

每个 prompt 是一个 JSON 提取任务，对应一个主题聚类。逐个执行，收集返回的**纯文本**（JSON 数组字符串）。

**特殊情况**：返回 `{"mode": "done", "stats": {"status": "no_new_entries"}}` 时直接结束。

### 第二步：带 LLM 结果完成完整流程

```python
def my_llm_call(prompt: str) -> str:
    # 调用 LLM，返回原始文本
    return response_text

final_result = run_dreaming(llm_call=my_llm_call)
# {"mode": "done", "stats": {...}}
```

`llm_call` 是 `callable(prompt) -> str`。Agent 需要包装一个函数。

### 第二步内部自动执行链路

1. **LLM 提取碎片** — 对每个主题聚类调用 llm_call，解析返回 JSON
2. **日期解析** — `_resolve_event_date(content, today)` 从碎片内容提取事件日期
3. **去重** — `add_hashes()` 生成 sha256[:16]，`deduplicate()` 去除已有
4. **写入 DB** — `save_fragments()` 带正确 date 写入 dreams.db（status='candidate'）
5. **碎片合并** — `consolidate_fragments()` 跨天同类碎片合并
6. **时间验证** — `promote_fragments()` 评估是否晋升 confirmed
7. **MEMORY.md 沉淀** — `insert_fragments()` 将 confirmed 碎片写入对应 section
8. **Skill 候选检测** — `detect_skill_candidates()` 标记反复出现的操作模式
9. **归档** — `archive_stale_entries()` 清理 30 天未出现的碎片
10. **DREAMS.md** — `generate_dreams_md()` 最近 14 天碎片摘要（倒序）
11. **WPS 同步** — `update_dreams_note()` 增量追加到云端笔记

### stats 关键字段

```
entries_parsed        — 解析的条目数
clusters              — 聚类数
fragments_extracted   — 提取的碎片数
fragments_promoted    — 晋升 confirmed 的碎片数
fragments_to_memory   — 写入 MEMORY.md 的碎片数
fragments_consolidated — 合并的碎片数
skill_candidates      — skill 候选数
archived              — 归档的过期碎片数
wps_synced            — WPS 同步是否成功
memory_tokens         — MEMORY.md 当前 token 数
```

## 日期解析（`_resolve_event_date`）

碎片入库前自动从内容解析事件日期，**不再强制覆盖为 today**：

1. 匹配「X月Y日」→ 取**最后一个**匹配 → 转为 `YYYY-MM-DD`（年份取当前）
2. 匹配 `YYYY-MM-DD` 格式 → 直接使用
3. 兜底返回 `today`（对话当天）

示例：
- "用户在3月15日去了上海展会" → `2026-03-15`
- "用户3月15日去了展会，3月14日也去了" → `2026-03-14`（取最后一个）
- "用户特别喜欢喝咖啡" → `today`（无日期信息，兜底）

## 时间验证制（candidate → confirmed）

碎片以 `candidate` 状态入库，满足以下**任一**条件晋升为 `confirmed`：

- **A. 频率+时间**: mention_count >= 2 且距 first_seen >= 3 天
- **B. 高置信度一次性**: confidence >= 0.8 的 fact/preference/rule，等 1 天冷却
- **C. Skill 候选**: skill_candidate == True 直接晋升

**归档淘汰**：mention_count <= 1 且距 last_seen > 30 天 → status = 'archived'

## MEMORY.md 精准插入

使用 L2 模块 + L3 标题路径组合定位插入点。

| 碎片分类 | 目标模块 | 目标 section | 匹配关键词 |
|---------|---------|------------|-----------|
| fact（工作类） | 模块一 | 近期工作动态 | 工作/项目/会议/论文/课题/科研/CSP/储能/出差/太原 |
| fact（身份类） | 模块一 | 基本信息 | 生日/年龄/星座/老家/住址/搬家/毕业/学历 |
| fact（生活类） | 模块二 | 兴趣爱好 | 买/搬/换/装修/旅游/出国/生病/体检/理发/驾照/手机 |
| fact（兜底） | 模块一 | 近期工作动态 | 无关键词匹配时 |
| preference（饮食） | 模块二 | 饮食偏好 | 吃/喝/咖啡/奶茶/辣/火锅/烤肉/零食/零食/酒 |
| preference（健身） | 模块二 | 健身习惯 | 健身/训练/卧推/深蹲/背/腿/胸/肩 |
| preference（兴趣） | 模块二 | 兴趣爱好 | 运动/旅行/阅读/游戏/音乐 |
| preference（审美） | 模块二 | 视觉审美偏好 | 丝袜/靴子/穿搭/衣服/裙子 |
| preference（兜底） | 模块二 | 兴趣爱好 | 无关键词匹配时 |
| rule | 模块三 | 羁绊约定 | 全部 |
| emotion | — | — | **不写入 MEMORY.md** |
| quote | — | — | **不写入 MEMORY.md** |

**安全机制**：
- Token 硬上限 12000（约 8000 字符），超限不写入
- 写入前自动备份 MEMORY.md.bak，失败时回滚
- 写入后校验文件完整性（非空且以 # 开头）

## 碎片合并（consolidate）

跨天碎片合并，减少重复记录：
- **实体提取**: 3 字中文滑动窗口 n-gram（去噪声词）+ 英文词（>=3 字符）
- **匹配条件**: 同 category + 共享 >= 2 个实体 + topic_coherent 双向比例 >= 0.20
- **合并策略**: 保留更长内容版本，累加 mention_count
- **精确重复**: content 完全相同则跳过
- **执行时机**: save_fragments() 之后，从 DB 查已有碎片做匹配

## 反偏差规则（extract.py 内置）

LLM 提炼时自动执行 6 条规则：
1. **因果链完整性** — 事件链 A→B→C 不能截断为 B→C
2. **代词消解** — "自己""那个"等必须回溯确认指代
3. **敏感话题保留原文** — 禁止得体化改写
4. **分类严格校验** — 单次事件归 fact，>=2 次才归 preference
5. **情绪标签规范** — 最多一个，禁止默认加"心疼"
6. **日期归属** — 归事件发生日，不是对话日

V4 新增：extract 返回 `confidence` 字段（0.0-1.0），支持数字和字符串格式。

## dreams.db Schema

```sql
CREATE TABLE dreams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    category TEXT NOT NULL,
    theme TEXT DEFAULT 'daily',
    content TEXT NOT NULL,
    context TEXT DEFAULT '',
    emotion TEXT DEFAULT '',
    source_date TEXT DEFAULT '',
    content_hash TEXT UNIQUE NOT NULL,  -- sha256[:16]
    created_at TEXT DEFAULT (datetime('now','localtime')),
    -- V4 字段 --
    status TEXT DEFAULT 'candidate',    -- candidate / confirmed / archived
    mention_count INTEGER DEFAULT 1,
    first_seen TEXT,
    last_seen TEXT,
    confidence REAL DEFAULT 0.7,
    skill_candidate INTEGER DEFAULT 0
);
```

## WPS 笔记同步策略

采用**增量追加模式**（编辑已有笔记），由 run_dreaming() 自动调用。

同步逻辑（sync.py）：
1. 从 `dreaming_note_id.json` 获取当前笔记 ID
2. 调用 `update_dreams_note(all_fragments, date=today)`
3. 如果该日期区块**已存在** → 往区块末尾追加新碎片
4. 如果该日期区块**不存在** → 在所有日期区块**最前面**新建（tag 之前）

**新增日期永远插在最上面**，形成倒序追加。

### edit_block 注意事项
- **insert** op: `block_id` + `anchor_id` + `position`
- **delete** op: 必须传 `block_ids`（数组），参数示例：
  ```python
  client._call("edit_block", {
      "note_id": nid, "block_id": bid, "op": "delete",
      "content": "", "block_ids": [bid],
  })
  ```
- **废弃接口**：`sync_dreams_to_wps()` 已移除，不要调用
