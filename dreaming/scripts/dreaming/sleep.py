# 深睡阶段处理器（Sleep Phase）
# V4 核心升级：时间验证制替代旧版六维评分

import os
import re
import sqlite3
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional

from .cluster import _STOP_WORDS, _ROLE_WORDS

MEMORY_DIR = r"./memory"
DB_PATH = os.path.join(MEMORY_DIR, "knowledge", "dreams.db")


def recall_check(theme: str, content: str, keywords: List[str] = None) -> int:
    """检查主题/内容在历史记忆中的提及次数。

    用 cluster.theme 关键词去查 dreams.db，而非碎片内容本身。
    因为"用户"等泛词命中率 93%，毫无区分度。

    Args:
        theme: 聚类主题名（如"太原出行""咖啡机"）
        content: 碎片内容
        keywords: 额外关键词列表（可选）

    Returns:
        历史命中次数（不含自身）
    """
    if not os.path.exists(DB_PATH):
        return 0

    conn = sqlite3.connect(DB_PATH, timeout=10)
    hits = 0

    # 用 theme 关键词匹配
    theme_kws = [w for w in re.findall(r'[\u4e00-\u9fff]{2,4}|[A-Za-z][A-Za-z0-9_-]{2,}', theme)
                 if w not in _ROLE_WORDS and w not in _STOP_WORDS]

    all_kws = list(set(theme_kws + (keywords or [])))

    if all_kws:
        # 至少匹配一个关键词
        params = []
        for kw in all_kws:
            # 每个关键词同时匹配 content 和 theme
            params.extend([f"%{kw}%", f"%{kw}%"])
        placeholders = " OR ".join("(content LIKE ? OR theme LIKE ?)" for _ in all_kws)
        sql = f"SELECT COUNT(DISTINCT content_hash) FROM dreams WHERE ({placeholders})"
        try:
            hits = conn.execute(sql, params).fetchone()[0]
        except Exception:
            hits = 0

    conn.close()
    return hits





def promote_fragments(fragments: List[Dict], today: str = None) -> Dict:
    """执行深睡阶段：评估碎片状态，决定是否晋升为 confirmed。

    晋升条件（满足任一）：
    A. mention_count >= 2 且距 first_seen >= 3 天（时间验证通过）
    B. category in [fact, preference, rule] 且 confidence >= 0.8（高价值一次性信息，需等1天）
    C. skill_candidate == True

    Args:
        fragments: [{"category", "content", "emotion", "confidence", "content_hash", ...}, ...]
        today: 日期字符串

    Returns:
        {
            "promoted": [已晋升的碎片],
            "candidates": [仍为候选的碎片],
            "to_memory": [需要写入 MEMORY.md 的碎片],
            "to_user": [需要同步到云端 user 的碎片],
        }
    """
    if not today:
        today = datetime.now().strftime('%Y-%m-%d')

    if not os.path.exists(DB_PATH):
        return {"promoted": [], "candidates": fragments, "to_memory": [], "to_user": []}

    conn = sqlite3.connect(DB_PATH, timeout=10)
    promoted = []
    candidates = []
    to_memory = []
    to_user = []

    for frag in fragments:
        chash = frag.get('content_hash', _content_hash(frag['content']))
        frag['content_hash'] = chash
        category = frag.get('category', 'fact')
        confidence = frag.get('confidence', 0.5)

        # 查询历史状态
        row = conn.execute(
            "SELECT id, status, mention_count, first_seen, last_seen, confidence, skill_candidate "
            "FROM dreams WHERE content_hash = ?",
            (chash,)
        ).fetchone()

        if row is None:
            # 全新碎片 → candidate
            _insert_new(conn, frag, today)
            candidates.append(frag)
            continue

        db_id, status, mention_count, first_seen, last_seen, db_conf, skill_candidate = row

        # 更新提及次数和最后出现时间
        new_mention = mention_count + 1
        conn.execute(
            "UPDATE dreams SET mention_count = ?, last_seen = ?, confidence = MAX(confidence, ?) "
            "WHERE content_hash = ?",
            (new_mention, today, confidence, chash)
        )

        # 判断是否晋升
        should_promote = False

        if status == 'confirmed':
            # 已经确认过了，跳过
            continue

        # 条件A：时间验证通过（被多次提及 + 间隔足够）
        days_since_first = _days_between(first_seen, today)
        if new_mention >= 2 and days_since_first >= 3:
            should_promote = True

        # 条件B：高价值一次性信息（需至少过1天冷却）
        if category in ('fact', 'preference', 'rule') and float(confidence) >= 0.8 and days_since_first >= 1:
            should_promote = True

        # 条件C：skill 候选
        is_skill_candidate = bool(skill_candidate)
        if is_skill_candidate:
            should_promote = True

        if should_promote:
            conn.execute(
                "UPDATE dreams SET status = 'confirmed' WHERE content_hash = ?",
                (chash,)
            )
            promoted.append(frag)

            # 决定沉淀目标
            if category in ('fact', 'preference', 'rule'):
                to_memory.append(frag)
            if category in ('fact', 'preference') and _is_user_related(frag['content']):
                to_user.append(frag)
        else:
            candidates.append(frag)

    conn.commit()
    conn.close()

    return {
        "promoted": promoted,
        "candidates": candidates,
        "to_memory": to_memory,
        "to_user": to_user,
    }


def _insert_new(conn, frag: Dict, today: str):
    """插入新碎片到 dreams.db"""
    chash = frag['content_hash']
    conn.execute(
        "INSERT OR IGNORE INTO dreams "
        "(date, category, theme, content, context, emotion, content_hash, "
        "status, mention_count, first_seen, last_seen, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate', 1, ?, ?, ?)",
        (
            frag.get('date', today),
            frag.get('category', 'fact'),
            frag.get('theme', ''),
            frag['content'],
            frag.get('context', ''),
            frag.get('emotion', ''),
            chash,
            today, today,
            frag.get('confidence', 0.5),
        )
    )


def detect_skill_candidates() -> List[Dict]:
    """检测 skill 候选：反复出现的操作模式。

    检测逻辑：
    1. 同一 theme 下，category=rule 的碎片 >= 3 条
    2. 含有"每次""必须""记得""别忘了"等关键词的 rule 碎片 >= 2 条

    Returns:
        候选列表 [{"theme", "content", "count"}, ...]
    """
    if not os.path.exists(DB_PATH):
        return []

    conn = sqlite3.connect(DB_PATH, timeout=10)
    candidates = []

    # 检测1：同主题下 rule 碎片 >= 3
    rows = conn.execute("""
        SELECT theme, content, COUNT(*) as cnt
        FROM dreams
        WHERE category = 'rule' AND status = 'candidate'
          AND theme != '' AND theme != 'daily' AND theme != '其他'
        GROUP BY theme
        HAVING cnt >= 3
    """).fetchall()
    for theme, content, cnt in rows:
        candidates.append({"theme": theme, "content": content, "count": cnt, "reason": "theme_rule_cluster"})

    # 检测2：含操作模式关键词的 rule 碎片
    pattern_keywords = ['每次', '必须', '记得', '别忘了', '记住', '不要忘', '一定']
    kw_placeholders = " OR ".join("content LIKE ?" for _ in pattern_keywords)
    kw_params = [f"%{kw}%" for kw in pattern_keywords]
    rows2 = conn.execute(f"""
        SELECT theme, content, COUNT(*) as cnt
        FROM dreams
        WHERE category = 'rule' AND status = 'candidate'
          AND ({kw_placeholders})
        GROUP BY theme
        HAVING cnt >= 2
    """, kw_params).fetchall()
    for theme, content, cnt in rows2:
        # 避免重复
        if not any(c['theme'] == theme and c['reason'] == 'theme_rule_cluster' for c in candidates):
            candidates.append({"theme": theme, "content": content, "count": cnt, "reason": "pattern_keyword"})

    # 标记到数据库
    for c in candidates:
        conn.execute("""
            UPDATE dreams SET skill_candidate = 1
            WHERE theme = ? AND category = 'rule'
        """, (c['theme'],))

    conn.commit()
    conn.close()

    return candidates


def archive_stale_entries(days: int = 30) -> int:
    """淘汰过期的 candidate 碎片。

    规则：mention_count == 1 且距 last_seen > days 天 → archived

    Returns:
        归档的条目数
    """
    if not os.path.exists(DB_PATH):
        return 0

    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    conn = sqlite3.connect(DB_PATH, timeout=10)
    cursor = conn.execute("""
        UPDATE dreams SET status = 'archived'
        WHERE status = 'candidate'
          AND mention_count <= 1
          AND last_seen < ?
    """, (cutoff,))
    archived = cursor.rowcount
    conn.commit()
    conn.close()

    return archived




# ========== 跨天碎片合并 ==========


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def _days_between(date_str1: str, date_str2: str) -> int:
    """计算两个日期字符串之间的天数差"""
    try:
        d1 = datetime.strptime(date_str1[:10], '%Y-%m-%d')
        d2 = datetime.strptime(date_str2[:10], '%Y-%m-%d')
        return abs((d2 - d1).days)
    except (ValueError, TypeError):
        return 0


def _is_user_related(content: str) -> bool:
    """判断内容是否与用户个人信息直接相关"""
    personal_kws = ['用户', '生日', '地址', '工作', '姓名', '年龄', '老家',
                    '电话', '邮箱', '喜欢', '不喜欢', '偏好', '爱好', '习惯']
    content_lower = content.lower()
    hits = sum(1 for kw in personal_kws if kw in content_lower)
    return hits >= 1


def consolidate_fragments(new_fragments: List[Dict]) -> List[Dict]:
    """跨天碎片合并：同 category + 共享关键实体的碎片合并为一条。

    策略：提取关键实体（3-6字词组），找数据库中同 category 且共享实体的碎片。
    合并时保留更长更完整的版本，累加 mention_count。
    """
    if not new_fragments or not os.path.exists(DB_PATH):
        return new_fragments

    conn = sqlite3.connect(DB_PATH, timeout=10)
    result = []
    merged_hashes = set()

    for frag in new_fragments:
        chash = frag.get('content_hash', _content_hash(frag['content']))
        frag['content_hash'] = chash

        # 精确匹配：内容完全相同或已在本轮被合并过，跳过
        if chash in merged_hashes:
            continue
        exact = conn.execute(
            "SELECT content_hash FROM dreams WHERE content = ?", (frag['content'],)
        ).fetchone()
        if exact:
            merged_hashes.add(exact[0])
            continue

        entities = _extract_entities(frag['content'])
        if not entities:
            result.append(frag)
            continue

        category = frag['category']
        db_rows = conn.execute("""
            SELECT content_hash, content, mention_count, confidence, emotion,
                   date, first_seen, last_seen, theme
            FROM dreams
            WHERE category = ? AND status IN ('candidate', 'confirmed')
              AND content_hash != ?
            ORDER BY last_seen DESC
            LIMIT 30
        """, (category, chash)).fetchall()

        best_match = None
        best_overlap = 0

        for row in db_rows:
            db_hash, db_content, db_mention, db_conf, db_emotion, \
                db_date, db_first, db_last, db_theme = row

            db_entities = _extract_entities(db_content)
            overlap = len(entities & db_entities)

            if overlap >= 1 and overlap > best_overlap:
                if _topic_coherent(frag['content'], db_content, entities & db_entities):
                    best_overlap = overlap
                    best_match = row

        if best_match:
            db_hash, db_content, db_mention, db_conf, db_emotion, \
                db_date, db_first, db_last, db_theme = best_match

            merged_content = frag['content'] if len(frag['content']) >= len(db_content) else db_content
            merged_conf = max(frag.get('confidence', 0.5), db_conf or 0.5)
            new_mention = (db_mention or 1) + 1

            conn.execute("""
                UPDATE dreams
                SET content = ?, mention_count = ?, confidence = ?,
                    last_seen = ?, emotion = ?
                WHERE content_hash = ?
            """, (merged_content, new_mention, merged_conf,
                  frag.get('date', datetime.now().strftime('%Y-%m-%d')),
                  frag.get('emotion', db_emotion or ''),
                  db_hash))

            merged_frag = {
                'category': category,
                'content': merged_content,
                'emotion': frag.get('emotion', db_emotion or ''),
                'confidence': merged_conf,
                'content_hash': db_hash,
                'date': frag.get('date', db_date),
                'theme': frag.get('theme', db_theme or ''),
                'mention_count': new_mention,
                'merged': True,
                'merged_with': db_hash,
                'shared_entities': list(entities & _extract_entities(db_content)),
            }
            result.append(merged_frag)
            merged_hashes.add(db_hash)
            merged_hashes.add(chash)
        else:
            result.append(frag)

    conn.commit()
    conn.close()
    return result


def _extract_entities(text: str) -> set:
    """提取3字中文实体（位置n-gram）+ 英文实体。

    纯3字滑动窗口确保overlap稳定可预测：
    - 同话题碎片几乎必然有共享3字窗口（如"某展会"→"展会"）
    - 不相关碎片几乎不会有共享（中文3字随机碰撞率极低）
    """
    import re as _re
    entities = set()

    # 3字中文位置 n-gram
    zh_segments = _re.findall(r'[\u4e00-\u9fff]+', text)
    for seg in zh_segments:
        for i in range(len(seg) - 2):
            entities.add(seg[i:i+3])

    # 过滤包含高频噪声词的
    noise = {'用户', '不要', '不要忘记', '不要忘了', '可以了', '好了', '没问题', '知道了', '收到', '嗯', '好的'
    filtered = set()
    for c in entities:
        if not any(n in c for n in noise):
            filtered.add(c)

    # 英文实体
    en_entities = set(_re.findall(r'[A-Za-z][A-Za-z0-9_-]{2,}', text))
    filtered.update(en_entities)

    return filtered


def _topic_coherent(text1: str, text2: str, shared_entities: set) -> bool:
    """判断两条文本在共享实体下是否话题一致。

    阈值逻辑：要求共享实体的字符数占两条文本的有效长度比例足够大。
    - 基准阈值：两条都必须 >= 0.20
    - 特例：短文本(<=15字)放宽到 >= 0.15（短文本信息密度高）
    - 必须共享至少2个实体（避免单个3字碰撞误判）
    """
    if not shared_entities or len(shared_entities) < 2:
        return False
    shared_chars = sum(len(e) for e in shared_entities)
    eff1 = max(1, len(text1) - 5)
    eff2 = max(1, len(text2) - 5)
    ratio1 = shared_chars / eff1
    ratio2 = shared_chars / eff2
    # 基准阈值
    base = 0.20
    # 短文本放宽
    if len(text1) <= 15:
        base = 0.15
    if len(text2) <= 15:
        base = 0.15
    return ratio1 >= base and ratio2 >= base
