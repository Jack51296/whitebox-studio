"""Human-readable artifacts derived from scene.json.

Control-layer prompt and V2V render prompt ([D2]: one-to-one per white model), director card,
continuation prompt and VTT cues (formats mirror the team's whitebox-world-studio delivery package).
Era only affects the V2V world description, never the white-model geometry ([D2]).
"""

from __future__ import annotations

import math
from typing import Any

from ..blender import kinematics as K
from ..camera_language import label
from ..prompts import render

HEAD_LABEL = {"sphere": "球形头", "cube": "方块头", "octahedron": "八面体头", "capsule": "胶囊头"}
BODY_LABEL = {"capsule": "胶囊身体", "box": "方柱身体", "cylinder": "圆柱身体", "cone": "圆锥身体", "taper": "梯台身体",
              "ellipsoid": "椭球身体", "bipyramid": "双锥身体"}
KIND_LABEL = {"pawn": "棋子式人偶（{head}+{body}，无四肢）", "block_animal": "四足动物体块（躯干+头+四个柱状腿块，整体刚性）",
              "block_bird": "鸟形体块（梭形躯干+两片薄板翅膀）", "block_fish": "鱼形体块（梭形躯干+尾鳍板）",
              "vehicle": "载具体块（车身+舱体+四个轮柱）", "robot": "机器人体块（方形头+方柱躯干，无四肢）",
              "prop": "产品体块（圆角方盒）", "fbx": "标准人体白模（FBX）"}
REAL_SUBJECT = {"pawn": "成年人", "block_animal": "四足陆生动物（按体块比例，如狼或鹿）", "block_bird": "飞鸟",
                "block_fish": "鱼", "vehicle": "汽车", "robot": "人形机器人", "prop": "产品", "fbx": "成年人"}
COLOR_NAME = {"#d9363e": "红色", "#2f6fdb": "蓝色", "#e0a800": "黄色", "#2e9e5b": "绿色", "#8e44ad": "紫色",
              "#e67e22": "橙色"}
ENV_REAL = {
    "plaza": "开阔广场；方块和圆柱障碍转化为与时代相符的设施（台座、货箱、雕塑底座等），终点门形体块转化为入口门廊或拱门。",
    "street": "城市街道；两侧长方体楼群转化为与时代相符的沿街建筑，保留路沿与巷口。",
    "rooms": "连续相通的室内房间；墙体、门洞与家具体块转化为与时代相符的室内装修和家具。",
    "towers": "高层建筑群；塔楼与连桥转化为与时代相符的高层建筑和空中连廊。",
    "canyon": "自然峡谷；谷壁转化为岩壁，石柱转化为石柱或巨树，地面转化为真实地形。",
    "seabed": "海底；礁石与海草体块转化为真实的礁石与海草，水体通透。",
    "pedestal": "产品展示空间；圆柱展台与背景板转化为干净的展台与展示背景。",
    "longtake": "按长镜头规划的连续空间；每个区域的墙体、门洞和道具体块转化为与时代相符的真实场景。",
    "story": "按剧本设计的场地；体块转化为与时代相符的真实建筑、地形和设施。",
}
ERA_WORLD = {"ancient_china": "中国古代，建筑、服饰与器物符合古代中国日常生活", "ancient_europe": "欧洲古代，石质建筑与古代欧洲日常服饰",
             "cyberpunk": "近未来赛博朋克都市，霓虹与金属材质", "space": "未来太空时代，飞船、空间站或外星地表",
             "steampunk": "蒸汽朋克时代，黄铜、铸铁与蒸汽机械", "modern": "现代日常世界"}
ERA_LIGHT = {"ancient_china": "自然日光，柔和真实阴影", "ancient_europe": "自然日光，柔和真实阴影",
             "cyberpunk": "夜景，霓虹灯与环境反射光，保持主体清晰", "space": "冷色主光与设备补光，保持主体清晰",
             "steampunk": "暖色日光与蒸汽散射光", "modern": "自然日光，真实阴影与环境反射"}
STYLE_LABEL = {"real": "写实实拍风格", "aaa_cg": "3A 游戏 CG 风格", "2d": "二维动画风格"}
ROLE_MARK = {"setup": "（铺垫）", "twist": "（转折）", "beat": ""}
HUMANOID = ("pawn", "fbx")
PROXY_SHORT = {"pawn": "无四肢棋子式人偶", "fbx": "无四肢标准人体白模", "robot": "无四肢机器人体块",
               "vehicle": "载具体块（车身+舱体+轮柱）", "block_animal": "四足动物体块", "block_bird": "鸟形体块",
               "block_fish": "鱼形体块", "prop": "产品体块"}
REALIZE = {"pawn": "将无四肢代理还原为自然完整的真人，四肢、服装、步态、表情与动作由AI自然完成，不沿用代理体块的僵直姿态。",
           "robot": "将机器人体块还原为完整的人形机器人，关节、外壳与动作由AI自然完成，不沿用体块的僵直姿态。",
           "vehicle": "将载具体块还原为真实汽车，车身造型、灯组、轮胎与驾驶者由AI自然完成；加减速、急刹与转向要有真实的"
                      "车身点头、侧倾和轮胎抓地反馈，不沿用体块的刚性平移。",
           "block_animal": "将四足动物体块还原为真实动物，躯体比例、毛发与步态由AI自然完成，不沿用体块的刚性姿态。",
           "block_bird": "将鸟形体块还原为真实飞鸟，羽毛与振翅由AI自然完成。",
           "block_fish": "将鱼形体块还原为真实的鱼，鳞片与摆尾由AI自然完成。",
           "prop": "将产品体块还原为真实产品，材质与细节由AI自然完成。"}
REALIZE["fbx"] = REALIZE["pawn"]
SOUND = {"pawn": "脚步", "fbx": "脚步", "robot": "机械运转", "vehicle": "引擎、轮胎摩擦、刹车", "block_animal": "动物脚步",
         "block_bird": "振翅", "block_fish": "水流", "prop": "动作"}
HUMAN_CAST = {"note": "人物均为无四肢几何代理，保持身份色与整体比例；完整自然表演留给最终AI。",
              "realize": "将无四肢代理还原为自然完整的主体，四肢、服装、步态、表情与动作由AI自然完成，不沿用代理体块的僵直姿态。",
              "who": "人物", "sound": "脚步、风、水、动作与环境声", "keep": "无四肢几何"}


def _compass(dx: float, dy: float) -> str:
    if math.hypot(dx, dy) < 0.5:
        return "原地"
    names = ["由南向北", "由西南向东北", "由西向东", "由西北向东南", "由北向南", "由东北向西南", "由东向西", "由东南向西北"]
    angle = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    return names[int((angle + 22.5) // 45) % 8]


def _path_stats(keys: list) -> tuple[float, float, float]:
    length = sum(math.dist(a[1:3], b[1:3]) for a, b in zip(keys, keys[1:]))
    return length, keys[-1][1] - keys[0][1], keys[-1][2] - keys[0][2]


def _pauses(keys: list) -> int:
    count, still = 0, False
    for a, b in zip(keys, keys[1:]):
        moving = math.dist(a[1:3], b[1:3]) > 0.05
        if not moving and not still:
            count += 1
        still = not moving
    return count


def actor_line(actor: dict) -> str:
    kind = KIND_LABEL[actor["kind"]].format(head=HEAD_LABEL.get(actor.get("head", ""), "头"),
                                            body=BODY_LABEL.get(actor.get("body", ""), "身体"))
    color = f"，{COLOR_NAME.get(actor.get('color') or '', '身份色')}" if actor.get("color") else ""
    return f"{actor['id']}：{kind}，高约 {actor['height_m']:.2f} 米{color}"


def spec_vars(scene: dict) -> dict[str, Any]:
    return {"duration_s": scene["duration_s"], "fps": scene["fps"],
            "frames": int(round(scene["duration_s"] * scene["fps"])), "resolution": scene["resolution"]}


def summary(scene: dict) -> dict[str, Any]:
    meta = scene.get("meta", {})
    actors = scene.get("actors", [])
    if actors:
        main = actors[0]["path"]["keys"]
        length, dx, dy = _path_stats(main)
        pauses = _pauses(main)
        motion = f"A {_compass(dx, dy)}移动，总路程约 {length:.0f} 米，平均速度约 {length / scene['duration_s']:.1f} 米/秒"
        motion += f"，途中停顿 {pauses} 次。" if pauses else "。"
        if len(actors) > 1:
            names = "、".join(a["id"] for a in actors[1:])
            motion += f"{names} 依次延迟约 0.9 秒跟随，保持横向间距，不相互穿插。"
        if any(a.get("yaw_keys") for a in actors):
            motion += "展示对象原地缓慢旋转。"
    else:
        motion = f"无主体；相机以约 {meta.get('speed_mps', 0):.1f} 米/秒沿空间路线运动，交代空间结构。"
    shots = []
    for s in scene["shots"]:
        cam = s["camera"]
        target = f"注视主体{cam['aim_actor']}" if cam.get("aim_actor") else "注视路径（见 aim_keys）"
        shots.append(f"{s['id']}｜{s['start_s']:.2f}–{s['end_s']:.2f} 秒｜{shot_labels(s, '｜')}｜{s['lens_mm']:.0f}mm｜{target}")
    return {"environment": meta.get("environment_description") or meta.get("world", ""),
            "subjects": [actor_line(a) for a in actors], "motion": motion, "shots": shots}


def control_layer_prompt(scene: dict) -> tuple[str, str]:
    control = scene.get("source", {}).get("control")
    labels = dict(scene.get("meta", {}).get("labels") or {})
    labels.setdefault("shot_count", len(scene["shots"]))
    labels["shot_count"] = len(scene["shots"])
    if control is None:
        labels = {"shot_form_label": "长镜头" if len(scene["shots"]) == 1 else "多镜头", "shot_count": len(scene["shots"]),
                  "subject_label": "、".join(a.get("label") or a["id"] for a in scene.get("actors", [])) or "无主体",
                  "color_label": {"white": "纯白色", "grey": "灰白色", "identity": "用不同颜色来区别主体、背景等"}[scene["palette"]],
                  "viewpoint_label": "按分镜", "camera_move_label": "按分镜",
                  "content_class_label": scene.get("meta", {}).get("content_class_label", "叙事"),
                  "color_rule": "场景为浅灰体块，主体用不同身份色区分（颜色只作身份识别）"}
    return render("forward.control_layer", spec=spec_vars(scene), control=labels, summary=summary(scene))


def v2v_render_prompt(scene: dict, style: str = "real") -> tuple[str, str]:
    meta = scene.get("meta", {})
    control = scene.get("source", {}).get("control") or {}
    era = control.get("era") or meta.get("era") or "modern"
    world = meta.get("world") or ERA_WORLD[era]
    subjects = []
    for actor in scene.get("actors", []):
        color = COLOR_NAME.get(actor.get("color") or "")
        text = f"{actor['id']}为{REAL_SUBJECT[actor['kind']]}"
        if color and actor["kind"] in ("pawn", "fbx", "robot"):
            text += f"（{color}为服装或外壳主色，用于区分身份）"
        elif actor["kind"] == "vehicle":
            role = f"{actor['label']}，" if actor.get("label") else ""
            text = f"{actor['id']}为{color or ''}汽车（{role}车漆颜色用于区分身份）"
        subjects.append(text)
    cuts = [f"{s['start_s']:.2f}" for s in scene["shots"][1:]]
    long_take = len(scene["shots"]) == 1
    camera_rule = ("严格沿用原视频的相机路径、景别和节奏，一镜到底，不切镜。" if long_take else
                   f"严格沿用原视频的切镜时间点（{'、'.join(cuts)} 秒）与每个镜头的相机路径、景别和节奏。")
    return render("forward.v2v_render", style_label=STYLE_LABEL.get(style, style), world=world, subjects=subjects,
                  environment=ENV_REAL.get(meta.get("environment", "story"), ENV_REAL["story"]) + dressing_note(scene),
                  camera_rule=camera_rule, lighting=meta.get("lighting") or ERA_LIGHT.get(era, ERA_LIGHT["modern"]),
                  long_take=long_take, vehicles=any(a["kind"] == "vehicle" for a in scene.get("actors", [])))


def dressing_note(scene: dict) -> str:
    """How the V2V stage should read the white-box scale cues (empty when the scene has none)."""
    counts = (scene.get("meta") or {}).get("dressing") or {}
    parts = [name for key, name in (("reference_posts", "参照柱"), ("lane_marks", "车道虚线"), ("floor_lines", "楼层线"),
                                    ("container_units", "按标准箱拆分的集装箱")) if counts.get(key)]
    if not parts:
        return ""
    return ("、".join(parts) + "是白模的尺度与速度参照：按世界观转化为路灯、行道树、立柱、道路标线、楼层窗带或真实集装箱；"
            "与时代不符时换成相应的自然或建筑元素，保留它们的位置与间距。")


def cast_wording(actors: list[dict]) -> dict[str, str]:
    """Card/prompt sentences about the proxies; an all-human cast keeps the team card's original wording."""
    if not actors:
        return {**HUMAN_CAST, "note": "本片无人物主体。"}
    groups: dict[str, list[str]] = {}
    for a in actors:
        groups.setdefault(a["kind"], []).append(a["id"])
    if all(k in HUMANOID for k in groups):
        return dict(HUMAN_CAST)
    kinds = "；".join(f"{'、'.join(ids)} 为{PROXY_SHORT[k]}" for k, ids in groups.items())
    return {"note": f"主体均为几何代理：{kinds}，保持身份色与整体比例；真实外观与完整自然动作留给最终AI。",
            "realize": "".join(dict.fromkeys(REALIZE[k] for k in groups)), "who": "主体",
            "sound": "、".join(dict.fromkeys(SOUND[k] for k in groups)) + "与环境声", "keep": "简化几何代理"}


def _story(scene: dict) -> dict[str, str]:
    meta = scene.get("meta", {})
    story = dict(meta.get("story") or {})
    actors = scene.get("actors", [])
    subject = meta.get("labels", {}).get("subject_label", "主体")
    story.setdefault("logline", f"{subject}在{meta.get('environment_description', '白模空间').split('：')[0]}中完成一段"
                                f"{meta.get('labels', {}).get('camera_move_label', '镜头').split('·')[-1]}调度。")
    story.setdefault("story", summary(scene)["motion"])
    story.setdefault("world", meta.get("world") or ERA_WORLD.get((scene.get("source", {}).get("control") or {}).get("era", "modern"), ""))
    story.setdefault("intent", "；".join(f"{s['id']} {s.get('title', '')}" for s in scene["shots"]))
    story.setdefault("semantic_only", "自然肢体动作、服装、表情与手持物只在最终AI中表现。")
    story.setdefault("cast", "；".join(f"{a.get('label') or a['id']}＝{a['id']}" for a in actors) or "无主体")
    return story


def director_card(scene: dict, storyboard: list[dict] | None = None) -> str:
    ev = K.SceneEvaluator(scene)
    story = _story(scene)
    cast = cast_wording(scene.get("actors", []))
    w, h = scene["resolution"]
    content = scene.get("meta", {}).get("labels", {}).get("content_class_label") or scene.get("meta", {}).get(
        "content_class_label", "叙事")
    lines = [f"《{scene['title']}》剧本与分镜导演卡",
             f"{scene['duration_s']:g}秒｜{w}×{h}｜{scene['fps']}fps｜{content}类｜{len(scene['shots'])}个物理镜头｜无音轨", "",
             f"一句话：{story['logline']}", "", f"完整剧本：{story['story']}", "", f"世界与空间：{story['world']}"]
    for i, space in enumerate(scene.get("meta", {}).get("spaces", []), 1):
        lines.append(f"空间{'一二三四五六七八九十'[i - 1] if i <= 10 else i}：{space}")
    if dressing_note(scene):
        lines.append(f"尺度参照：{dressing_note(scene)}")
    lines += ["", f"人物：{story['cast']}", cast["note"], f"摄影意图：{story['intent']}", ""]
    for e in scene.get("events", []):
        src = ev.source_time(e["at_s"])
        marker = ROLE_MARK.get(e.get("role", "beat"), "")
        lines.append(f"{e['id']}{marker} 成片{e['at_s']:.3f}s／源{src:.3f}s：{e.get('choice') or e.get('mechanism', '')} → "
                     f"{e.get('consequence', '')}")
    lines += ["", "逐镜/镜内阶段："]
    frames = {s["id"]: s for s in (storyboard or [])}
    for shot in scene["shots"]:
        mid = (shot["start_s"] + shot["end_s"]) / 2
        board = frames.get(shot["id"])
        board_time = board["time"] if board else mid
        target = f"注视主体{shot['camera']['aim_actor']}" if shot["camera"].get("aim_actor") else "注视空间点（scene.json aim_keys）"
        tags = shot_labels(shot, "·")
        lines += [f"{shot['id']}｜{shot['start_s']:.3f}–{shot['end_s']:.3f}s｜{shot.get('title') or shot['id']}",
                  f"动作与可见后果：{shot.get('action') or '按路线推进'}",
                  f"相机：{tags + '｜' if tags else ''}{shot['lens_mm']:.0f}mm；{target}；Blender 机位对象 CAMERA_{shot['id']}"
                  "（位置与朝向逐帧烘焙，改机位请改 scene.json 后重建）。",
                  f"交接：由当前动作、观察对象或信息变化转向下一镜，{cast['who']}保持全片共享轨迹。",
                  f"故事板：故事板\\{shot['id']}.png，成片{board_time:.3f}s／源{ev.source_time(board_time):.3f}s，中间帧构图参考；"
                  "独立续作不得从这里重演整段。",
                  f"短提示词：{shot_short_prompt(shot)}", ""]
    lines += [f"白模实际锁定：{cast['who']}身份与整体位置/朝向、实体支撑/路径、相机位置/角度、时机和镜头连接。",
              f"仅在剧本/最终AI表现：{story['semantic_only']}", "",
              f"声音：当前视频无音轨。最终成片可按事件添加{cast['sound']}，不把设计当作已制作。",
              "验收：几何/朝向/编码与关键帧检查记录见 reports/ 下的 JSON；正常速度观看与用户采用待定。"]
    lines += route_beat_lines(scene)
    return "\n".join(lines).rstrip() + "\n"


def route_beat_lines(scene: dict) -> list[str]:
    """The team card's 路线与空间交接 section (only when the plan declared route beats)."""
    beats = scene.get("beats") or []
    if not beats:
        return []
    return (["", "路线与空间交接（对应实际白模，不新增切镜）："]
            + [f"{b['start_s']:.3f}–{b['end_s']:.3f}s：{b['condition']} → {b['action']} → {b['change']}" for b in beats]
            + ["路线图只用于核对；不要把图中的线、箭头或时间标记画入最终视频。"])


def shot_labels(shot: dict, sep: str = "") -> str:
    """Framing · angle · move labels of a shot (vocabulary in taxonomy/camera_language.yaml)."""
    return sep.join(x for x in (label("framing", shot.get("framing", "")), label("angle", shot.get("angle", "")),
                                label("move", shot.get("move", ""))) if x)


def shot_short_prompt(shot: dict) -> str:
    tags = shot_labels(shot, "·")
    return f"{shot.get('action') or shot.get('title') or ''}{'；' + tags if tags else ''}。".replace("。。", "。")


def continuation_prompt(scene: dict) -> str:
    story = _story(scene)
    cast = cast_wording(scene.get("actors", []))
    long_take = len(scene["shots"]) == 1
    lines = [f"《{scene['title']}》视频续作提示词", "", "A. 最终AI成片提示词（可复制）",
             f"以本片{scene['duration_s']:g}秒白模视频为结构与摄影参考，保留{cast['who']}身份、空间路线、相对位置、事件时机和"
             f"真实镜头连接。{cast['realize']}",
             f"世界：{story['world']}", f"完整故事：{story['story']}", f"摄影：{story['intent']}",
             f"摄影格式：{'一镜到底，不切镜' if long_take else '按参考的实际切镜接续'}；整体时长{scene['duration_s']:g}秒。"]
    for shot in scene["shots"]:
        lines.append(f"{shot['start_s']:.3f}–{shot['end_s']:.3f}s：{shot_short_prompt(shot)}")
    lines += ["自然动作与效果：", story["semantic_only"],
              f"完整{cast['who']}动作不能改变已确定的出入路线、相遇先后、实际后果和角色数量。", "音效为后续制作建议，当前参考无声音。", "",
              "B. 白模续作提示词（仅继续预演时使用）",
              f"保持参考的明亮灰白结构、现有同款身份色主体和{cast['keep']}；不加骨骼、手持物、武器或特效轨迹。"
              f"沿 scene.json 中已有的路线与机位延续事件阶段和相机。上段A中的完整{cast['who']}与效果要求仅适用于最终AI成片。", "",
              "素材状态：视频与故事板均为本地已生成文件，未上传任何外部生成平台，@视频/图片编号需由实际使用者绑定。",
              "故事板为阶段中间帧或构图参考；时间与依赖见 reports/storyboard-frames.json。"]
    lines += route_beat_lines(scene)
    return "\n".join(lines) + "\n"


def vtt_cues(scene: dict) -> list[tuple[float, float, str]]:
    return [(s["start_s"], s["end_s"], s.get("title") or s["id"]) for s in scene["shots"]]
