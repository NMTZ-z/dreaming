import json
import os
from datetime import datetime

from .parser import parse_chat_files
from .cluster import cluster_by_theme, extract_keywords
from .extract import extract_fragments, add_hashes, deduplicate
from .storage import init_db, get_existing_hashes, save_fragments, generate_dreams_md, write_dreams_md
from .sleep import recall_check, promote_fragments, detect_skill_candidates, archive_stale_entries, consolidate_fragments
from .memory_writer import insert_fragments, get_token_estimate

MEMORY_DIR = r"./memory"
STATE_FILE = os.path.join(MEMORY_DIR, "dreaming_state.json")
SKILL_CANDIDATES_FILE = os.path.join(MEMORY_DIR, "skill_candidates.json")
LOOKBACK_DAYS = 7


def run_dreaming(llm_call=None, memory_dir: str = None) -> dict:
    """执行 Dreaming V4 完整流程（三阶段 + skill 检测）。

    函数签名与 V3 完全兼容，定时任务无需修改。

    Args:
        llm_call: callable(prompt) -> str，LLM调用函数。
                  若为None则返回prompt供Agent执行（定时任务模式）。
        memory_dir: 记忆目录（默认使用MEMORY_DIR）。

    Returns:
        如果llm_call为None: 返回 {"mode": "need_llm", "prompts": [...]}
        否则: 返回 {"mode": "done", "stats": {...}}
    """
    global MEMORY_DIR
    if memory_dir:
        MEMORY_DIR = memory_dir

    t0 = datetime.now()
    today = datetime.now().strftime('%Y-%m-%d')

    # 0. 初始化（含 schema 迁移）
    init_db()
    since_date = _load_state()

    # ========== Phase 1: 浅睡（Light Sleep）— 解析 + 聚类 ==========
    entries = parse_chat_files(since_date)
    if not entries:
        # 即使没有新条目，也执行归档检查
        archived = archive_stale_entries()
        stats = {
            "status": "no_new_entries",
            "since_date": since_date,
            "entries_parsed": 0,
            "archived": archived,
        }
        if archived > 0:
            # 归档后重新生成 DREAMS.md
            dreams_md = generate_dreams_md()
            write_dreams_md(dreams_md)
        return {"mode": "done", "stats": stats}

    # 2. 梳理：主题聚类
    clusters = cluster_by_theme(entries)

    # 2.5 recall_check：统计每个主题的历史提及次数
    for cluster in clusters:
        theme = cluster['theme']
        kws = extract_keywords(theme)
        hits = recall_check(theme, '', kws)
        cluster['recall_hits'] = hits

    # 3. 获取已有hash，去重
    existing_hashes = get_existing_hashes()

    # ========== Phase 2: 梳理（REM）— LLM 提取 ==========
    if llm_call is None:
        prompts = _build_prompts(clusters)
        return {"mode": "need_llm", "prompts": prompts,
                "stats": {"entries_parsed": len(entries), "clusters": len(clusters)}}

    # 直接执行模式：LLM 提取碎片
    all_fragments = []
    for cluster in clusters:
        theme = cluster['theme']
        entries_list = cluster['entries']
        recall_hits = cluster.get('recall_hits', 0)

        fragments = extract_fragments(theme, entries_list, llm_call)
        fragments = add_hashes(fragments)
        fragments = deduplicate(fragments, existing_hashes)

        if fragments:
            # 给每个碎片附上 theme 和 recall 信息
            # 信任 LLM 提炼的日期归属（规则6）：碎片内容中的日期就是事件日期
            for f in fragments:
                f['date'] = _resolve_event_date(f['content'], today)
                f['theme'] = theme
                f['recall_hits'] = recall_hits

            all_fragments.extend(fragments)

    # ========== Phase 2.5: 碎片合并（跨天同类碎片去重增强） ==========
    if all_fragments:
        all_fragments = consolidate_fragments(all_fragments)
        consolidated_count = sum(1 for f in all_fragments if f.get('merged'))
    else:
        consolidated_count = 0

    # ========== Phase 2.6: 写入 DB（在 consolidate 之后，避免 promote 重复写入） ==========
    for f in all_fragments:
        if not f.get('merged', False):
            # 只写入非合并的碎片（合并碎片已在 consolidate 中更新 DB）
            save_fragments([f], f.get('theme', 'daily'))
    existing_hashes.update(f['content_hash'] for f in all_fragments)

    # ========== Phase 3: 深睡（Deep Sleep）— 时间验证 + 沉淀 ==========
    promoted = []
    to_memory = []
    to_user = []
    skill_candidates = []

    if all_fragments:
        # 3.1 评估碎片状态，决定是否晋升
        sleep_result = promote_fragments(all_fragments, today)
        promoted = sleep_result['promoted']
        to_memory = sleep_result['to_memory']
        to_user = sleep_result['to_user']

        # 3.2 将晋升的碎片写入 MEMORY.md
        if to_memory:
            mem_result = insert_fragments(to_memory)
            # 记录写入结果

        # 3.3 同步到云端 user（仅标记，由 Agent 决定是否实际执行）
        # to_user 列表在 stats 中返回，Agent 可以在 prompt 中处理

    # ========== Phase 3.5: Skill 候选检测 ==========
    skill_candidates = detect_skill_candidates()
    if skill_candidates:
        _save_skill_candidates(skill_candidates)

    # ========== 清理：归档过期碎片 ==========
    archived = archive_stale_entries()

    # ========== 生成 DREAMS.md ==========
    dreams_md_content = generate_dreams_md()
    write_dreams_md(dreams_md_content)

    # ========== WPS 笔记同步 ==========
    wps_synced = False
    note_link = ""
    if all_fragments:
        try:
            from ..wps_sync.sync import update_dreams_note
            # 按碎片实际日期分组写入，而非统一用 today
            from collections import defaultdict
            by_date = defaultdict(list)
            for frag in all_fragments:
                frag_date = frag.get('date', today)
                by_date[frag_date].append(frag)
            for frag_date, date_frags in sorted(by_date.items(), reverse=True):
                result = update_dreams_note(date_frags, date=frag_date)
                if not wps_synced:
                    wps_synced = result.get("success", False)
                    note_link = result.get("note_link", "")
        except Exception as e:
            wps_synced = False

    # ========== 更新状态 ==========
    _save_state(today)

    duration = (datetime.now() - t0).total_seconds()
    token_info = get_token_estimate()

    return {"mode": "done", "stats": {
        "status": "success",
        "entries_parsed": len(entries),
        "clusters": len(clusters),
        "fragments_extracted": len(all_fragments),
        "fragments_promoted": len(promoted),
        "fragments_to_memory": len(to_memory),
        "fragments_to_user": len(to_user),
        "fragments_consolidated": consolidated_count,
        "skill_candidates": len(skill_candidates),
        "archived": archived,
        "wps_synced": wps_synced,
        "note_link": note_link,
        "memory_tokens": token_info.get("tokens", 0),
        "memory_usage": round(token_info.get("usage", 0) * 100, 1),
        "duration_seconds": round(duration, 1),
        "since_date": since_date,
    }}


def _build_prompts(clusters: list[dict]) -> list[dict]:
    """为定时任务模式构建LLM调用prompt列表。"""
    prompts = []
    for cluster in clusters:
        entries_text = '\n'.join(f"- [{e['date']}] {e['content']}" for e in cluster['entries'])
        from .extract import EXTRACT_PROMPT
        prompt = EXTRACT_PROMPT.format(theme=cluster['theme'], entries=entries_text)
        prompts.append({
            "theme": cluster['theme'],
            "entry_count": len(cluster['entries']),
            "recall_hits": cluster.get('recall_hits', 0),
            "prompt": prompt,
        })
    return prompts


def _load_state() -> str:
    """加载上次处理日期，返回开始扫描的日期（至少从 7 天前开始）。"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
            last = state.get('last_processed', '')
            if last:
                return last
        except (json.JSONDecodeError, KeyError):
            pass
    # state 不存在或损坏时，兜底从 LOOKBACK_DAYS 天前开始，避免重扫全部历史
    from datetime import timedelta
    return (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime('%Y-%m-%d')


def _save_state(date: str):
    """保存处理状态。"""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    state = {
        "last_processed": date,
        "last_run": datetime.now().isoformat(),
        "version": "4.0",
    }
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)




def _resolve_event_date(content: str, fallback: str) -> str:
    """从碎片内容中解析事件日期。

    规则：
    1. 匹配「X月Y日」→ 取最后一个匹配 → 转为 YYYY-MM-DD
    2. 匹配 YYYY-MM-DD 格式 → 直接返回
    3. 兜底返回 fallback（当天日期）
    """
    import re
    from datetime import datetime

    # 优先匹配「X月Y日」
    md_matches = re.findall(r'(\d{1,2})月(\d{1,2})日', content)
    if md_matches:
        month, day = md_matches[-1]
        year = datetime.now().year
        try:
            return f"{year}-{int(month):02d}-{int(day):02d}"
        except (ValueError, OverflowError):
            pass

    # 匹配 YYYY-MM-DD
    iso_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', content)
    if iso_match:
        return iso_match.group(0)

    # 兜底
    return fallback

def _save_skill_candidates(candidates: list[dict]):
    """保存 skill 候选列表。"""
    os.makedirs(os.path.dirname(SKILL_CANDIDATES_FILE), exist_ok=True)
    with open(SKILL_CANDIDATES_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            "updated_at": datetime.now().isoformat(),
            "candidates": candidates,
        }, f, ensure_ascii=False, indent=2)
