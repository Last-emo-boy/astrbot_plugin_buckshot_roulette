# main.py
from astrbot.api.all import *
import asyncio
import textwrap
import random

def generate_random_bullet_list():
    """
    随机生成一个弹夹列表:
    - 子弹数量在 3 ~ 8 之间
    - 每发子弹为 "实弹" 或 "空包弹" (各 50% 概率)
    - 最后再洗牌
    """
    bullet_count = random.randint(3, 8)
    bullets = []
    for _ in range(bullet_count):
        bullets.append("实弹" if random.random() < 0.5 else "空包弹")
    random.shuffle(bullets)
    return bullets

@register(
    "astrbot_plugin_buckshot_roulette",  # 插件名 (必须唯一)
    "Your Name",                         # 作者
    "恶魔轮盘 - Buckshot Roulette",       # 简要描述
    "1.0.0"                              # 版本
)
class BuckshotRoulette(Star):
    """
    AstrBot 恶魔轮盘游戏插件。
    在群聊里支持 2 人对战，使用种种道具与子弹组合决胜负。
    """

    def __init__(self, context: Context, config: dict = None):
        """
        :param context: AstrBot 传入的 Context
        :param config: (可选) 从 _conf_schema.json 读取的用户配置
        """
        super().__init__(context)

        # 如果未定义 _conf_schema.json 或者没有配置，给出默认
        if not config:
            config = {}

        # 仅示例简单配置，供游戏强制结束使用
        self.config = {
            "admin": config.get("admin", []),             # 游戏管理员列表
            "maxWaitTime": config.get("maxWaitTime", 180) # 等待玩家2的最大时间
        }

        # 记录不同群聊的游戏状态 { cid: {...}, ... }
        self.games = {}

        # 定义可用道具
        self.item_list = {
            "手锯": {
                "description": "下一发造成双倍伤害，不可叠加",
                "use": self.use_saw,
            },
            "放大镜": {
                "description": "查看当前膛内的子弹",
                "use": self.use_magnifier,
            },
            "啤酒": {
                "description": "卸下当前膛内的子弹",
                "use": self.use_beer,
            },
            "香烟": {
                "description": "恢复1点生命值",
                "use": self.use_cigarette,
            },
            "手铐": {
                "description": "跳过对方下一回合",
                "use": self.use_handcuff,
            },
            "肾上腺素": {
                "description": "立刻指定对方的道具并使用（不可选择肾上腺素）",
                "use": self.use_epinephrine,
            },
            "过期药物": {
                "description": "50%概率+2血，50%概率-1血",
                "use": self.use_expired_medicine,
            },
            "逆转器": {
                "description": "实弹 ⇔ 空包弹",
                "use": self.use_reverser,
            },
            "一次性电话": {
                "description": "随机告知其中一发子弹是实弹还是空包弹（不移除）",
                "use": self.use_once_phone,
            }
        }

    def get_channel_id(self, event: AstrMessageEvent) -> str:
        """
        获取唯一群聊ID（或session_id）。
        优先使用群ID，没有则使用 session_id 作为私聊标识。
        """
        gid = event.get_group_id()
        if gid:
            return gid
        return event.session_id  # 私聊时使用 session_id

    @command_group("恶魔轮盘")
    def demon_roulette(self):
        """恶魔轮盘游戏主指令组"""
        pass

    @demon_roulette.command("创建游戏")
    async def create_game(self, event: AstrMessageEvent):
        """
        创建游戏：仅当本群尚无游戏时可创建。
        创建后等待玩家2加入，否则超时自动取消。
        """
        cid = self.get_channel_id(event)
        if cid not in self.games:
            self.games[cid] = {
                "player1": {
                    "name": event.get_sender_name(),
                    "id": event.get_sender_id(),
                    "hp": 6,
                    "item": [],
                    "handcuff": False
                },
                "status": "waiting",  # 等待阶段
            }
            # 启动一个异步任务，等待玩家2
            asyncio.create_task(self.wait_for_join_timeout(cid, event))

            yield event.plain_result(textwrap.dedent(f"""\
            ══恶魔轮盘══
            游戏创建成功！
            玩家1：{event.get_sender_name()}({event.get_sender_id()})
            玩家2：等待中……

            发送“/恶魔轮盘 加入游戏”加入本游戏。等待超时自动取消。
            """))
        else:
            status = self.games[cid].get("status", "")
            if status == "waiting":
                yield event.plain_result("══恶魔轮盘══\n本群已有游戏正在等待玩家，发送“/恶魔轮盘 加入游戏”即可加入。")
            else:
                yield event.plain_result("══恶魔轮盘══\n本群已有游戏正在进行中，无法重复创建。")

    async def wait_for_join_timeout(self, cid: str, event: AstrMessageEvent):
        """等待玩家2的最大时间，超时自动取消游戏"""
        await asyncio.sleep(self.config["maxWaitTime"])
        if cid in self.games and self.games[cid]["status"] == "waiting":
            del self.games[cid]
            await self.context.send_message(
                event.unified_msg_origin,
                MessageChain().message(f"{event.at_sender()} 等待玩家2超时，游戏已取消。")
            )

    @demon_roulette.command("加入游戏")
    async def join_game(self, event: AstrMessageEvent):
        """
        加入游戏：只能在游戏等待状态下加入，不能自己加入自己创建的游戏。
        """
        cid = self.get_channel_id(event)
        if cid not in self.games:
            yield event.plain_result("══恶魔轮盘══\n当前没有可加入的游戏，请先创建。")
            return

        if self.games[cid]["status"] != "waiting":
            yield event.plain_result("══恶魔轮盘══\n游戏已满或正在进行中。")
            return

        if self.games[cid]["player1"]["id"] == event.get_sender_id():
            yield event.plain_result("══恶魔轮盘══\n你不能加入自己创建的游戏。")
            return

        # 成为 player2
        self.games[cid]["player2"] = {
            "name": event.get_sender_name(),
            "id": event.get_sender_id(),
            "hp": 6,
            "item": [],
            "handcuff": False
        }
        self.games[cid]["status"] = "full"

        yield event.plain_result(textwrap.dedent(f"""\
            ══恶魔轮盘══
            成功加入游戏！
            玩家1：{self.games[cid]['player1']['name']}({self.games[cid]['player1']['id']})
            玩家2：{event.get_sender_name()}({event.get_sender_id()})

            由玩家1发送“/恶魔轮盘 开始游戏”正式开始游戏。
        """))

    @demon_roulette.command("开始游戏")
    async def start_game(self, event: AstrMessageEvent):
        """
        开始游戏：只有玩家1可开始。
        随机生成弹夹，随机先/后手，发放道具。
        """
        cid = self.get_channel_id(event)
        if cid not in self.games:
            yield event.plain_result("══恶魔轮盘══\n没有可开始的游戏，请先创建/加入。")
            return

        if self.games[cid]["status"] != "full":
            yield event.plain_result("══恶魔轮盘══\n本群游戏尚未凑满两人，无法开始。")
            return

        if self.games[cid]["player1"]["id"] != event.get_sender_id():
            yield event.plain_result("══恶魔轮盘══\n只有玩家1才能开始游戏。")
            return

        # 进入游戏
        self.games[cid]["status"] = "started"
        self.games[cid]["bullet"] = generate_random_bullet_list()
        self.games[cid]["currentTurn"] = random.randint(1, 2)
        self.games[cid]["double"] = False
        self.games[cid]["round"] = 0
        self.games[cid]["usedHandcuff"] = False

        # 发放道具
        item_count_base = random.randint(3, 6)
        first_p = f"player{self.games[cid]['currentTurn']}"
        second_p = f"player{1 if self.games[cid]['currentTurn'] == 2 else 2}"

        for _ in range(item_count_base - 1):
            self.games[cid][first_p]["item"].append(random.choice(list(self.item_list.keys())))
        for _ in range(item_count_base):
            self.games[cid][second_p]["item"].append(random.choice(list(self.item_list.keys())))

        bullet_list = self.games[cid]["bullet"]
        yield event.plain_result(textwrap.dedent(f"""\
            ══恶魔轮盘══
            游戏开始！

            玩家1：{self.games[cid]["player1"]["name"]}({self.games[cid]["player1"]["id"]})
            玩家2：{self.games[cid]["player2"]["name"]}({self.games[cid]["player2"]["id"]})

            由 {self.at_id(self.games[cid][first_p]["id"])} 先手。
            先手获得 {item_count_base - 1} 个道具，后手获得 {item_count_base} 个道具。

            当前弹夹共 {len(bullet_list)} 发子弹，其中：
            实弹 {self.count_bullet(bullet_list, "实弹")} 发，空包弹 {self.count_bullet(bullet_list, "空包弹")} 发。

            发送 “/恶魔轮盘 对战信息” 查看当前对战状况。
        """))

    @demon_roulette.command("对战信息")
    async def show_game_info(self, event: AstrMessageEvent):
        """查看当前对战信息（血量、道具等）"""
        cid = self.get_channel_id(event)
        if cid not in self.games or self.games[cid]["status"] != "started":
            yield event.plain_result("══恶魔轮盘══\n本群无正在进行的恶魔轮盘游戏。")
            return

        g = self.games[cid]
        p1 = g["player1"]
        p2 = g["player2"]

        msg = textwrap.dedent(f"""\
            ══恶魔轮盘══
            --血量--
            玩家1({p1["name"]})：{p1["hp"]}/6
            玩家2({p2["name"]})：{p2["hp"]}/6

            --玩家1的道具 ({len(p1["item"])}/8)--
        """)
        msg += "\n".join(f"{it}({self.item_list[it]['description']})" for it in p1["item"])
        msg += textwrap.dedent(f"""\n
            --玩家2的道具 ({len(p2["item"])}/8)--
        """)
        msg += "\n".join(f"{it}({self.item_list[it]['description']})" for it in p2["item"])
        msg += textwrap.dedent(f"""\n
            发送道具名可使用道具；发送“自己”或“对方”可选择向谁开枪。
        """)
        yield event.plain_result(msg)

    @demon_roulette.command("结束游戏")
    async def end_game(self, event: AstrMessageEvent):
        """
        主动结束游戏，可由玩家1/玩家2或管理员执行
        """
        cid = self.get_channel_id(event)
        if cid not in self.games:
            yield event.plain_result("══恶魔轮盘══\n无可结束的游戏。")
            return

        p1_id = self.games[cid]["player1"]["id"]
        p2_id = self.games[cid].get("player2", {}).get("id", "")
        if event.get_sender_id() not in [p1_id, p2_id, *self.config["admin"]]:
            yield event.plain_result("══恶魔轮盘══\n只有游戏参与者或管理员可结束游戏。")
            return

        del self.games[cid]
        yield event.plain_result(f"══恶魔轮盘══\n{self.at_id(event.get_sender_id())} 已强制结束本群游戏。")

    # ----------------------------------------------------------------
    # 在消息层面拦截：如果玩家输入 “自己”/“对方” 或 道具名，执行相应操作
    # ----------------------------------------------------------------
    @filter.on_message()
    async def on_message(self, event: AstrMessageEvent):
        cid = self.get_channel_id(event)
        if cid not in self.games or self.games[cid]["status"] != "started":
            return  # 无游戏或游戏未开始 -> 不处理

        g = self.games[cid]
        cur_player = f"player{g['currentTurn']}"
        if g[cur_player]["id"] != event.get_sender_id():
            return  # 不是当前玩家回合 -> 不处理

        content = event.message_obj.message_str.strip()

        # 开枪：输入 “自己”/“对方”
        if content in ["自己", "对方"]:
            async for msg_ret in self.fire(cid, content, event):
                yield msg_ret
            return

        # 使用道具：如果玩家拥有该道具，触发对应逻辑
        if content in g[cur_player]["item"]:
            async for msg_ret in self.use_item(cid, content, event):
                yield msg_ret

    # ----------------------------------------------------------------
    # 核心函数：开枪 & 使用道具
    # ----------------------------------------------------------------
    async def fire(self, cid: str, target: str, event: AstrMessageEvent):
        game = self.games[cid]
        cur_p = f"player{game['currentTurn']}"
        oth_p = f"player{1 if game['currentTurn'] == 2 else 2}"

        bullet = game["bullet"].pop() if game["bullet"] else None
        if not bullet:
            # 如果子弹已经打空，下一轮
            yield event.plain_result("══恶魔轮盘══\n当前弹夹已空，自动进入下一轮。")
            yield event.plain_result(self.next_round(game))
            return

        text = f"══恶魔轮盘══\n你将枪口对准了【{target}】，扣下扳机……是【{bullet}】\n"

        if bullet == "实弹":
            damage = 2 if game["double"] else 1
            if target == "自己":
                game[cur_p]["hp"] -= damage
                text += f"你损失了 {damage} 点血量。"
                if game[cur_p]["hp"] <= 0:
                    yield event.plain_result(text)
                    # 结束游戏
                    lines = self.game_over(cid, winner=oth_p, loser=cur_p)
                    for ln in lines:
                        yield event.plain_result(ln)
                    return
            else:
                game[oth_p]["hp"] -= damage
                text += f"对方损失了 {damage} 点血量。"
                if game[oth_p]["hp"] <= 0:
                    yield event.plain_result(text)
                    # 结束游戏
                    lines = self.game_over(cid, winner=cur_p, loser=oth_p)
                    for ln in lines:
                        yield event.plain_result(ln)
                    return

        # 空包弹 或 完成射击后
        if bullet == "空包弹" and target == "自己":
            text += "\n接下来仍然是你的回合。"
        else:
            # 是否手铐状态
            if not game[oth_p]["handcuff"]:
                game["currentTurn"] = 1 if game["currentTurn"] == 2 else 2
                new_p = f"player{game['currentTurn']}"
                text += f"\n切换回合：由 {self.at_id(game[new_p]['id'])} 开始行动。"
                game["usedHandcuff"] = False
            else:
                game[oth_p]["handcuff"] = False
                text += f"\n对方被手铐束缚无法行动，依然由你继续。"

        yield event.plain_result(text)
        game["double"] = False

        # 若子弹打空，进入下一轮
        if len(game["bullet"]) == 0:
            yield event.plain_result(self.next_round(game))

    def next_round(self, game: dict):
        """
        进入下一轮：重新随机生成弹夹，双方各获得若干道具。
        """
        game["round"] += 1
        game["bullet"] = generate_random_bullet_list()
        bullet_list = game["bullet"]

        item_pool = list(self.item_list.keys())
        item_count = random.randint(2, 5)
        cur_p = f"player{game['currentTurn']}"
        oth_p = f"player{1 if game['currentTurn'] == 2 else 2}"

        for _ in range(item_count):
            game[cur_p]["item"].append(random.choice(item_pool))
            game[oth_p]["item"].append(random.choice(item_pool))

        # 裁剪道具至上限 8
        game["player1"]["item"] = game["player1"]["item"][:8]
        game["player2"]["item"] = game["player2"]["item"][:8]

        msg = textwrap.dedent(f"""\
            ══恶魔轮盘══
            弹夹打空，进入第 {game["round"]} 轮！
            新弹夹共 {len(bullet_list)} 发子弹，
            其中实弹 {self.count_bullet(bullet_list, "实弹")} 发，空包弹 {self.count_bullet(bullet_list, "空包弹")} 发。
            双方各获得 {item_count} 个随机道具（上限 8）。
        """)
        return msg

    async def use_item(self, cid: str, item: str, event: AstrMessageEvent):
        """
        使用道具。如果是肾上腺素，需要先询问要使用的对方道具名。
        """
        game = self.games[cid]
        cur_p = f"player{game['currentTurn']}"

        # 肾上腺素需要额外交互
        if item == "肾上腺素":
            await event.plain_result("你使用了肾上腺素，请在 30 秒内输入一个想让对方立刻使用的道具名：")
            try:
                pick_item = await self.context.prompt(event.unified_msg_origin, timeout=30)
            except asyncio.TimeoutError:
                yield "操作超时，已取消使用肾上腺素。"
                return

            if not pick_item:
                yield "未输入道具名，操作取消。"
                return
            other_p = f"player{1 if game['currentTurn'] == 2 else 2}"
            if pick_item == "肾上腺素":
                yield "不能选择对方的肾上腺素，操作取消。"
                return
            if pick_item not in game[other_p]["item"]:
                yield f"对方没有【{pick_item}】道具，操作取消。"
                return
            # 执行肾上腺素效果
            lines = await self.item_list[item]["use"](self, cid, cur_p, pick_item, event)
            for ln in lines:
                yield ln
        else:
            lines = await self.item_list[item]["use"](self, cid, cur_p, None, event)
            for ln in lines:
                yield ln

        # 使用完毕 -> 移除道具
        if item in game[cur_p]["item"]:
            game[cur_p]["item"].remove(item)

    # ----------------------------------------------------------------
    # 道具的实际实现：返回字符串列表
    # ----------------------------------------------------------------

    @staticmethod
    async def use_saw(plugin, cid, cur_player, pick, event):
        """手锯：下一发造成双倍伤害，不可叠加"""
        g = plugin.games[cid]
        g["double"] = True
        return ["手锯效果激活：下一发造成双倍伤害。"]

    @staticmethod
    async def use_magnifier(plugin, cid, cur_player, pick, event):
        """放大镜：查看当前膛内的子弹"""
        g = plugin.games[cid]
        if not g["bullet"]:
            return ["当前枪膛已空。"]
        return [f"你使用了放大镜，看到了最后一发是【{g['bullet'][-1]}】"]

    @staticmethod
    async def use_beer(plugin, cid, cur_player, pick, event):
        """啤酒：卸下当前膛内的一发子弹"""
        g = plugin.games[cid]
        if not g["bullet"]:
            return ["枪膛已空，无子弹可卸。"]
        bullet = g["bullet"].pop()
        msg = [f"你喝下啤酒，将一发【{bullet}】抛出膛外。"]
        if len(g["bullet"]) == 0:
            msg.append(plugin.next_round(g))
        return msg

    @staticmethod
    async def use_cigarette(plugin, cid, cur_player, pick, event):
        """香烟：恢复1点生命值（最多6点）"""
        g = plugin.games[cid]
        if g[cur_player]["hp"] < 6:
            g[cur_player]["hp"] += 1
            return ["你抽了一根香烟，恢复 1 点血量。"]
        else:
            return ["你已经满血，香烟没有额外效果。"]

    @staticmethod
    async def use_handcuff(plugin, cid, cur_player, pick, event):
        """手铐：跳过对方下回合"""
        g = plugin.games[cid]
        if g.get("usedHandcuff", False):
            return ["本回合已使用过手铐，无法再次使用。"]
        other_p = f"player{1 if g['currentTurn'] == 2 else 2}"
        g[other_p]["handcuff"] = True
        g["usedHandcuff"] = True
        return ["你给对方戴上手铐，对方的下一回合将被跳过。"]

    @staticmethod
    async def use_epinephrine(plugin, cid, cur_player, pick_item, event):
        """
        肾上腺素：让对方立即使用 pick_item 道具
        """
        g = plugin.games[cid]
        other_p = f"player{1 if g['currentTurn'] == 2 else 2}"
        # 执行对方道具
        msgs_sub = await plugin.item_list[pick_item]["use"](plugin, cid, other_p, None, event)
        # 对方失去该道具
        if pick_item in g[other_p]["item"]:
            g[other_p]["item"].remove(pick_item)

        return [f"你给自己注射了肾上腺素，对方立即使用了【{pick_item}】↓"] + msgs_sub

    @staticmethod
    async def use_expired_medicine(plugin, cid, cur_player, pick, event):
        """过期药物：50%几率 +2 血；50%几率 -1 血(可能导致自己死亡)"""
        g = plugin.games[cid]
        if random.random() < 0.5:
            recover = min(6 - g[cur_player]["hp"], 2)
            g[cur_player]["hp"] += recover
            return [f"你服下过期药物，意外恢复了 {recover} 点血量。"]
        else:
            g[cur_player]["hp"] -= 1
            if g[cur_player]["hp"] <= 0:
                # 自己倒下
                other_p = f"player{1 if g['currentTurn'] == 2 else 2}"
                msg = textwrap.dedent(f"""\
                    你服下过期药物，感觉身体剧烈不适，直接倒下……
                    {plugin.at_id(g[other_p]['id'])} 获得了胜利！
                """)
                # 先发送倒下消息
                await event.plain_result(msg)
                # 然后结束游戏
                lines = plugin.game_over(cid, winner=other_p, loser=cur_player)
                for ln in lines:
                    await event.plain_result(ln)
                return []
            else:
                return ["你服下过期药物，感觉不妙，损失 1 点血量。"]

    @staticmethod
    async def use_reverser(plugin, cid, cur_player, pick, event):
        """逆转器：将当前膛内最后一发子弹 实弹⇔空包弹"""
        g = plugin.games[cid]
        if not g["bullet"]:
            return ["当前枪膛已空。"]
        old_bullet = g["bullet"].pop()
        new_bullet = "空包弹" if old_bullet == "实弹" else "实弹"
        g["bullet"].append(new_bullet)
        return [f"你使用逆转器，将【{old_bullet}】转换成【{new_bullet}】。"]

    @staticmethod
    async def use_once_phone(plugin, cid, cur_player, pick, event):
        """
        一次性电话：根据当前枪内子弹总数，随机提示其中某一发的类型。不移除子弹。
        """
        g = plugin.games[cid]
        bullet_count = len(g["bullet"])
        if bullet_count == 0:
            return ["当前枪膛已空，电话也无法帮你。"]
        idx = random.randint(0, bullet_count - 1)
        bullet_type = g["bullet"][idx]
        return [f"你使用了一次性电话，对方神秘地告诉你：第 {idx + 1} 发是【{bullet_type}】。"]

    # ----------------------------------------------------------------
    # 游戏结束：改为返回字符串列表，而非生成器
    # ----------------------------------------------------------------
    def game_over(self, cid: str, winner: str, loser: str):
        """
        结束游戏并宣告胜者。这里返回一个字符串列表，在外部 async 函数里再逐条发送。
        """
        g = self.games[cid]
        w_id = g[winner]["id"]
        l_id = g[loser]["id"]
        text = textwrap.dedent(f"""\
            ══恶魔轮盘══
            {self.at_id(l_id)} 倒下了！
            {self.at_id(w_id)} 获得了最终胜利！
            游戏结束！
        """)
        del self.games[cid]
        return [text]

    # ----------------------------------------------------------------
    # 辅助函数
    # ----------------------------------------------------------------
    def count_bullet(self, bullet_list, key):
        return sum(1 for b in bullet_list if b == key)

    def at_id(self, user_id: str) -> str:
        """
        简易示例，返回一个类似 @xxxx 的消息段。
        实际可根据平台适配器，使用 event.at_sender() 等方法。
        """
        return f"[CQ:at,qq={user_id}]"
