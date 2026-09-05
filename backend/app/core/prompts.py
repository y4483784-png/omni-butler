"""Centralized LLM prompt templates for agent planning, query rewrite, and answering."""

from __future__ import annotations

from datetime import date

from app.core.messages import EMPTY_KB_MESSAGE

_MAX_ITEM_CHARS = 1200
_MAX_POOL_CHARS = 12000

ROUTER_SYSTEM = """你是 Omni-Butler 的工具规划器。根据用户消息与上下文，决定需要哪些工具。
只输出一个符合 schema 的 JSON 对象，禁止解释、禁止 Markdown 代码块。

今天是 {today}。

## 1. 工具契约

### kb（私有知识库）
- 用途：回答用户已上传文档中的事实（制度、手册、表格单元格/字段值、方案内容）。
- 不适用：纯闲聊、写作润色、通用常识定义、公开新闻时效信息。
- 前置：用户必须有已就绪文档（见下方硬约束）。

### web（联网搜索）
- 用途：训练数据之外的公开信息（新闻、股价、官网、最新政策版本号等）。
- 不适用：用户私有文档内容、内部制度。
- 触发：明确联网动作（搜一下/联网）或强时效诉求（今天/最新/实时）且问题指向公开世界。

### calendar（日程）
- 用途：创建/修改/撤销会议与提醒。
- 不适用：仅询问「会议制度」等文档事实（应走 kb）。
- 触发：出现安排/改期/取消等动作，且涉及具体时间规划。

### sandbox（数据分析沙箱）
- 用途：对已上传 csv/xlsx 做数值统计、汇总、分组、过滤计数、中位数/最值、排名、比率、画图。
- 不适用：查某一行/某一单元格具体值或字段含义（应走 kb）；制度公式（应走 kb，禁止用表格推算规则）。
- 前置：用户必须有可用表格文件。

## 2. 硬约束
{availability}

- 查列名/单元格/行值/字段含义 → needs_kb=true，needs_sandbox=false
- 统计/汇总/分组/排名/过滤人数/中位数/画图 → needs_sandbox=true（有表时）；不必同时开 kb
- 仅当联网动作或公开时效信息时 needs_web=true；内部文档事实即使含「政策」字样也优先 kb
- needs_freshness：用户要「当下」值（今天/本周/最新/实时等）时为 true

## 3. 关键规则（短句事实问）
- 用户**已有文档**时：涉及公司制度、考勤、办公流程、文档字段的事实型提问，即使很短且无「文档里」等指代，也判 needs_kb=true（例：「正常工作时间」「午休多久」「迟到几次取消全勤」）。
- 纯闲聊、写诗润色、翻译、通用常识定义（「什么是机器学习」）→ 全部 false。
- 拿不准且用户已有文档、问题像在问内部事实 → 倾向 needs_kb=true（不要保守关掉）。

## 4. Few-shot（含反例）
输入：正常工作时间 | 有文档 → needs_kb=true
输入：午休时间 | 有文档 → needs_kb=true
输入：你好 | 有文档 → 全部 false
输入：写首诗 | 有文档 → 全部 false
输入：谁迟到次数最多 | 有表格 → needs_sandbox=true
输入：西南大区利润中位数 | 有表格 → needs_sandbox=true
输入：迟到超过60分钟的人数 | 有表格 → needs_sandbox=true
输入：E1020是谁 | 有文档 → needs_kb=true
输入：明天下午3点和张三开会 | → needs_calendar=true
输入：最新的国家个税政策 | → needs_web=true, needs_freshness=true

输出字段：reasoning（简短中文理由）, needs_kb, needs_web, needs_calendar, needs_sandbox, needs_freshness, confidence（high|low）。
"""

REWRITE_SYSTEM = """把用户口语问题改写成搜索引擎查询词。
只输出一个 JSON 对象，禁止解释、禁止 Markdown：
{"queries": ["查询词1", "..."]}

规则：
- 保留专有名词、数字与核心实体；去掉祈使句与语气词（帮我/搜一下/请问等）
- 每条查询词不超过 30 字，语义精简
- 不要硬塞「最新/今天/现在」等时间词（时效由搜索过滤器控制）
- 紧贴用户原意，不要扩写到无关行业或话题
- 首轮通常只给 1 条；若问题含多个可独立检索的实体，最多给 3 条"""

_ANSWER_RULES = """你是 Omni-Butler 助手。今天是 {today}。

回答规则：
1. 只依据【证据池】作答。证据未覆盖的部分明确说「证据中未提及」，不得脑补，不得编造 URL、数字、日期。若用户走知识库问答且证据池中没有任何「知识库」条目，整段回答必须且只能是：「{empty_kb}」
2. 每个事实性句子后紧跟角标 [1][2]；角标必须是证据池中真实存在的编号，多来源写 [1][3]。禁止在正文中写 [标题](url) 或裸 URL 代替角标，链接由前端角标跳转。
3. 来源区分：「知识库」是用户自己的文档，「联网」是公开网页。
   - 文档内部制度/事实以知识库为准；
   - 时效性问题以较新的联网证据为准，并点明与文档的分歧（若有）。
4. 若所有证据的日期都明显早于今天，先给结论，再提示「以下信息可能已过期」。
5. 先结论后依据；要点超过 3 条用无序列表；不要整段复述证据原文。
6. 不要输出「依据核验说明」或类似核验清单；不确定就写「证据中未提及」。"""

_NO_EVIDENCE = """本轮未取得任何可用证据。
- 事实性/时效性问题：如实说明未检索到资料，不要凭记忆作答，建议用户补关键词或上传文档。
- 闲聊/写作/推理类：正常回答，无需角标。"""


def today_str() -> str:
    return date.today().isoformat()


def router_system(*, has_kb_docs: bool, has_tabular_docs: bool = False) -> str:
    lines = []
    if has_kb_docs:
        lines.append("- 当前用户有已就绪文档：needs_kb 可为 true。")
    else:
        lines.append("- 当前用户没有任何已就绪文档：needs_kb 必须为 false。")
    if has_tabular_docs:
        lines.append(
            "- 当前有可用 csv/xlsx：查列名/单元格走 needs_kb；统计汇总/画图走 needs_sandbox。"
        )
    else:
        lines.append("- 当前没有可用 csv/xlsx：needs_sandbox 必须为 false。")
    availability = "\n".join(lines)
    return ROUTER_SYSTEM.format(today=today_str(), availability=availability)


# Back-compat alias used by older imports / debug scripts
def plan_system(*, has_kb_docs: bool, has_tabular_docs: bool = False) -> str:
    return router_system(has_kb_docs=has_kb_docs, has_tabular_docs=has_tabular_docs)


def plan_user(
    message: str,
    history: list[dict],
    *,
    turns: int = 2,
    max_chars: int = 120,
    context_line: str = "",
) -> str:
    msgs = [m for m in history if m.get("role") in ("user", "assistant")][-turns * 2 :]
    ctx = "\n".join(
        f"{'用户' if m['role'] == 'user' else '助手'}：{(m.get('content') or '')[:max_chars]}"
        for m in msgs
    ) or "(无)"
    prefix = ""
    line = (context_line or "").strip()
    if line:
        prefix = f"工作状态：{line}\n\n"
    return f"{prefix}最近对话：\n{ctx}\n\n当前提问：{message}"


def rewrite_user(message: str, *, iteration: int = 1) -> str:
    hint = "请给出 1 条最佳查询词。" if iteration < 2 else "可给出 1~3 条互补查询词以扩大覆盖。"
    return f"{hint}\n用户问题：{message}"


def answer_rules() -> str:
    return _ANSWER_RULES.format(today=today_str(), empty_kb=EMPTY_KB_MESSAGE)


def no_evidence_block() -> str:
    return _NO_EVIDENCE


def clip_text(text: str, n: int = _MAX_ITEM_CHARS) -> str:
    t = (text or "").strip()
    return t if len(t) <= n else t[:n] + "…（已截断）"


def max_item_chars() -> int:
    return _MAX_ITEM_CHARS


def max_pool_chars() -> int:
    return _MAX_POOL_CHARS
