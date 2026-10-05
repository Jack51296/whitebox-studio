---
id: forward.control_layer.example
version: 1.0.0
kind: verbatim
title: 白模控制层 prompt 实例（正向实例测试）
source: "发布版工作流提示词；正文与已验证基线一致"
imported_at: 2026-09-27
---
目标：
生成一段简易 3D blocking 体块预演动画，不需要写实材质，不需要贴图，不需要复杂灯光，只用于测试 Codex 是否能自动控制 Blender 从建模到动画再到 MP4 输出。
视频规格：
时长：15 秒
帧率：24fps
总帧数：360 帧
分辨率：1920×1080
输出文件：outputs/test_blocking_animation.mp4
渲染方式优先使用 Workbench 或类似快速预览渲染，不要使用 Cycles
画面内容：
创建一个简易场景，包括灰色地面、几个大型方块障碍物、圆柱体树干、简单拱门和坡道。

创建一个低精度体块动物主体，全部由基础几何体组成：
身体：长方体
头部：方块或低模球体
四肢：细长方块或圆柱体
尾巴：细长圆柱体

主体从画面左侧向右前方持续奔跑移动。

主体动画需要包含：
整体向前位移
身体轻微上下起伏
四肢周期性前后摆动
尾巴轻微摆动

相机需要全程跟随主体运动，形成一镜到底的跟拍效果。

主体必须始终在画面中清晰可见，不要切镜头，不要突然跳变。

场景整体像 Blender / Maya / Cinema4D 中的简易 blocking 白模/灰模预演动画。

技术要求：
所有模型、动画、相机、灯光、渲染设置都必须通过 Python 脚本自动完成。

脚本运行前自动清空默认场景。

自动创建 outputs 文件夹。

自动保存 .blend 文件到 outputs/test_blocking_animation.blend。

自动渲染 MP4 到 outputs/test_blocking_animation.mp4。

脚本里要包含清晰的函数结构，例如：
clear_scene()
create_environment()
create_block_animal()
animate_subject()
animate_camera()
setup_render()
main()

脚本完成后，请尝试用以下命令运行：
macOS: /Applications/Blender.app/Contents/MacOS/Blender -b --python test_blocking_animation.py
如果系统中 blender 命令可用，也可以运行：blender -b --python test_blocking_animation.py

如果找不到 Blender 可执行文件，不要停止；请告诉我应该手动运行的命令。

渲染完成后，请检查 outputs/test_blocking_animation.mp4 是否存在，并告诉我结果。
