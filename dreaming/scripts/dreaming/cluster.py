import re
from collections import Counter


# 手动定义的角色词（不作为主题关键词）
_ROLE_WORDS = {
    '用户', 'AI', 'Agent',
    '主人', '哥哥', '捏', '嘛', '啦', '呀', '呢', '吧',
}

# 停用词
_STOP_WORDS = {
    '的', '了', '在', '是', '我', '你', '他', '她', '它', '这', '那',
    '有', '和', '与', '也', '都', '就', '不', '很', '被', '把', '给',
    '要', '会', '能', '可以', '没有', '不是', '什么', '怎么', '为什么',
    '一个', '一', '个', '上', '下', '中', '去', '来', '说', '看', '想',
    '到', '从', '对', '让', '还', '又', '只', '已', '但', '而', '或',
    '如果', '因为', '所以', '然后', '但是', '不过', '虽然', '而且',
    '比较', '非常', '真的', '感觉', '应该', '可能', '需要', '知道',
    '今天', '昨天', '明天', '现在', '时候', '问题', '东西', '事情',
    '做法', '部分', '相关', '进行', '使用', '通过', '关于', '之后',
    '之前', '以及', '或者', '还是', '已经', '正在', '自己', '其他',
    '第一', '第二', '第三', '里面', '时候', '这样', '那样', '',
    
    # 技术日志常见词
    'API', 'json', 'block', 'sync', 'prompt', 'skill', 'Skill', 'SKILL',
    'insert', 'delete', 'replace', 'anchor', 'note_id', 'block_id',
    'client', 'module', 'script', 'function', 'return', 'result',
    'success', 'failed', 'error', 'debug', 'test', 'print',
    'update', 'create', 'build', 'check', 'fix', 'patch',
    'True', 'False', 'None', 'str', 'dict', 'list',
}

# 中文停用词正则：去掉单字（保留有意义的双字以上）
_SINGLE_CHAR = re.compile(r'^[\u4e00-\u9fff]$')


def extract_keywords(text: str) -> list[str]:
    """从文本中提取关键词（2-4字词组）。"""
    # 提取中文词组（2-4字）
    zh_words = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
    # 提取英文单词
    en_words = re.findall(r'[A-Za-z][A-Za-z0-9_-]{2,}', text)

    candidates = []
    for w in zh_words + en_words:
        if w in _STOP_WORDS or w in _ROLE_WORDS:
            continue
        if _SINGLE_CHAR.match(w):
            continue
        candidates.append(w)

    return candidates


def cluster_by_theme(entries: list[dict]) -> list[dict]:
    """将条目按关键词主题聚类。

    Args:
        entries: [{"date": str, "content": str, "source": str}, ...]

    Returns:
        [{"theme": str, "entries": [entry, ...]}, ...]
        主题按条目数降序，单条归入"其他"。
    """
    if not entries:
        return []

    # 为每个条目提取关键词
    entry_keywords = []
    for entry in entries:
        kws = extract_keywords(entry['content'])
        entry_keywords.append((entry, kws))

    # 全局关键词频率
    global_freq = Counter()
    for _, kws in entry_keywords:
        for kw in set(kws):
            global_freq[kw] += 1

    # 每个条目选择频率最高的关键词作为主题
    theme_map: dict[str, list[dict]] = {}
    unassigned: list[dict] = []

    for entry, kws in entry_keywords:
        if not kws:
            unassigned.append(entry)
            continue

        # 选频率最高的关键词
        best_kw = max(kws, key=lambda w: global_freq[w])
        if best_kw not in theme_map:
            theme_map[best_kw] = []
        theme_map[best_kw].append(entry)

    # 整理：条目数>=2的独立成主题，单条归入"其他"
    clusters = []
    for theme, items in sorted(theme_map.items(), key=lambda x: -len(x[1])):
        if len(items) >= 2:
            clusters.append({"theme": theme, "entries": items})
        else:
            unassigned.extend(items)

    if unassigned:
        clusters.append({"theme": "其他", "entries": unassigned})

    return clusters


def format_clusters_for_llm(clusters: list[dict]) -> str:
    """将聚类结果格式化为LLM输入文本。"""
    parts = []
    for c in clusters:
        parts.append(f"## 主题：{c['theme']}")
        for e in c['entries']:
            parts.append(f"- [{e['date']}] {e['content']}")
        parts.append("")
    return '\n'.join(parts)