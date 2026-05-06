# MEMORY.md 精准插入模块
# 负责将 confirmed 碎片写入 MEMORY.md 的对应 section

import os
import re
import shutil
from datetime import datetime
from typing import Dict, List, Optional, Tuple


MEMORY_FILE = r"./memory\MEMORY.md"
BACKUP_FILE = r"./memory\MEMORY.md.bak"
MAX_TOKENS = 12000  # 硬上限，粗略按 1中文字=1.5token 估算
MAX_CHARS = int(MAX_TOKENS / 1.5)


# 模块路径 → L2标题 关键词
MODULE_KEYWORDS = {
    'module1': ['模块一', 'Work', 'Tech', '硬核工作', '技术库'],
    'module2': ['模块二', 'Lifestyle', 'Aesthetics', '生活颗粒度', '审美'],
    'module3': ['模块三', 'Lore', 'Timeline', '虚拟世界', '编年史'],
}

# 分类 → 沉淀目标 (模块, L3标题关键词)
CATEGORY_TARGETS = {
    'fact': [
        # 工作关键词 → 模块一 > 近期工作动态
        ('module1', '近期工作动态', ['工作', '项目', '会议', '论文', '课题', '科研', 'CSP', '储能', '出差', '太原']),
        # 生活/身份关键词 → 模块一 > 基本信息
        ('module1', '基本信息', ['生日', '年龄', '星座', '老家', '住址', '搬家', '毕业', '学历']),
        # 其他事实 → 模块二对应子节
        ('module2', None, None),  # None 表示需要二次匹配
    ],
    'preference': [
        # 饮食 → 模块二 > 饮食偏好
        ('module2', '饮食偏好', ['吃', '喝', '咖啡', '奶茶', '辣', '火锅', '烤肉', '零食', '零食', '酒']),
        # 健身 → 模块二 > 健身习惯
        ('module2', '健身习惯', ['健身', '训练', '卧推', '深蹲', '背', '腿', '胸', '肩']),
        # 兴趣 → 模块二 > 兴趣爱好
        ('module2', '兴趣爱好', ['骑行', '摩托', '模型', '游戏', '打游戏', '旅行', '年卡']),
        # 审美 → 模块二 > 视觉审美偏好
        ('module2', '视觉审美偏好', ['丝袜', '靴子', '穿搭', '衣服', '裙子']),
    ],
    'rule': [
        # 规矩 → 模块三 > 羁绊约定
        ('module3', '羁绊约定', None),
    ],
    # emotion / quote 不自动写入 MEMORY.md
    'emotion': [],
    'quote': [],
}


def insert_fragment(fragment: Dict, dry_run: bool = False) -> Dict:
    """将一条碎片插入 MEMORY.md 的对应 section。

    Args:
        fragment: {"category", "content", "emotion", "date", ...}
        dry_run: True 时只返回目标位置，不实际写入

    Returns:
        {"success", "target_module", "target_section", "insert_line", "message"}
    """
    if not os.path.exists(MEMORY_FILE):
        return {"success": False, "message": "MEMORY.md 不存在"}

    category = fragment.get('category', 'fact')
    content = fragment['content']
    date = fragment.get('date', datetime.now().strftime('%Y-%m-%d'))

    if category not in CATEGORY_TARGETS or not CATEGORY_TARGETS[category]:
        return {"success": False, "message": f"category '{category}' 不自动写入 MEMORY.md", "target_module": None, "target_section": None}

    # 确定目标 section
    target_module, target_section = _resolve_target(category, content)

    if target_module is None:
        return {"success": False, "message": "无法确定插入位置", "target_module": None, "target_section": None}

    # 读取文件
    with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # 定位插入点
    insert_line = _find_insert_point(lines, target_module, target_section)
    if insert_line is None:
        return {"success": False, "message": f"找不到目标 section: {target_module} > {target_section}", "target_module": target_module, "target_section": target_section}

    if dry_run:
        return {"success": True, "target_module": target_module, "target_section": target_section, "insert_line": insert_line, "message": f"dry_run: 将插入到行 {insert_line}"}

    # 检查是否已存在（去重）
    if _content_exists(lines, content):
        return {"success": False, "message": "内容已存在于 MEMORY.md", "target_module": target_module, "target_section": target_section}

    # 检查字符硬上限（保守估算：1中文≈1.5token，MAX_TOKENS/1.5）

    # 备份
    _backup()

    # 插入
    insert_text = f"- {content}"
    emotion = fragment.get('emotion', '')
    if emotion:
        insert_text += f"（{emotion}）"
    insert_text += f"（{date} 自动沉淀）\n"

    lines.insert(insert_line, insert_text)

    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        f.writelines(lines)

    # 校验
    if not _validate():
        _restore()
        return {"success": False, "message": "写入后校验失败，已回滚", "target_module": target_module, "target_section": target_section}

    return {"success": True, "target_module": target_module, "target_section": target_section, "insert_line": insert_line, "message": f"已插入到 {target_module} > {target_section}"}


def insert_fragments(fragments: List[Dict]) -> Dict:
    """批量插入碎片。

    Returns:
        {"total": N, "success": N, "failed": N, "results": [...]}
    """
    results = []
    success = 0
    failed = 0

    for frag in fragments:
        result = insert_fragment(frag)
        results.append(result)
        if result['success']:
            success += 1
        else:
            failed += 1

    return {"total": len(fragments), "success": success, "failed": failed, "results": results}


def _resolve_target(category: str, content: str) -> Tuple[Optional[str], Optional[str]]:
    """根据分类和内容确定插入目标。"""
    targets = CATEGORY_TARGETS.get(category, [])

    if not targets:
        return None, None

    if category == 'fact':
        # 尝试匹配工作关键词
        for module_key, section, keywords in targets:
            if module_key == 'module1' and section == '近期工作动态':
                if any(kw in content for kw in keywords):
                    return module_key, section
            elif module_key == 'module1' and section == '基本信息':
                if any(kw in content for kw in keywords):
                    return module_key, section

        # fact 的兜底：生活类事实归模块二，其他归工作动态
        life_kws = ['买', '搬', '换', '装修', '旅游', '出国', '生病', '体检', '理发', '驾照', '宠物', '手机', '电脑', '车']
        if any(kw in content for kw in life_kws):
            return 'module2', '兴趣爱好'
        return 'module1', '近期工作动态'

    if category == 'preference':
        for module_key, section, keywords in targets:
            if keywords and any(kw in content for kw in keywords):
                return module_key, section
        # 兜底
        return 'module2', '兴趣爱好'

    if category == 'rule':
        return 'module3', '羁绊约定'

    return None, None


def _find_insert_point(lines: List[str], target_module_key: str, target_section: str) -> Optional[int]:
    """找到插入点（目标 section 最后一个条目之后，下一个标题之前）。"""
    module_kws = MODULE_KEYWORDS[target_module_key]

    # 第一遍：找到目标 L2 模块的起始行
    module_start = None
    module_end = len(lines)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith('## '):
            continue
        if any(kw in stripped for kw in module_kws):
            module_start = i
        elif module_start is not None:
            # 找到了下一个 L2 标题 → 模块结束
            module_end = i
            break

    if module_start is None:
        return None

    # 第二遍：在模块范围内找目标 L3 section
    section_start = None
    section_end = module_end

    for i in range(module_start + 1, module_end):
        stripped = lines[i].strip()
        if stripped.startswith('### '):
            if section_start is not None:
                # 找到了下一个 L3 → 上一节结束
                section_end = i
                break
            if target_section in stripped:
                section_start = i

    if section_start is None:
        return None

    # 找 section 内最后一个非空内容行
    insert_line = section_start + 1
    for i in range(section_end - 1, section_start, -1):
        if lines[i].strip() and not lines[i].strip().startswith('#'):
            insert_line = i + 1
            break

    return insert_line


def _content_exists(lines: List[str], content: str) -> bool:
    """检查内容是否已存在于 MEMORY.md（取前50字模糊匹配）。"""
    content_part = content[:50]
    for line in lines:
        if content_part in line:
            return True
    return False


def _backup():
    """备份 MEMORY.md。"""
    if os.path.exists(MEMORY_FILE):
        shutil.copy2(MEMORY_FILE, BACKUP_FILE)


def _restore():
    """从备份恢复 MEMORY.md。"""
    if os.path.exists(BACKUP_FILE):
        shutil.copy2(BACKUP_FILE, MEMORY_FILE)


def _validate() -> bool:
    """校验 MEMORY.md 完整性。"""
    if not os.path.exists(MEMORY_FILE):
        return False
    with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
        content = f.read()
    return len(content) > 100 and content.startswith('#')


def get_token_estimate() -> Dict:
    """获取 MEMORY.md 的 token 估算。"""
    if not os.path.exists(MEMORY_FILE):
        return {"chars": 0, "tokens": 0, "limit": MAX_TOKENS, "usage": 0}

    with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    chars = len(content)
    tokens = int(chars * 1.5)
    return {
        "chars": chars,
        "tokens": tokens,
        "limit": MAX_TOKENS,
        "usage": tokens / MAX_TOKENS,
    }


def cleanup_archived(archived_hashes: List[str]):
    """从 MEMORY.md 中移除已归档碎片对应的内容。

    Args:
        archived_hashes: 需要清理的 content_hash 列表
    """
    # 注意：MEMORY.md 中没有存 content_hash，
    # 所以这个函数需要配合 dreams.db 的 content 字段来做模糊匹配
    # 目前作为预留接口，暂不自动执行
    pass
