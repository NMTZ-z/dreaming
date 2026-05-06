import re
import os
import hashlib
from datetime import datetime

MEMORY_DIR = r"./memory"
CHAT_DIR = os.path.join(MEMORY_DIR, "chat")

_SKIP_PATTERNS = [
    re.compile(r'^#{1,4}\s'),
    re.compile(r'^\s*$'),
    re.compile(r'^---+$'),
    re.compile(r'^>\s'),
    re.compile(r'^```'),
]

_GREETINGS = frozenset({
    '早安', '晚安', '午安', '早上好', '下午好', '晚上好',
    '好的', '谢谢', '嗯', '哦', '好', '行', '对', '是', 'OK', 'ok',
})

# 设计原则（借鉴Claude Code"不存代码"）：只保留关于"人"的知识，过滤工具/技术噪音
_NOISE_PATTERNS = [
    # 工具调用记录
    re.compile(r'(?:调用|执行|完成|生成|创建|读取|写入|上传|下载|安装|启动|运行|触发)\s*(?:了|完|到)?(?:.*?)(?:流程|脚本|API|工具|任务|代码|模块|文件)'),
    re.compile(r'(?:jupyter_cell_exec|start_write_file|end_write_file|generate_image|pip_install|search|timer_task|get_memory|edit_memory|write_memory)\s'),
    # 状态报告（✅/❌ + 简短描述）
    re.compile(r'^.{0,30}(?:✅|❌|✓|✗|✔|✘)\s'),
    # "xxx：成功/失败/已xxx"
    re.compile(r'.{0,20}[:：]\s*(?:成功|失败|完成|已更新|已创建|已同步|已修复|已补写)'),
    # 技术参数/配置记录
    re.compile(r'(?:笔记|文件|目录|路径|链接|ID|大小)[:：]\s'),
    re.compile(r'(?:正确|错误)?参数[:：]'),
    # 技术操作记录
    re.compile(r'^使用了?(?:的|了)?(?:正确|新的)?(?:SKILL|基准图|diary|insert_image|edit_block|replace)'),
    re.compile(r'^按照.{0,30}(?:格式|要求|规范)'),
    # 技术文件名
    re.compile(r'(?:fetch_data|noiz_tts|ainote_mcp|diary_prompt|kdocs\.upload|insert_image|edit_block|replace_first_block|block_id|anchor_id)\b'),
    re.compile(r'(?:SKILL|skill|Skill)\.md'),
    re.compile(r'(?:cherry-diary)\b'),
    # "集成/修改/更新/添加/创建了xxx"
    re.compile(r'^(?:集成|修改|更新|添加|创建|新增|清空|写了?|补写|测试|检查|确认|捕获|清理了?)\s'),
    re.compile(r'^更新了?(?:云端|本地|WPS)?(?:user|memory|MEMORY|笔记)'),
    re.compile(r'^已更新(?:本地|云端)'),
    re.compile(r'^日记(?:生成|写入|补写|ID|链接)'),
    re.compile(r'^包含了?(?:完整的|今天的|昨天的)?(?:日记|聊天记录|记忆)'),
    re.compile(r'^目的[:：]'),
    re.compile(r'^(?:记忆根目录|聊天记录目录|昨天的记忆文件)'),
    re.compile(r'^记录了?(?:用户)?本周'),
    re.compile(r'^每次对话(?:前|中|后)必须'),
    re.compile(r'^日记生成完成'),
    re.compile(r'执行准则'),
    re.compile(r'^(?:改为|改为：)'),
    re.compile(r'^\[(?:约|时间不详)\]'),
    # URL
    re.compile(r'https?://\S{10,}'),
    # 文件路径
    re.compile(r'[A-Z]:\\[\\]+\.\w{2,4}'),
    # 技术调试对话（AI和用户讨论代码/bug时的噪音行）
    re.compile(r'^[✓✗❌✔✘⬆↓→]'),  # 纯符号开头的状态行
    re.compile(r'(?:语法|py_compile|import|from\s+)\s*(?:OK|成功|失败|错误)'),
    re.compile(r'^\d+\s+(?:个|条|个block|条碎片|次)'),
    re.compile(r'^清理(?:完成|重复)'),
    re.compile(r'^(?:block数|字数|字符|行数|大小)[:：]\s*\d'),
]


def parse_chat_files(since_date: str) -> list:
    """扫描chat目录，返回since_date之后的条目列表。"""
    entries = []
    if not os.path.isdir(CHAT_DIR):
        return entries

    for fname in sorted(os.listdir(CHAT_DIR)):
        if not fname.endswith('.md'):
            continue
        stem = fname[:-3]
        try:
            file_date = datetime.strptime(stem, '%Y-%m-%d').strftime('%Y-%m-%d')
        except ValueError:
            continue
        if file_date < since_date:
            continue

        with open(os.path.join(CHAT_DIR, fname), 'r', encoding='utf-8') as f:
            for line in f:
                entry = _parse_line(line, file_date)
                if entry:
                    entries.append(entry)
    return entries


def _parse_line(line: str, fallback_date: str) -> dict | None:
    line = line.strip()

    for pat in _SKIP_PATTERNS:
        if pat.match(line):
            return None

    ts_match = re.match(r'^-?\s*\[(\d{1,2}:\d{2})\]\s*(.*)', line)
    if ts_match:
        text = ts_match.group(2).strip()
    elif line.startswith('- ') or line.startswith('* '):
        text = line.lstrip('- *').strip()
    else:
        return None

    if not text or len(text) < 5:
        return None

    if _is_noise(text):
        return None

    return {"date": fallback_date, "content": text}


def _is_noise(text: str) -> bool:
    cleaned = text.rstrip('！!。.～~')
    if cleaned in _GREETINGS:
        return True
    for pat in _NOISE_PATTERNS:
        if pat.search(text):
            return True
    return False


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]