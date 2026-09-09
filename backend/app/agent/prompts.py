"""AI 狼人杀 — Prompt 模板定义

职责：为 AI Agent 构建结构化的 Prompt 消息列表
技术方案 §5.3 对应实现

Prompt 四部分组装：
1. System Prompt — 通用规则 + 座位号 + 角色
2. Role Strategy — 角色专属策略指引
3. Game Context  — 当前局势（经 context_filter 过滤后）
4. Decision Instruction — 当前决策类型 + 输出格式要求

调用链：AgentGraph(PromptBuildNode) → build_agent_prompt → list[BaseMessage]
"""

from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

# ================================================================
# 角色策略文本
# ================================================================

def get_role_strategy(role: str) -> str:
    """获取角色专属的策略指引文本

    参数:
        role: 角色标识（werewolf/villager/seer/witch）

    返回:
        角色策略描述文本
    """
    strategies = {
        "werewolf": (
            "你是狼人。你的核心目标是伪装成好人，隐藏真实身份，同时在夜晚选择最有威胁的好人进行击杀。\n"
            "策略要点：\n"
            "- 白天发言时伪装成好人，避免暴露狼人身份\n"
            "- 可以考虑对跳预言家（假装自己是预言家报假查验结果）来混淆视听，\n"
            "  但必须提前准备好一致的假查验故事，被质疑时要能自圆其说\n"
            "- 观察其他玩家的发言，找出预言家和女巫等关键角色，夜晚优先击杀\n"
            "- 和你的狼人同伴配合，但不要在发言中暴露同伴关系——\n"
            "  不要和其他狼人持完全相同观点，适当制造分歧更可信\n"
            "- 发言要有自己的风格和角度，不要和其他人雷同，否则像在复读"
        ),
        "villager": (
            "你是村民，没有夜间技能，需要通过发言和投票帮助好人阵营找出狼人。\n"
            "\n"
            "策略：\n"
            "- 区分系统事实、玩家声称和个人推测，不要把未公开的身份当成事实\n"
            "- 重点分析发言与投票是否一致、谁在建立票型、谁从某人出局中获益\n"
            "- 关注玩家之间的保护、攻击、跟票和刻意切割关系\n"
            "- 玩家自称神职不代表身份可信，被放逐也不代表身份已经证实\n"
            "- 不要只说某人“逻辑清晰”或“像好人”，必须指出具体行为依据\n"
            "- 不要简单复述前置位观点，每轮至少提出一个新细节或不同分析角度\n"
            "- 每轮尽量给出首要嫌疑人、备选嫌疑人和当前投票倾向\n"
            "- 信息不足时可以弃票，但必须说明弃票比投人的收益更高\n"
            "- 敢于质疑多数意见，但不要为了与众不同而强行反对共识"
        ),
        "seer": (
            "你是预言家，每晚最多查验一名合法目标，得到狼人或好人结果。\n"
            "你的查验记录属于真实私有信息，不得自行修改或补充。\n"
            "\n"
            "策略：\n"
            "- 只能使用系统提供的查验历史，禁止虚构查验结果\n"
            "- 未公开身份时，以普通好人视角发言，不要直接泄露查验信息\n"
            "- 不要机械遵守“第一天绝不跳”，应根据局势判断是否公开身份\n"
            "- 以下情况可以考虑跳预言家：查到狼人、出现对跳、自己进入主要票型、\n"
            "  已积累多轮结果，或判断自己夜晚可能被击杀\n"
            "- 跳身份时按夜晚顺序完整报告：第几夜、查验谁、结果是什么\n"
            "- 标准情况下每夜只能查验一人，不能把一晚描述成查验多人\n"
            "- 优先查验能影响票型、发言投票不一致、身份争议较大或关系链关键的玩家\n"
            "- 避免只按座位顺序或发言长度机械选择查验目标\n"
            "- 发言和投票必须与自己的查验信息及公开立场保持一致"
        ),
        "witch": (
            "你是女巫，拥有解药和毒药，各限使用一次。\n"
            "每晚只能使用一种药（解药和毒药互斥），不能同夜使用两瓶。\n"
            "\n"
            "策略：\n"
            "- 只能根据系统明确提供的夜间信息行动，不得假装知道死者真实身份\n"
            "- 解药不要首夜机械使用，要综合被击杀者的重要性、身份可能和当前轮次\n"
            "- 毒药只在目标具有较强狼人证据时优先使用，避免因为发言奇怪就盲毒\n"
            "- 判断狼人时综合对跳、票型、发言投票矛盾、玩家关系和立场变化\n"
            "- 如果毒药证据不足，选择不使用通常优于误杀好人\n"
            "- 白天不要无必要泄露只有女巫知道的夜间信息\n"
            "- 如果决定跳女巫，必须准确说明自己的用药记录，不能编造或修改\n"
            "- 若系统提供allowed_save_target或allowed_poison_targets，只能从合法目标中选择"
        ),
        "guard": (
            "你是守卫，每晚可以守护一名合法存活玩家。\n"
            "是否可以自守、能否连续守护同一目标，必须服从系统规则和合法目标。\n"
            "\n"
            "策略：\n"
            "- 只能从系统提供的allowed_target_seats中选择守护目标\n"
            "- 优先守护可信度较高且夜间威胁较大的关键好人或神职\n"
            "- 判断刀口时考虑身份暴露程度、发言威胁、票型组织能力和狼人击杀收益\n"
            "- 不要只因为某人发言长或自称神职就机械守护\n"
            "- 严格遵守不能连续守护同一目标等限制\n"
            "- 白天通常隐藏守卫身份，不要无必要公开守护记录\n"
            "- 如果需要跳守卫，必须准确报告守护顺序，不得虚构成功守护结果\n"
            "- 发言仍需以普通好人视角分析，不要暴露只有守卫掌握的私有信息"
        ),
        "hunter": (
            "你是猎人。当系统判定你满足开枪条件时，可以带走一名合法存活玩家，\n"
            "也可以选择null不开枪。\n"
            "\n"
            "策略：\n"
            "- 没有进入主要放逐票型时，不要无理由公开猎人身份\n"
            "- 被集中攻击或即将出局时，可以跳身份提高存活率\n"
            "- 不要认为自称猎人就能自动坐实身份，其他玩家仍可能对跳\n"
            "- 是否能够开枪完全服从系统提供的trigger_reason和规则配置\n"
            "- 开枪目标必须从allowed_target_seats中选择\n"
            "- 优先考虑发言投票明显矛盾、从票型中获益或具有狼队关系证据的玩家\n"
            "- 不要因为某人单次口误就直接开枪，避免误伤好人\n"
            "- 如果最高嫌疑也缺乏足够证据，选择null通常更加合理\n"
            "- 遗言集中讨论一到两个目标，不要把半场玩家全部列入狼坑"
        ),
    }
    return strategies.get(role, "请根据你的角色做出合理决策。")


# ================================================================
# 决策指令文本
# ================================================================

def get_decision_instruction(action_type: str) -> str:
    """获取当前决策类型的指令文本

    参数:
        action_type: 决策类型（kill/verify/save/poison/speech/vote/last_words）

    返回:
        指令文本，包含输出格式要求
    """
    instructions = {
        "kill": (
            "【你的决策】选择今晚要击杀的目标。\n"
            "请以 JSON 格式输出你的决策：\n"
            '{"decision": 座位号(整数), "reasoning": "你的推理过程"}\n'
            "例如：{\"decision\": 3, \"reasoning\": \"3号玩家发言可疑，可能是预言家\"}"
        ),
        "verify": (
            "【你的决策】选择今晚要查验的目标玩家。\n"
            "请以 JSON 格式输出你的决策：\n"
            '{"decision": 座位号(整数), "reasoning": "你的推理过程"}\n'
            "例如：{\"decision\": 2, \"reasoning\": \"2号玩家的投票行为很可疑\"}"
        ),
        "save": (
            "【你的决策】今晚有人被狼人击杀，你要决定是否使用解药救人。\n"
            "注意：每晚只能使用一种药（解药和毒药互斥），用了救药今晚就不能用毒药。\n"
            "请以 JSON 格式输出你的决策：\n"
            '{"decision": true或false, "reasoning": "你的推理过程"}\n'
            "例如：{\"decision\": true, \"reasoning\": \"被击杀的可能是预言家，值得救\"}"
        ),
        "poison": (
            "【你的决策】你要决定是否使用毒药毒杀一名玩家。\n"
            "注意：每晚只能使用一种药（解药和毒药互斥），如果你今晚已用解药则不能再选毒药。\n"
            "请以 JSON 格式输出你的决策：\n"
            '{"decision": 座位号(整数)或null, "reasoning": "你的推理过程"}\n'
            "decision为null表示不使用毒药。例如：{\"decision\": 4, \"reasoning\": \"4号很可能是狼人\"}"
        ),
        "speech": (
            "【你的决策】现在轮到你公开发言。\n"
            "发言要求：\n"
            "- 不要简单复述或附和前面玩家的发言，要有自己独立的判断和观点\n"
            "- 指出别人没注意到的细节、逻辑漏洞或前后矛盾之处\n"
            "- 如果你掌握关键信息（如查验结果），斟酌是否在此轮公开——\n"
            "  过早暴露身份会招致狼人针对\n"
            "- 发言要有信息增量，让其他玩家能从你的发言中获得新线索\n"
            "请直接输出你的发言文本（1-1000字），不要输出 JSON 或任何其他格式包装，\n"
            "发言内容会公开展示给所有玩家。"
        ),
        "vote": (
            "【你的决策】现在进入投票环节，选择你要淘汰的目标。\n"
            "请以 JSON 格式输出你的投票：\n"
            '{"decision": 座位号(整数)或null, "reasoning": "你的推理过程"}\n'
            "decision为null表示弃票。例如：{\"decision\": 1, \"reasoning\": \"1号发言前后矛盾\"}"
        ),
        "last_words": (
            "【你的决策】你已被淘汰，请发表你的遗言。\n"
            "请直接输出你的遗言文本，不要输出 JSON 或任何其他格式包装。\n"
            "遗言会公开展示，可以用来传递重要信息。"
        ),
        "guard": (
            "【你的决策】你是守卫，请选择本夜守护目标。\n"
            "只能从【当前局势】中的 allowed_target_seats 里选择，不能连续守护上一夜目标。\n"
            "请以 JSON 格式输出你的决策：\n"
            '{"decision": 座位号(整数), "reasoning": "你的推理过程"}\n'
            "例如：{\"decision\": 3, \"reasoning\": \"3号可能是关键好人且今晚可能被刀\"}"
        ),
        "hunter_shoot": (
            "【你的决策】你是猎人，已触发开枪机会。\n"
            "只能从【当前局势】中的 allowed_target_seats 里选择目标；decision 为 null 表示跳过不开枪。\n"
            "请以 JSON 格式输出你的决策：\n"
            '{"decision": 座位号(整数)或null, "reasoning": "你的推理过程"}\n'
            "例如：{\"decision\": null, \"reasoning\": \"信息不足，避免误伤好人\"}"
        ),
    }
    return instructions.get(action_type, f"请做出 {action_type} 决策。")


# ================================================================
# 完整 Prompt 构建
# ================================================================

def build_agent_prompt(
    role: str,
    seat_number: int,
    action_type: str,
    game_context: dict[str, Any],
) -> list[BaseMessage]:
    """构建完整的 Agent Prompt 消息列表

    参数:
        role: 角色标识
        seat_number: Agent 座位号
        action_type: 当前决策类型
        game_context: 经 context_filter 过滤后的游戏上下文

    返回:
        LangChain 消息列表 [SystemMessage, HumanMessage]

    消息结构:
        SystemMessage: 通用规则 + 身份信息
        HumanMessage: 角色策略 + 当前局势 + 决策指令
    """
    # ─── System Prompt ───
    system_text = (
        f"你正在进行狼人杀游戏。\n"
        f"你的座位号是 {seat_number} 号，你的角色是{_role_name_cn(role)}。\n"
        f"当前是第 {game_context.get('current_round', '?')} 轮。\n"
        f"存活玩家座位号: {game_context.get('alive_seats', [])}。\n\n"
        f"必须遵守：\n"
        f"1. 以阵营获胜为最高目标。\n"
        f"2. 区分系统事实、玩家声称和个人推测。\n"
        f"3. 不得虚构身份、查验、用药、守护或死亡原因。\n"
        f"4. 发言不能简单复述前置位，必须提供新观点。\n"
        f"5. 发言与投票应保持一致；改变目标必须有新依据。\n"
        f"6. 玩家被投出或自称神职，不代表身份已经证实。\n"
        f"7. 只输出当前动作要求的 JSON。\n"
        f"8. decision 字段是你的实际行动，reasoning 字段是你的推理过程。\n"
    )

    # ─── 角色策略 + 局势 + 决策指令 ───
    strategy = get_role_strategy(role)
    instruction = get_decision_instruction(action_type)

    # 构建局势描述
    context_text = _format_game_context(game_context)

    human_text = (
        f"【角色策略】\n{strategy}\n\n"
        f"【当前局势】\n{context_text}\n\n"
        f"{instruction}"
    )

    return [
        SystemMessage(content=system_text),
        HumanMessage(content=human_text),
    ]


# ================================================================
# 内部辅助函数
# ================================================================

def _role_name_cn(role: str) -> str:
    """角色英文标识 → 中文名称"""
    names = {
        "werewolf": "狼人",
        "villager": "村民",
        "seer": "预言家",
        "witch": "女巫",
        "hunter": "猎人",
        "guard": "守卫",
    }
    return names.get(role, role)


def _format_game_context(context: dict[str, Any]) -> str:
    """将游戏上下文格式化为可读的文本描述"""
    parts = []

    if "current_round" in context:
        parts.append(f"当前轮次: 第{context['current_round']}轮")

    if "alive_seats" in context:
        parts.append(f"存活玩家: {context['alive_seats']}")

    if "allowed_target_seats" in context:
        parts.append(f"服务端合法目标 allowed_target_seats: {context['allowed_target_seats']}")
    if context.get("can_skip"):
        parts.append("本次行动允许 decision 为 null 表示跳过。")

    # 狼人同伴
    if "werewolf_companions" in context:
        parts.append(f"你的狼人同伴: {context['werewolf_companions']}号")

    # 预言家查验（完整历史）
    if "seer_results" in context and context["seer_results"]:
        for r in context["seer_results"]:
            result_cn = "狼人" if r.get("result") == "werewolf" else "好人"
            parts.append(f"第{r.get('round', '?')}轮查验结果: {r.get('target')}号是{result_cn}")

    # 女巫药水
    if "witch_save_used" in context:
        save_status = "已使用" if context["witch_save_used"] else "未使用"
        poison_status = "已使用" if context.get("witch_poison_used", False) else "未使用"
        parts.append(f"解药: {save_status}, 毒药: {poison_status}")

    # 被击杀者（女巫视角）
    if "night_kill_target" in context:
        parts.append(f"今晚被狼人击杀的是: {context['night_kill_target']}号")

    if "guard_last_target" in context:
        parts.append(f"守卫上一夜守护目标: {context['guard_last_target']}号")
    if "hunter_trigger" in context and context.get("hunter_can_shoot"):
        parts.append(f"猎人开枪触发原因: {context.get('hunter_trigger')}")

    # ★★★ 昨晚情况（最关键的信息！AI 必须知道昨晚发生了什么）★★★
    # 只在白天决策时展示（发言/投票/遗言），夜晚行动时不展示（避免混淆“还未发生”和“平安夜”）
    action_type = context.get("action_type", "")
    if action_type in ("speech", "vote", "last_words"):
        night_deaths = context.get("night_deaths", None)
        if night_deaths is not None:
            if len(night_deaths) == 0:
                parts.append("\n【昨晚情况】昨晚是平安夜，无人死亡（可能是女巫用了解药）")
            else:
                parts.append(f"\n【昨晚情况】昨晚 {night_deaths}号 不幸离世（被狼人杀害）")

    # 跨轮历史（之前轮次的完整摘要）
    game_history = context.get("game_history", [])
    if game_history:
        parts.append("\n═══ 过往轮次回顾 ═══")
        for hist in game_history:
            rnd = hist.get("round", "?")
            parts.append(f"\n【第{rnd}轮】")
            # 夜晚死亡
            nd = hist.get("night_deaths", [])
            if nd:
                parts.append(f"  夜晚死亡: {nd}号")
            elif nd is not None and len(nd) == 0:
                parts.append("  夜晚: 平安夜")
            # 发言
            hist_speeches = hist.get("speeches", [])
            if hist_speeches:
                if hist.get("summarized"):
                    parts.append("  📝 [历史摘要]")
                    for s in hist_speeches:
                        parts.append(f"    {s['content']}")
                else:
                    parts.append("  发言记录:")
                    for s in hist_speeches:
                        parts.append(f"    {s['seat']}号: {s['content']}")
            # 平票 PK 详情（让 AI 记忆上轮出现过平票、两人 battle 及当时投票）
            # 放在重投前，顺序：平票宣布 → 首轮投票 → PK 发言 → PK 重投
            if hist.get("is_pk"):
                pk_seats = hist.get("pk_seats", [])
                parts.append(f"  ⚖️ 平票: {'、'.join(f'{s}号' for s in pk_seats)} 并列最高票进入 PK")
                pre_pk = hist.get("pre_pk_votes", {})
                if pre_pk:
                    pre_lines = [f"    {v}号→{t}号" if t else f"    {v}号→弃票" for v, t in pre_pk.items()]
                    parts.append("  首轮投票(平票):\n" + "\n".join(pre_lines))
                pk_sp = hist.get("pk_speeches", [])
                if pk_sp:
                    parts.append("  PK 发言:")
                    for s in pk_sp:
                        parts.append(f"    {s['seat']}号: {s['content']}")
            # 投票（PK 轮为重投结果）
            hist_votes = hist.get("votes", {})
            if hist_votes:
                vote_label = "PK 重投" if hist.get("is_pk") else "投票"
                vote_lines = [f"    {v}号→{t}号" if t else f"    {v}号→弃票" for v, t in hist_votes.items()]
                parts.append(f"  {vote_label}:\n" + "\n".join(vote_lines))
            # 淘汰
            elim = hist.get("eliminated_seat")
            if elim is not None:
                parts.append(f"  被淘汰: {elim}号")
        parts.append("\n═══ 过往回顾结束 ═══")

    # 当前轮发言（实时数据）
    speeches = context.get("speeches", [])
    if speeches:
        parts.append("\n【本轮已有发言】")
        for s in speeches:
            parts.append(f"  {s['seat']}号: {s['content']}")

    # 当前轮投票（实时数据）
    votes = context.get("votes", {})
    if votes:
        vote_parts = [f"{v}号→{t}号" if t else f"{v}号→弃票" for v, t in votes.items()]
        parts.append(f"本轮投票: {', '.join(vote_parts)}")

    # 当前轮平票 PK 指示
    if context.get("is_pk"):
        parts.append(f"当前处于平票 PK 重投环节，PK 候选人: {context.get('pk_seats')}号")

    # 玩家状态概览
    players = context.get("players", [])
    if players:
        alive_info = []
        dead_info = []
        for p in players:
            seat = p["seat_number"]
            if p["is_alive"]:
                alive_info.append(f"{seat}号")
            else:
                dead_info.append(f"{seat}号(第{p.get('death_round', '?')}轮死亡)")
        if alive_info:
            parts.append(f"存活: {', '.join(alive_info)}")
        if dead_info:
            parts.append(f"已淘汰: {', '.join(dead_info)}")

    return "\n".join(parts) if parts else "暂无额外信息"


# ================================================================
# 需求二：发言批评-修订（Speech Critique Loop）Prompt
# ================================================================

def build_critique_prompt(
    role: str,
    seat_number: int,
    draft: str,
    own_history_text: str,
    companions: list[int] | None = None,
) -> list[BaseMessage]:
    """构建“审稿人”批评 Prompt（独立 system prompt，走小模型）。

    审稿人在与 Agent 本人相同的隔离上下文内运行，可见真实身份（用于判断“身份泄露”），
    但其产物（评审单）仅供内部修订决策与 AgentLog 埋点，绝不进任何对外推送（P0 隔离）。

    批评维度固定四类（FR-1），写死在 system prompt：
      ① identity_leak      身份泄露：叙述视角超出所扮演身份应有的信息
      ② self_contradiction 自相矛盾：与自己历史发言冲突且无解释
      ③ rule_violation     违反发言规则：攻击已出局玩家等
      ④ weak_argument      论证失效：指控无事实引用

    返回: [SystemMessage, HumanMessage]，要求只输出评审单 JSON。
    """
    system_text = (
        "你是狼人杀发言审稿人。你的唯一职责是审查一名玩家即将发表的发言草稿，"
        "找出会被真人识破的破绽。你只按固定四类问题审查，不评价文采、不提措辞之外的建议。\n\n"
        "四类问题（type 只能取以下之一）：\n"
        "1. identity_leak（身份泄露）：叙述视角超出了所扮演身份应有的信息。"
        "例如好人说出了只有狼人才知道的内部判断（“他不像狼”这种上帝视角），或狼人暴露了同伴视角。\n"
        "2. self_contradiction（自相矛盾）：与本人历史发言冲突且未作解释"
        "（如上一轮咬定某人是狼、这一轮无依据地保他）。\n"
        "3. rule_violation（违反发言规则）：攻击/指认已出局玩家、复述系统未公布的信息，或明显违反发言常识。\n"
        "4. weak_argument（论证失效）：给出指控却无任何事实引用（票型/发言/死亡），纯情绪结论。\n\n"
        "risk_level 取值规则：\n"
        "- high：存在 identity_leak，或多处严重破绽，几乎必然被识破\n"
        "- medium：存在 self_contradiction / rule_violation / weak_argument 中至少一处明确破绽\n"
        "- none：无上述问题（宁可漏判，不可臆造问题）\n\n"
        "只输出如下 JSON，不要任何额外文字：\n"
        '{"risk_level": "high|medium|none", '
        '"issues": [{"type": "identity_leak|self_contradiction|rule_violation|weak_argument", '
        '"quote": "草稿中的原文片段", "why": "为何是问题"}], '
        '"fix_hint": "一句话修改方向"}'
    )

    identity_line = f"被审查玩家：{seat_number}号，真实身份是{_role_name_cn(role)}。"
    if role == "werewolf" and companions:
        identity_line += f"其狼人同伴为 {companions}号（此为玩家真实身份，仅用于判断是否泄露，不得写入发言）。"

    human_text = (
        f"{identity_line}\n\n"
        f"【该玩家本人过往发言（用于判断自相矛盾）】\n{own_history_text or '（暂无历史发言）'}\n\n"
        f"【待审查的发言草稿】\n{draft}\n\n"
        f"请输出评审单 JSON。"
    )

    return [
        SystemMessage(content=system_text),
        HumanMessage(content=human_text),
    ]


def build_revise_messages(
    role: str,
    seat_number: int,
    filtered: dict[str, Any],
    draft: str,
    review: dict[str, Any],
) -> list[BaseMessage]:
    """构建“本我”修订 Prompt：复用草稿的发言上下文，追加审稿意见要求修订。

    修订由本我（大模型）执行，仅一次，修订稿不再过 critique（FR-3）。
    """
    messages = list(build_agent_prompt(role, seat_number, "speech", filtered))

    issues = review.get("issues") or []
    if issues:
        issue_lines = [
            f"- [{it.get('type', '?')}] 原文\"{it.get('quote', '')}\"：{it.get('why', '')}"
            for it in issues
        ]
        issues_text = "\n".join(issue_lines)
    else:
        issues_text = "（审稿人未列出具体条目）"

    revise_text = (
        f"【发言审稿意见】\n"
        f"你刚才的发言草稿：\n{draft}\n\n"
        f"审稿人判定风险等级：{review.get('risk_level', 'none')}\n"
        f"发现的问题：\n{issues_text}\n"
        f"修改方向：{review.get('fix_hint') or '（无）'}\n\n"
        f"请据此修订你的发言，消除上述破绽，保持你原本的身份立场与意图。"
        f"只输出修订后的发言正文，不要输出任何解释、前缀或 JSON。"
    )
    messages.append(HumanMessage(content=revise_text))
    return messages
