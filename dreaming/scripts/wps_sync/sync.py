import re
import json
import time
from .client import get_wps_client, get_note_link, get_first_block_id

NOTE_ID = "514473671668"
_STATE_FILE = r"./memory\dreaming_note_id.json"
WPS_TAG = '<tag id="<YOUR_TAG_ID>">#<YOUR_DREAM_TAG></tag>'

CATEGORY_ORDER = ["fact", "preference", "emotion", "rule", "quote"]
CATEGORY_EMOJI = {
    "fact": "📌", "preference": "💗", "emotion": "💕",
    "rule": "📏", "quote": "💬",
}
CATEGORY_NAME = {
    "fact": "事实", "preference": "偏好", "emotion": "情感",
    "rule": "规矩", "quote": "金句",
}


def _load_current_note_id() -> str:
    import os
    if os.path.exists(_STATE_FILE):
        try:
            with open(_STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f).get("note_id", NOTE_ID)
        except Exception:
            pass
    return NOTE_ID


def _save_current_note_id(note_id: str):
    import os
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    with open(_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump({"note_id": note_id}, f, ensure_ascii=False)


def _get_outline(client, note_id: str) -> list[dict]:
    """获取笔记outline列表。"""
    outline = client.get_note_outline(note_id)
    if isinstance(outline, list):
        return outline
    if isinstance(outline, dict):
        return outline.get('blocks', outline.get('content', []))
    return []


def _get_block_id(block: dict) -> str:
    return block.get("block_id") or block.get("id", "")


def _get_preview(block: dict) -> str:
    return block.get("preview", block.get("text", ""))


def _insert_block_after(client, note_id: str, anchor_id: str, content: str) -> str:
    """在anchor_id之后插入一个block，返回last_block_id。"""
    result = client._call("edit_block", {
        "note_id": note_id,
        "block_id": anchor_id,
        "op": "insert",
        "content": content,
        "anchor_id": anchor_id,
        "position": "after",
    })
    data = json.loads(result['content'][0]['text'])
    return data.get('last_block_id', anchor_id)


def _find_date_section(client, note_id: str, date: str) -> dict | None:
    """在outline中查找指定日期的区块，返回结构化信息。

    搜索范围：从第一个 hr 开始到笔记末尾，不依赖 tag block。

    Returns:
        {"date_block_idx", "date_block_id", "end_block_idx", "end_block_id", "existing_categories"}
        或 None
    """
    outline = _get_outline(client, note_id)

    # 找到日期区域的起始位置（第一个 hr 之后）
    start_idx = 0
    for i, b in enumerate(outline):
        if b.get("type") == "horizontal_rule":
            start_idx = i + 1
            break

    # 从后往前找匹配的独立日期行
    date_block_idx = None
    for i in range(len(outline) - 1, start_idx - 1, -1):
        preview = _get_preview(b := outline[i]).strip()
        clean = re.sub(r'<[^>]+>', '', preview).strip()
        if clean == date:
            date_block_idx = i
            break

    if date_block_idx is None:
        return None

    date_block_id = _get_block_id(outline[date_block_idx])

    # 找区块末尾：下一个独立日期行或笔记末尾
    end_idx = date_block_idx
    existing_categories = set()
    for i in range(date_block_idx + 1, len(outline)):
        preview = _get_preview(outline[i])
        clean = re.sub(r'<[^>]+>', '', preview).strip()
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', clean):
            break
        end_idx = i
        for cat, name in CATEGORY_NAME.items():
            if name in preview:
                existing_categories.add(cat)

    return {
        "date_block_idx": date_block_idx,
        "date_block_id": date_block_id,
        "end_block_idx": end_idx,
        "end_block_id": _get_block_id(outline[end_idx]),
        "existing_categories": existing_categories,
    }


def _find_insert_anchor(client, note_id: str) -> str:
    """找到新日期区块的插入锚点：第一个 hr 之前的 block（通常是说明文字）。

    笔记结构：标题 → 说明 → [hr → 日期 → 内容 → ...] → tag
    新日期区块应插在最前面的 hr 之前，即说明文字之后。
    """
    outline = _get_outline(client, note_id)
    for i in range(len(outline)):
        if outline[i].get("type") == "horizontal_rule":
            if i > 0:
                return _get_block_id(outline[i - 1])
    # 没有hr，用最后一个block
    return _get_block_id(outline[-1])


def _escape(text: str) -> str:
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    return text


def _md_to_xml(text: str) -> str:
    text = _escape(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    return text


def update_dreams_note(fragments: list[dict], date: str = None, note_id: str = None) -> dict:
    """向梦境笔记追加新的记忆碎片。

    智能逻辑：
    - 如果日期区块已存在 → 往该区块末尾追加新碎片
    - 如果日期区块不存在 → 在第一个hr之前创建新区块

    Args:
        fragments: [{"category": "fact", "content": "...", "emotion": "开心"}, ...]
        date: 日期字符串（默认今天）
        note_id: 笔记ID
    """
    from datetime import datetime
    if not date:
        date = datetime.now().strftime('%Y-%m-%d')

    # 防御：空碎片列表不执行任何写入
    if not fragments:
        return {
            "success": True,
            "note_link": get_note_link(note_id or _load_current_note_id()),
            "message": f"无碎片需要写入（{date}）",
            "note_id": note_id or _load_current_note_id(),
        }

    client = get_wps_client()
    nid = note_id or _load_current_note_id()

    try:
        # 按类别分组
        grouped = {}
        for f in fragments:
            cat = f.get("category", "fact")
            grouped.setdefault(cat, []).append(f)

        # 检查日期区块是否已存在
        section = _find_date_section(client, nid, date)

        if section:
            # === 日期已存在：往区块末尾追加 ===
            anchor_id = section["end_block_id"]
            existing_cats = section["existing_categories"]
            last_category = None

            # 找当前区块最后一个类别
            outline = _get_outline(client, nid)
            for i in range(section["date_block_idx"], section["end_block_idx"] + 1):
                preview = _get_preview(outline[i])
                for cat, name in CATEGORY_NAME.items():
                    if name in preview:
                        last_category = cat

            for cat in CATEGORY_ORDER:
                if cat not in grouped:
                    continue

                emoji = CATEGORY_EMOJI.get(cat, "📌")
                name = CATEGORY_NAME.get(cat, cat)

                if cat not in existing_cats:
                    # 新类别：需要插入类别标题
                    anchor_id = _insert_block_after(client, nid, anchor_id,
                        f'<p><emoji value="{emoji}" type="base"/><strong> {name}</strong></p>')
                    time.sleep(0.1)
                    existing_cats.add(cat)

                # 插入该类别的碎片
                for f in grouped[cat]:
                    text = _md_to_xml(f.get("content", ""))
                    emotion = f.get("emotion", "")
                    if emotion:
                        text += f'（{_escape(emotion)}）'
                    anchor_id = _insert_block_after(client, nid, anchor_id, f'<p>  • {text}</p>')
                    time.sleep(0.1)

            msg = f"已追加 {len(fragments)} 条碎片到 {date} 区块"
        else:
            # === 日期不存在：在tag前创建新区块 ===
            anchor_id = _find_insert_anchor(client, nid)

            # hr
            anchor_id = _insert_block_after(client, nid, anchor_id, "<hr/>")
            time.sleep(0.1)

            # 日期标题
            anchor_id = _insert_block_after(client, nid, anchor_id,
                f'<p><strong>{_escape(date)}</strong></p>')
            time.sleep(0.1)

            # 按类别
            for cat in CATEGORY_ORDER:
                if cat not in grouped:
                    continue
                emoji = CATEGORY_EMOJI.get(cat, "📌")
                name = CATEGORY_NAME.get(cat, cat)

                anchor_id = _insert_block_after(client, nid, anchor_id,
                    f'<p><emoji value="{emoji}" type="base"/><strong> {name}</strong></p>')
                time.sleep(0.1)

                for f in grouped[cat]:
                    text = _md_to_xml(f.get("content", ""))
                    emotion = f.get("emotion", "")
                    if emotion:
                        text += f'（{_escape(emotion)}）'
                    anchor_id = _insert_block_after(client, nid, anchor_id, f'<p>  • {text}</p>')
                    time.sleep(0.1)

            msg = f"已创建 {date} 区块并插入 {len(fragments)} 条碎片"

        return {
            "success": True,
            "note_link": get_note_link(nid),
            "message": msg,
            "note_id": nid,
        }
    except Exception as e:
        return {"success": False, "note_link": "", "message": f"插入失败: {e}"}


def sync_dreams_to_wps(md_content: str = None, note_id: str = None) -> dict:
    """确认梦境笔记可用。

    仅检查笔记存在性，不做全量写入。
    """
    client = get_wps_client()
    nid = note_id or _load_current_note_id()

    try:
        client.read_note_content(nid)
        _save_current_note_id(nid)
        return {
            "success": True,
            "note_link": get_note_link(nid),
            "message": f"笔记可用（{nid}）",
            "note_id": nid,
        }
    except Exception:
        return {"success": False, "note_link": "", "message": "笔记不存在"}
