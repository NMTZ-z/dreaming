import sqlite3, os, json

MEMORY_DIR = r"./memory"
DB_PATH = os.path.join(MEMORY_DIR, "knowledge", "dreams.db")
DREAMS_MD = os.path.join(MEMORY_DIR, "DREAMS.md")
STATE_FILE = os.path.join(MEMORY_DIR, "dreaming_state.json")
ARCHIVE_DIR = os.path.join(MEMORY_DIR, "archive", "dreams")
MAX_DREAMS_MD_CHARS = 17000
KEEP_DAYS = 14

CATEGORY_LABELS = {
    "fact": ("📌 事实", "用户的事实信息"),
    "preference": ("💗 偏好", "用户的喜好和偏好"),
    "emotion": ("💕 情感", "AI和用户的情感时刻"),
    "rule": ("📏 规矩", "用户定的规矩和要求"),
    "quote": ("💬 金句", "用户说过的话"),
}


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dreams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            category TEXT NOT NULL,
            theme TEXT DEFAULT '',
            content TEXT NOT NULL,
            context TEXT DEFAULT '',
            emotion TEXT DEFAULT '',
            source_date TEXT DEFAULT '',
            content_hash TEXT UNIQUE,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_category ON dreams(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON dreams(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hash ON dreams(content_hash)")

    # V4 schema 升级：添加新字段（兼容旧数据库）
    _migrate_schema(conn)

    conn.commit()
    conn.close()


def _migrate_schema(conn):
    """V4 schema 迁移：添加新字段并迁移现有数据。"""
    # 检查是否已迁移
    cursor = conn.execute("PRAGMA table_info(dreams)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    new_columns = {
        'status': 'TEXT DEFAULT \'candidate\'',
        'mention_count': 'INTEGER DEFAULT 1',
        'first_seen': 'TEXT',
        'last_seen': 'TEXT',
        'confidence': 'REAL DEFAULT 0.7',
        'skill_candidate': 'INTEGER DEFAULT 0',
    }

    for col, definition in new_columns.items():
        if col not in existing_cols:
            try:
                conn.execute(f"ALTER TABLE dreams ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass  # 字段可能已存在

    # 迁移现有数据：status 为空的设为 confirmed
    try:
        conn.execute("""
            UPDATE dreams SET 
                status = 'confirmed',
                mention_count = COALESCE(mention_count, 1),
                confidence = COALESCE(confidence, 0.7),
                first_seen = COALESCE(first_seen, date),
                last_seen = COALESCE(last_seen, date)
            WHERE status IS NULL
        """)
    except sqlite3.OperationalError:
        pass

def save_fragments(fragments, theme='daily'):
    """保存碎片到 dreams.db，包含完整的 V4 字段（status/mention_count/first_seen/last_seen/confidence）。

    Phase 2 调用此函数写入碎片，Phase 3 的 promote_fragments 会基于这些字段做时间验证。
    如果 first_seen 缺失，碎片永远无法晋升 confirmed（_days_between 返回 0）。
    """
    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_PATH)
    saved = 0
    for f in fragments:
        try:
            changes_before = conn.total_changes
            confidence = f.get('confidence', 0.7)
            # confidence 兜底：LLM 可能返回字符串 "0.8"
            if isinstance(confidence, str):
                try:
                    confidence = float(confidence)
                except (ValueError, TypeError):
                    confidence = 0.7
            confidence = max(0.0, min(1.0, confidence))

            conn.execute(
                "INSERT OR IGNORE INTO dreams "
                "(date, category, theme, content, context, emotion, content_hash, "
                "status, mention_count, first_seen, last_seen, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate', 1, ?, ?, ?)",
                (f['date'], f['category'], theme, f['content'], f.get('context', ''),
                 f.get('emotion', ''), f['content_hash'],
                 today, today, confidence)
            )
            if conn.total_changes > changes_before:
                saved += 1
        except Exception as e:
            print(f"  ✗ 写入失败: {e}")
    conn.commit()
    conn.close()
    return saved

def get_existing_hashes():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT content_hash FROM dreams").fetchall()
    conn.close()
    return {r[0] for r in rows}

def search_fragments(keyword, category=None, limit=10):
    conn = sqlite3.connect(DB_PATH)
    sql = "SELECT date, category, content, context, emotion FROM dreams WHERE content LIKE ?"
    params = [f"%{keyword}%"]
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY date DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows

def generate_dreams_md():
    conn = sqlite3.connect(DB_PATH)
    from datetime import datetime, timedelta
    cutoff_date = datetime.now() - timedelta(days=KEEP_DAYS)
    cutoff = cutoff_date.strftime('%Y-%m-%d')
    if cutoff:
        rows = conn.execute("""
            SELECT date, category, content, emotion
            FROM dreams WHERE date >= ? ORDER BY date DESC
        """, (cutoff,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT date, category, content, emotion
            FROM dreams ORDER BY date DESC
        """).fetchall()
    conn.close()

    grouped = {}
    for date, cat, content, emotion in rows:
        grouped.setdefault(date, []).append((cat, content, emotion))

    md_parts = ["# 🌙 记忆梦境\n", "> 不是执行日志，是AI整理的关于用户的一切。\n"]
    char_count = len(md_parts[0]) + len(md_parts[1])

    for date in sorted(grouped.keys(), reverse=True):
        if char_count > MAX_DREAMS_MD_CHARS:
            break
        items = grouped[date]
        cat_items = {}
        for cat, content, emotion in items:
            cat_items.setdefault(cat, []).append((content, emotion))

        day_lines = [f"\n## {date}\n"]
        for cat in ['fact', 'preference', 'emotion', 'rule', 'quote']:
            if cat not in cat_items:
                continue
            label, _ = CATEGORY_LABELS[cat]
            day_lines.append(f"### {label}\n")
            for content, emotion in cat_items[cat]:
                line = f"- {content}"
                if emotion:
                    line += f"（{emotion}）"
                day_lines.append(line)
                char_count += len(line) + 1
            if char_count > MAX_DREAMS_MD_CHARS:
                break
        if char_count > MAX_DREAMS_MD_CHARS:
            break
        md_parts.append("\n".join(day_lines))

    return "\n".join(md_parts)

def write_dreams_md(content):
    os.makedirs(os.path.dirname(DREAMS_MD), exist_ok=True)
    with open(DREAMS_MD, 'w', encoding='utf-8') as f:
        f.write(content)

def read_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"last_processed": "", "last_run": "", "version": "3.0"}

def write_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)