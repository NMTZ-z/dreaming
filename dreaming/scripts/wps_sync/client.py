import sys
import os

_client = None

def get_wps_client():
    """获取WPS笔记MCP客户端（单例）。"""
    global _client
    if _client is not None:
        return _client

    skill_path = r'./skills'
    cherry_scripts = os.path.join(skill_path, 'cherry-diary', 'scripts')
    if cherry_scripts not in sys.path:
        sys.path.insert(0, cherry_scripts)

    from ainote_mcp import AinoteMCPClient
    _client = AinoteMCPClient()
    _client.initialize()
    return _client


def get_note_link(note_id: str) -> str:
    """生成笔记链接。"""
    return f"https://www.kdocs.cn/l/{note_id}"


def get_note_outline(note_id: str) -> list:
    """获取笔记大纲。"""
    return get_wps_client().get_note_outline(note_id)


def get_first_block_id(note_id: str) -> str:
    """获取笔记第一个block ID。"""
    return get_wps_client().get_first_block_id(note_id)


def edit_block(note_id: str, block_id: str, op: str, content: str):
    """编辑block（replace/insert_after/insert_before）。"""
    return get_wps_client().edit_block(note_id, block_id, op, content)


def replace_first_block(note_id: str, content: str):
    """替换第一个block。"""
    return get_wps_client().replace_first_block(note_id, content)