# main.py
from astrbot.api.all import *  # 导入所有API
import asyncio
import textwrap
import random

def generate_random_bullet_list():
    """
    随机生成一个弹夹列表：
      - 子弹数量在 3 ~ 8 之间
      - 每发子弹为 "实弹" 或 "空包弹"（各50%概率）
      - 最后洗牌后返回
    """
    bullet_count = random.randint(3, 8)
    bullets = []
    for _ in range(bullet_count):
        bullets.append("实弹" if random.random() < 0.5 else "空包弹")
    random.shuffle(bullets)
    return bullets

@register(
    "astrbot_plugin_buckshot_roulette",  # 插件唯一识别名
    "w33d",                              # 作者
    "恶魔轮盘 - Buckshot Roulette",       # 简短描述
    "1.1.1"                              # 版本号
)
class BuckshotRoulette(Star):
    """
    AstrBot 恶魔轮盘游戏插件。
    支持在群聊中进行2人对战，利用随机子弹与多种道具决胜负，
    并提供商店兑换及管理员调试命令，增强趣味性和可控性。
    """

    def __init__(self, context: Context, config: dict = None):
        """
        :param context: AstrBot 传入的 Context 对象
        :param config: (可选) 从 _conf_schema.json 读取的配置
        """
        super().__init__(context)
        if not config:
            config = {}
        self.config = {
            "admin": config.get("admin", []),             # 管理员列表（ID列表）
            "maxWaitTime": config.get("maxWaitTime", 180)   # 等待玩家2加入的最大秒数
        }
        self.games = {}  # 存储各群/会话的游戏数据

        # 定义可用道具（已移除肾上腺素，新增加炸弹、幸运星、护盾）
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
                "description": "让对方跳过下一回合",
                "use": self.use_handcuff,
            },
            "过期药物": {
                "description": "50%几率恢复2血；50%几率损失1血",
                "use": self.use_expired_medicine,
            },
            "逆转器": {
                "description": "将最后一发子弹类型反转",
                "use": self.use_reverser,
            },
            "一次性电话": {
                "description": "随机告知枪膛中某发子弹的类型（不移除）",
                "use": self.use_once_phone,
            },
            "炸弹": {
                "description": "投掷后对对手造成2点伤害（若对方有护盾则抵消）",
                "use": self.use_zhandan,
            },
            "幸运星": {
                "description": "随机获得血量恢复或额外道具",
                "use": self.use_xingyunxing,
            },
            "护盾": {
                "description": "获得护盾效果，下一次攻击伤害将被抵消",
                "use": self.use_hudun,
            },
        }

    def get_channel_id(self, event: AstrMessageEvent) -> str:
        """
        获取唯一群聊ID（或session_id）。
        优先返回群ID；若为私聊，则返回session_id。
        """
        gid = event.get_group_id()
        if gid:
            return gid
        return event.session_id

    # ------------- 游戏基本指令 -------------
    @command_group("恶魔轮盘")
    def demon_roulette(self):
        """恶魔轮盘游戏主指令组"""
        pass

    @demon_roulette.command("创建游戏")
    async def create_game(self, event: AstrMessageEvent):
        """
        创建游戏：当本群中没有正在进行的游戏时可创建，
        创建后等待另一名玩家加入，超时自动取消。
        """
        cid = self.get_channel_id(event)
        if cid not in self.games:
            self.games[cid] = {
                "player1": {
                    "name": event.get_sender_name(),
                    "id": event.get_sender_id(),
                    "hp": 6,
                    "item": [],
                    "handcuff": False,
                    "shield": False
                },
                "status": "waiting",
            }
            asyncio.create_task(self.wait_for_join_timeout(cid, event))
            yield event.plain_result(textwrap.dedent(f""" ══恶魔轮盘══\n游戏创建成功！\n玩家1：{event.get_sender_name()} ({event.get_sender_id()})\n玩家2：正在等待中……\n\n请发送“/恶魔轮盘 加入游戏”加入本游戏，超时后将自动取消！
            """))
        else:
            status = self.games[cid].get("status", "")
            if status == "waiting":
                yield event.plain_result("══恶魔轮盘══\n本群已有游戏在等待玩家，请发送“/恶魔轮盘 加入游戏”加入。")
            else:
                yield event.plain_result("══恶魔轮盘══\n当前群中已有游戏正在进行，无法重复创建。")

    async def wait_for_join_timeout(self, cid: str, event: AstrMessageEvent):
        """等待玩家2加入，超时则自动取消游戏"""
        await asyncio.sleep(self.config["maxWaitTime"])
        if cid in self.games and self.games[cid]["status"] == "waiting":
            del self.games[cid]
            await self.context.send_message(
                event.unified_msg_origin,
                MessageChain().message(f"{event.at_sender()}，等待玩家2超时，游戏已取消。")
            )

    @demon_roulette.command("加入游戏")
    async def join_game(self, event: AstrMessageEvent):
        """
        加入游戏：仅当游戏处于等待状态时可加入，
        且你不能加入自己创建的游戏。
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
        self.games[cid]["player2"] = {
            "name": event.get_sender_name(),
            "id": event.get_sender_id(),
            "hp": 6,
            "item": [],
            "handcuff": False,
            "shield": False
        }
        self.games[cid]["status"] = "full"
        yield event.plain_result(textwrap.dedent(f"""\ 
            ══恶魔轮盘══\n成功加入游戏！\n玩家1：{self.games[cid]['player1']['name']} ({self.games[cid]['player1']['id']})\n玩家2：{event.get_sender_name()} ({event.get_sender_id()})\n\n请由玩家1发送“/恶魔轮盘 开始游戏”以正式开始对战！
            """))

    @demon_roulette.command("开始游戏")
    async def start_game(self, event: AstrMessageEvent):
        """
        开始游戏：仅允许游戏创建者（玩家1）操作，
        系统将随机生成弹夹、随机决定先后手，并为双方发放随机道具。
        """
        cid = self.get_channel_id(event)
        if cid not in self.games:
            yield event.plain_result("══恶魔轮盘══\n没有可开始的游戏，请先创建或加入。")
            return
        if self.games[cid]["status"] != "full":
            yield event.plain_result("══恶魔轮盘══\n游戏尚未凑满两人，无法开始。")
            return
        if self.games[cid]["player1"]["id"] != event.get_sender_id():
            yield event.plain_result("══恶魔轮盘══\n只有游戏创建者（玩家1）才能开始游戏。")
            return
        self.games[cid]["status"] = "started"
        self.games[cid]["bullet"] = generate_random_bullet_list()
        self.games[cid]["currentTurn"] = random.randint(1, 2)
        self.games[cid]["double"] = False
        self.games[cid]["round"] = 0
        self.games[cid]["usedHandcuff"] = False

        first_p = f"player{self.games[cid]['currentTurn']}"
        second_p = f"player{1 if self.games[cid]['currentTurn'] == 2 else 2}"
        item_count_base = random.randint(3, 6)
        for _ in range(item_count_base - 1):
            self.games[cid][first_p]["item"].append(random.choice(list(self.item_list.keys())))
        for _ in range(item_count_base):
            self.games[cid][second_p]["item"].append(random.choice(list(self.item_list.keys())))
        bullet_list = self.games[cid]["bullet"]
        yield event.plain_result(textwrap.dedent(f"""\ 
            ══恶魔轮盘══\n游戏开始!\n玩家1：{self.games[cid]["player1"]["name"]} ({self.games[cid]["player1"]["id"]})\n玩家2：{self.games[cid]["player2"]["name"]} ({self.games[cid]["player2"]["id"]})\n由 {self.at_id(self.games[cid][first_p]["name"])} 先手!\n先手获得 {item_count_base - 1} 个道具，后手获得 {item_count_base} 个道具.\n当前弹夹中共有 {len(bullet_list)} 发子弹,\n其中实弹 {self.count_bullet(bullet_list, "实弹")} 发, 空包弹 {self.count_bullet(bullet_list, "空包弹")} 发.\n请发送“/恶魔轮盘 对战信息”查看详细情况，祝你好运!
        """))

    @demon_roulette.command("对战信息")
    async def show_game_info(self, event: AstrMessageEvent):
        """
        查看对战信息：显示双方当前血量和持有的道具情况。
        """
        cid = self.get_channel_id(event)
        if cid not in self.games or self.games[cid]["status"] != "started":
            yield event.plain_result("══恶魔轮盘══\n当前没有正在进行的游戏。")
            return
        g = self.games[cid]
        p1 = g["player1"]
        p2 = g["player2"]
        msg = textwrap.dedent(f"""\ 
            ══恶魔轮盘══\n-- 血量状况 --\n玩家1 ({p1["name"]})：{p1["hp"]}/6\n玩家2 ({p2["name"]})：{p2["hp"]}/6

            -- 玩家1的道具 ({len(p1["item"])}/8) --
        """)
        msg += "\n".join(f"{it} ({self.item_list[it]['description']})" for it in p1["item"])
        msg += textwrap.dedent(f"""\n
            -- 玩家2的道具 ({len(p2["item"])}/8) --
        """)
        msg += "\n".join(f"{it} ({self.item_list[it]['description']})" for it in p2["item"])
        msg += textwrap.dedent(f"""\n
            请发送道具名以使用对应道具,
            或发送“自己” / “对方” 来开枪!
        """)
        yield event.plain_result(msg)

    @demon_roulette.command("结束游戏")
    async def end_game(self, event: AstrMessageEvent):
        """
        结束游戏：允许游戏参与者或管理员主动结束当前游戏。
        """
        cid = self.get_channel_id(event)
        if cid not in self.games:
            yield event.plain_result("══恶魔轮盘══\n当前没有可结束的游戏。")
            return
        p1_id = self.games[cid]["player1"]["id"]
        p2_id = self.games[cid].get("player2", {}).get("id", "")
        if event.get_sender_id() not in [p1_id, p2_id, *self.config["admin"]]:
            yield event.plain_result("══恶魔轮盘══\n只有游戏参与者或管理员可以结束游戏。")
            return
        del self.games[cid]
        yield event.plain_result(f"══恶魔轮盘══\n{self.at_id(event.get_sender_name())} 已强制结束当前游戏。")

    # ------------- 商店兑换功能 -------------
    @demon_roulette.command("兑换")
    async def exchange_item(self, event: AstrMessageEvent, source: str, target: str):
        """
        兑换道具：如果你拥有2个相同的【source】道具，则可兑换为1个【target】道具。
        允许兑换规则：
          香烟：可兑换为 手锯、放大镜、炸弹、幸运星、护盾
          啤酒：可兑换为 手铐、护盾
          手锯：可兑换为 逆转器
          放大镜：可兑换为 一次性电话
        """
        allowed_exchanges = {
            "香烟": ["手锯", "放大镜", "炸弹", "幸运星", "护盾"],
            "啤酒": ["手铐", "护盾"],
            "手锯": ["逆转器"],
            "放大镜": ["一次性电话"],
        }
        cid = self.get_channel_id(event)
        if cid not in self.games or self.games[cid]["status"] != "started":
            yield event.plain_result("当前没有正在进行的游戏。")
            return
        if source not in allowed_exchanges or target not in allowed_exchanges[source]:
            yield event.plain_result(f"【{source}】无法兑换成【{target}】。")
            return
        game = self.games[cid]
        cur_player = f"player{game['currentTurn']}"
        if game[cur_player]["item"].count(source) < 2:
            yield event.plain_result(f"你没有足够的【{source}】进行兑换（需要2个）。")
            return
        for _ in range(2):
            game[cur_player]["item"].remove(source)
        game[cur_player]["item"].append(target)
        yield event.plain_result(f"兑换成功：2个【{source}】已兑换为1个【{target}】！")

    # ------------- Debug 模式（仅管理员可用） -------------
    @demon_roulette.group("debug")
    def debug(self):
        """Debug模式：仅限管理员使用，用于给玩家道具、修改血量、查询状态等"""
        pass

    @debug.command("给道具")
    async def debug_give_item(self, event: AstrMessageEvent, target: str, item: str, quantity: int):
        if event.get_sender_id() not in self.config["admin"]:
            yield event.plain_result("权限不足！")
            return
        cid = self.get_channel_id(event)
        if cid not in self.games:
            yield event.plain_result("当前群中没有游戏。")
            return
        game = self.games[cid]
        player = None
        if game["player1"]["id"] == target:
            player = game["player1"]
        elif "player2" in game and game["player2"]["id"] == target:
            player = game["player2"]
        if not player:
            yield event.plain_result("指定的玩家不在当前游戏中。")
            return
        for _ in range(quantity):
            player["item"].append(item)
        yield event.plain_result(f"已给玩家 {player['name']} 添加了 {quantity} 个【{item}】。")

    @debug.command("修改血量")
    async def debug_set_hp(self, event: AstrMessageEvent, target: str, hp: int):
        if event.get_sender_id() not in self.config["admin"]:
            yield event.plain_result("权限不足！")
            return
        cid = self.get_channel_id(event)
        if cid not in self.games:
            yield event.plain_result("当前群中没有游戏。")
            return
        game = self.games[cid]
        player = None
        if game["player1"]["id"] == target:
            player = game["player1"]
        elif "player2" in game and game["player2"]["id"] == target:
            player = game["player2"]
        if not player:
            yield event.plain_result("指定的玩家不在当前游戏中。")
            return
        player["hp"] = hp
        yield event.plain_result(f"已将玩家 {player['name']} 的血量设置为 {hp}。")

    @debug.command("查询子弹")
    async def debug_query_bullet(self, event: AstrMessageEvent):
        if event.get_sender_id() not in self.config["admin"]:
            yield event.plain_result("权限不足！")
            return
        cid = self.get_channel_id(event)
        if cid not in self.games:
            yield event.plain_result("当前群中没有游戏。")
            return
        bullet_list = self.games[cid].get("bullet", [])
        yield event.plain_result(f"当前弹夹：{bullet_list}")

    @debug.command("查询游戏")
    async def debug_query_game(self, event: AstrMessageEvent):
        if event.get_sender_id() not in self.config["admin"]:
            yield event.plain_result("权限不足！")
            return
        cid = self.get_channel_id(event)
        if cid not in self.games:
            yield event.plain_result("当前群中没有游戏。")
            return
        yield event.plain_result(f"当前游戏数据：{self.games[cid]}")

    # ------------- 消息监听 -------------
    @event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """
        监听消息：如果游戏正在进行且处于当前玩家回合，
        则判断玩家是否选择了“自己”或“对方”开枪，或使用道具。
        """
        cid = self.get_channel_id(event)
        if cid not in self.games or self.games[cid]["status"] != "started":
            return
        g = self.games[cid]
        cur_player = f"player{g['currentTurn']}"
        if g[cur_player]["id"] != event.get_sender_id():
            return
        content = event.message_obj.message_str.strip()
        if content in ["自己", "对方"]:
            async for msg_ret in self.fire(cid, content, event):
                yield msg_ret
            return
        if content in g[cur_player]["item"]:
            async for msg_ret in self.use_item(cid, content, event):
                yield msg_ret

    # ------------- 核心函数：开枪 -------------
    async def fire(self, cid: str, target: str, event: AstrMessageEvent):
        """
        开枪逻辑：根据枪膛中子弹的类型计算伤害、切换回合或结束游戏，
        并反馈详细情景描述。
        """
        game = self.games[cid]
        cur_p = f"player{game['currentTurn']}"
        oth_p = f"player{1 if game['currentTurn'] == 2 else 2}"
        bullet = game["bullet"].pop() if game["bullet"] else None
        if not bullet:
            yield event.plain_result("══恶魔轮盘══\n当前弹夹已空，自动进入下一轮。")
            yield event.plain_result(self.next_round(game))
            return
        text = f"══恶魔轮盘══\n你将枪口对准了【{target}】，扣下扳机……结果是【{bullet}】\n"
        if bullet == "实弹":
            damage = 2 if game["double"] else 1
            if target == "自己":
                game[cur_p]["hp"] -= damage
                text += f"你遭受猛烈反噬，损失了 {damage} 点血量！"
                if game[cur_p]["hp"] <= 0:
                    yield event.plain_result(text)
                    lines = self.game_over(cid, winner=oth_p, loser=cur_p)
                    for ln in lines:
                        yield event.plain_result(ln)
                    return
            else:
                if game[oth_p].get("shield", False):
                    text += "但对方的护盾闪耀，将伤害全部吸收！"
                    game[oth_p]["shield"] = False
                else:
                    game[oth_p]["hp"] -= damage
                    text += f"对方被你狠狠击中，损失了 {damage} 点血量！"
                    if game[oth_p]["hp"] <= 0:
                        yield event.plain_result(text)
                        lines = self.game_over(cid, winner=cur_p, loser=oth_p)
                        for ln in lines:
                            yield event.plain_result(ln)
                        return
        if bullet == "空包弹" and target == "自己":
            text += "\n幸好只是空包弹，你仍保有行动权！"
        else:
            if not game[oth_p]["handcuff"]:
                game["currentTurn"] = 1 if game["currentTurn"] == 2 else 2
                new_p = f"player{game['currentTurn']}"
                text += f"\n切换回合：现在由 {self.at_id(game[new_p]['name'])} 决定下一步！"
                game["usedHandcuff"] = False
            else:
                game[oth_p]["handcuff"] = False
                text += "\n对方被手铐束缚，无法反击，你继续掌控全局！"
        yield event.plain_result(text)
        game["double"] = False
        if len(game["bullet"]) == 0:
            yield event.plain_result(self.next_round(game))

    def next_round(self, game: dict):
        """
        进入下一轮：重新生成弹夹，并为双方发放新的随机道具。
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
        game["player1"]["item"] = game["player1"]["item"][:8]
        game["player2"]["item"] = game["player2"]["item"][:8]
        msg = textwrap.dedent(f"""\ 
            ══恶魔轮盘══
            弹夹打空，进入第 {game["round"]} 轮！
            新弹夹中共有 {len(bullet_list)} 发子弹，
            其中实弹 {self.count_bullet(bullet_list, "实弹")} 发, 空包弹 {self.count_bullet(bullet_list, "空包弹")} 发.
            双方各获得 {item_count} 个随机道具（上限 8）。
        """)
        return msg

    async def use_item(self, cid: str, item: str, event: AstrMessageEvent):
        """
        使用道具：调用对应道具效果，并将反馈信息发送到聊天，
        使用成功后从玩家背包中移除该道具。
        """
        game = self.games[cid]
        cur_p = f"player{game['currentTurn']}"
        yield event.plain_result(f"你尝试使用【{item}】道具……")
        lines = await self.item_list[item]["use"](self, cid, cur_p, None, event)
        for ln in lines:
            yield event.plain_result(ln)
        if item in game[cur_p]["item"]:
            game[cur_p]["item"].remove(item)
            yield event.plain_result(f"【{item}】已从你的背包中移除，希望这能助你一臂之力！")

    # ------------- 各道具具体实现 -------------
    @staticmethod
    async def use_saw(plugin, cid, cur_player, pick, event):
        """手锯：下一发造成双倍伤害，不可叠加"""
        g = plugin.games[cid]
        g["double"] = True
        return [
            "你小心翼翼地取出手锯，锯短了枪管……",
            "【手锯】效果启动：下一发子弹伤害翻倍！"
        ]

    @staticmethod
    async def use_magnifier(plugin, cid, cur_player, pick, event):
        """放大镜：查看当前膛内的子弹"""
        g = plugin.games[cid]
        if not g["bullet"]:
            return ["你拿着放大镜仔细查看，发现枪膛中已无子弹。"]
        bullet_type = g["bullet"][-1]
        return [
            "你取出放大镜，凑近枪膛仔细观察……",
            f"发现下一发子弹是【{bullet_type}】！"
        ]

    @staticmethod
    async def use_beer(plugin, cid, cur_player, pick, event):
        """啤酒：卸下当前膛内的一发子弹"""
        g = plugin.games[cid]
        if not g["bullet"]:
            return ["你试图用啤酒卸下子弹，但枪膛已空。"]
        bullet = g["bullet"].pop()
        msg = [
            "你大口喝下冰镇啤酒，猛然敲击枪膛……",
            f"“叮”地一声，一发【{bullet}】弹飞而出！"
        ]
        if len(g["bullet"]) == 0:
            msg.append(plugin.next_round(g))
        return msg

    @staticmethod
    async def use_cigarette(plugin, cid, cur_player, pick, event):
        """香烟：恢复1点生命值（最多6点）"""
        g = plugin.games[cid]
        if g[cur_player]["hp"] < 6:
            g[cur_player]["hp"] += 1
            return [
                "你点燃一根香烟，缓缓吸入袅袅烟雾……",
                "感觉紧张得以缓解，恢复了 1 点血量！"
            ]
        else:
            return [
                "你点燃香烟，但发现自己已满血，",
                "不过这也让你稍微放松了一下。"
            ]

    @staticmethod
    async def use_handcuff(plugin, cid, cur_player, pick, event):
        """手铐：让对方跳过下一回合"""
        g = plugin.games[cid]
        if g.get("usedHandcuff", False):
            return ["你试图再次使用手铐，但本回合已使用，请冷静。"]
        other_p = f"player{1 if g['currentTurn'] == 2 else 2}"
        g[other_p]["handcuff"] = True
        g["usedHandcuff"] = True
        return [
            "你迅速掏出手铐，瞬间锁住了对方双手……",
            "对方下一回合将被迫放弃行动！"
        ]

    @staticmethod
    async def use_expired_medicine(plugin, cid, cur_player, pick, event):
        """过期药物：50%几率恢复2点血；50%几率损失1点血（可能导致自己死亡）"""
        g = plugin.games[cid]
        if random.random() < 0.5:
            recover = min(6 - g[cur_player]["hp"], 2)
            g[cur_player]["hp"] += recover
            return [
                "你从口袋中摸出一瓶泛黄药剂，毫不犹豫地服下……",
                f"顿时感觉体内充满温暖，恢复了 {recover} 点血量！"
            ]
        else:
            g[cur_player]["hp"] -= 1
            if g[cur_player]["hp"] <= 0:
                other_p = f"player{1 if g['currentTurn'] == 2 else 2}"
                msg = textwrap.dedent(f"""\
                    你吞下药剂后，胃中剧痛难忍……
                    眼前一黑，你彻底倒下。
                    
                    {plugin.at_id(g[other_p]["name"])} 获得了最终胜利！
                """)
                await event.plain_result(msg)
                lines = plugin.game_over(cid, winner=other_p, loser=cur_player)
                for ln in lines:
                    await event.plain_result(ln)
                return []
            else:
                return [
                    "你盲目服下药剂，突然感到胃中一阵绞痛……",
                    "遗憾地损失了 1 点血量。"
                ]

    @staticmethod
    async def use_reverser(plugin, cid, cur_player, pick, event):
        """逆转器：将当前膛内最后一发子弹的类型进行反转"""
        g = plugin.games[cid]
        if not g["bullet"]:
            return ["你轻抚逆转器，却发现枪膛中无子弹可逆转。"]
        old_bullet = g["bullet"].pop()
        new_bullet = "空包弹" if old_bullet == "实弹" else "实弹"
        g["bullet"].append(new_bullet)
        return [
            "你拿起那闪烁着神秘光芒的逆转器，轻按一下……",
            f"原本的【{old_bullet}】瞬间变为【{new_bullet}】！"
        ]

    @staticmethod
    async def use_once_phone(plugin, cid, cur_player, pick, event):
        """
        一次性电话：随机告知枪膛中某发子弹的类型，但不移除该子弹。
        说明：子弹列表以先进后出顺序发射，因此列表末尾为下一发（显示为第一发）。
        """
        g = plugin.games[cid]
        bullet_count = len(g["bullet"])
        if bullet_count == 0:
            return ["你拿起神秘电话，却发现枪膛中空空如也……"]
        idx = random.randint(0, bullet_count - 1)
        firing_order = bullet_count - idx  # 列表末尾为第一发
        bullet_type = g["bullet"][idx]
        return [
            "你拨通了一次性电话，耳边响起低沉电子声……",
            f"“秘密告诉你，第 {firing_order} 发子弹竟是【{bullet_type}】！”"
        ]

    @staticmethod
    async def use_zhandan(plugin, cid, cur_player, pick, event):
        """炸弹：投掷后对对手造成2点伤害（若对方有护盾则抵消）"""
        g = plugin.games[cid]
        oth_p = f"player{1 if g['currentTurn'] == 2 else 2}"
        if g[oth_p].get("shield", False):
            g[oth_p]["shield"] = False
            return ["你投掷炸弹，但对方的护盾闪耀，将爆炸伤害全部抵消！"]
        else:
            g[oth_p]["hp"] -= 2
            return ["你果断投掷炸弹，对方受到猛烈爆炸冲击，损失了 2 点血量！"]

    @staticmethod
    async def use_xingyunxing(plugin, cid, cur_player, pick, event):
        """幸运星：随机获得血量恢复或额外道具"""
        g = plugin.games[cid]
        if random.random() < 0.5:
            if g[cur_player]["hp"] < 6:
                g[cur_player]["hp"] += 1
                return ["幸运星闪耀，你感觉体内充满力量，血量增加了 1 点！"]
            else:
                return ["幸运星闪烁，但你已满血，效果无效。"]
        else:
            new_item = random.choice(list(plugin.item_list.keys()))
            g[cur_player]["item"].append(new_item)
            return [f"幸运星降临，你意外获得了额外道具【{new_item}】！"]

    @staticmethod
    async def use_hudun(plugin, cid, cur_player, pick, event):
        """护盾：获得护盾效果，下一次受到攻击时自动抵消伤害"""
        g = plugin.games[cid]
        g[cur_player]["shield"] = True
        return ["你装备了护盾，下一次受到攻击时将自动抵消伤害！"]

    # ------------- 游戏结束及辅助函数 -------------
    def game_over(self, cid: str, winner: str, loser: str):
        """
        宣告胜者并删除当前游戏数据。
        """
        g = self.games[cid]
        winner_name = g[winner]["name"]
        loser_name = g[loser]["name"]
        text = textwrap.dedent(f"""\
            ══恶魔轮盘══
            {self.at_id(loser_name)} 倒下了！
            {self.at_id(winner_name)} 获得了最终胜利！
            游戏正式结束，感谢参与，期待下次再战！
        """)
        del self.games[cid]
        return [text]

    def count_bullet(self, bullet_list, key):
        """统计列表中指定类型子弹的数量"""
        return sum(1 for b in bullet_list if b == key)

    def at_id(self, nickname: str) -> str:
        """
        返回适用于当前平台的@消息格式。
        若为微信平台（例如 adapter_name 包含 "wechat" 或 "gewechat"），则直接返回 "@{nickname}"，
        否则返回 QQ 的 CQ 码格式，例如 "[CQ:at,qq={nickname}]"。
        """
        return f"@{nickname}"
