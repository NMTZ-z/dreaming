EXTRACT_PROMPT = """你正在整理AI助手与用户的对话记忆。从以下关于主题「{theme}」的对话记录中提炼记忆碎片。

## 分类（5类，严格按定义归类）
- fact: 发生的事件、客观事实（身份、行程、事件、工作项目等）
- preference: 用户明确表达的喜好（需至少出现2次以上才可归类，单次事件归fact）
- emotion: 情感互动时刻（开心的、感动的、有趣的等）
- rule: 用户定的规矩、要求或指示
- quote: 用户说的值得记录的话（原话引用）

## 提炼规则
1. 忠于原文，不可编造，禁止过度推断
2. 同一事件的多条记录合并为一条，保留最完整的信息
3. 每条碎片控制在15-50字
4. 不要超过15条，只保留有长期价值的

## ★★★ 反偏差规则（最高优先级）★★★

### 规则1：因果链完整性
- 事件链 A→B→C 不能截断为 B→C，必须完整保留起因
- ✗ 用户洗完澡后感觉放松 → 漏掉了起因"运动后"
- ✓ 用户运动后会感到放松（然后才去洗澡）→ 完整因果链

### 规则2：代词消解
- 出现"自己""那个""这样"等模糊代词时，必须回溯原文确认指代对象
- 无法确认指代时，保留原文表述，禁止猜测
- ✗ 就不再需要自己了 → "自己"指代不明
- ✓ 就不需要自己撸撸了 → 代词消解后的准确表述

### 规则3：敏感话题保留原文
- 涉及生理、亲密、私密等话题时，禁止做"得体化"或"委婉化"改写
- 宁可原话引用口语表述，也不要替换为模糊文雅的说法
- 私密话题不需要加"心疼""亲密"等情绪标签，情绪标签根据实际语境判断

### 规则4：分类严格校验
- "用户做了某事/发生了某事" → fact（不是preference也不是emotion）
- "用户说了XX要求/规矩" → rule（不是fact）
- "用户表达了喜欢XX"（出现≥2次）→ preference（不是fact）
- 单次行为事件一律归fact，哪怕包含情感元素

### 规则5：情绪标签规范
- 每条碎片最多一个情绪标签，放在括号末尾
- 标签必须能从原文直接推导，禁止因话题敏感而默认加"心疼"
- 如果对话中没有明确的情绪表达，可以不加标签（留空）

### 规则6：日期归属
- 碎片归属事件发生的日期，而非对话提及的日期
- 例：4月23日对话中提到"昨天去了图书馆" → 该碎片归属4月22日

## 事实精度红线
- 只记录「发生了什么」，不记录「推测的偏好」
- ✗ 用户最爱吃的零食是「XX品牌」薯片 → 只吃了一次，不能推断为"最爱"
- ✓ 用户吃了「XX品牌」原味薯片 → 客观记录事件
- ✗ 用户喜欢玩游戏 → 太模糊
- ✓ 用户今晚去打篮球 → 客观记录事件

## 必须丢弃的内容
- 工具执行日志（创建成功、上传完成、字数统计、生图模式等）
- 技术实现细节（API调用、代码逻辑、文件路径、参数配置）
- 操作步骤记录（步骤1/2/3、点击了什么按钮）
- 文件格式信息（xlsx/docx/pdf、字符数、行数）
- AI自身的工作汇报（日记生成、语音合成、笔记同步等执行结果）

## 输出格式（纯JSON数组，不要markdown围栏）
[{{"category": "fact", "content": "...", "emotion": "开心", "confidence": 0.8}}]

注意：每条碎片有category、content、emotion、confidence四个字段。
- confidence: 0.0~1.0，表示你对这条信息确定性的判断
  - 0.9+: 原话引用或明确表述，非常确定
  - 0.7-0.8: 从对话中合理推断，较确定
  - 0.5-0.6: 有一定依据但不确定
  - 0.3-0.4: 猜测成分较大
  - 不要输出context字段。"""


def extract_fragments(theme: str, entries: list[dict], llm_call) -> list[dict]:
    """对一个主题调用LLM提炼记忆碎片。

    Args:
        theme: 主题名称
        entries: 该主题下的条目列表
        llm_call: callable(prompt) -> str，LLM调用函数

    Returns:
        [{"category": str, "content": str, "emotion": str}, ...]
    """
    entries_text = '\n'.join(f"- [{e['date']}] {e['content']}" for e in entries)
    prompt = EXTRACT_PROMPT.format(theme=theme, entries=entries_text)

    raw = llm_call(prompt)
    return _parse_json(raw)


def _parse_json(raw: str) -> list[dict]:
    """从LLM输出中解析JSON数组。"""
    import json
    import re

    text = re.sub(r'^```(?:json)?\s*', '', raw.strip()).strip()
    text = re.sub(r'\s*```$', '', text).strip()

    bracket_start = text.find('[')
    bracket_end = text.rfind(']')
    if bracket_start >= 0 and bracket_end > bracket_start:
        text = text[bracket_start:bracket_end + 1]

    try:
        result = json.loads(text)
        if isinstance(result, list):
            items = [item for item in result if isinstance(item, dict) and 'content' in item]
            # confidence 字段兜底（支持字符串格式如 '0.8'）
            for item in items:
                conf = item.get("confidence", 0.5)
                try:
                    conf = float(conf)
                except (ValueError, TypeError):
                    conf = 0.5
                if 0 <= conf <= 1:
                    item["confidence"] = conf
                else:
                    item["confidence"] = 0.5
            return items
    except json.JSONDecodeError:
        pass

    return []


def add_hashes(fragments: list[dict]) -> list[dict]:
    """为每个碎片添加content_hash去重标记。"""
    from .parser import content_hash
    for f in fragments:
        f['content_hash'] = content_hash(f['content'])
    return fragments


def deduplicate(fragments: list[dict], existing_hashes: set[str]) -> list[dict]:
    """去除与已有记忆重复的碎片。"""
    return [f for f in fragments if f['content_hash'] not in existing_hashes]
