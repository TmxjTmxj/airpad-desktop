# Recommended Implementation Order

配套文档：[AIRPAD DESIGN SPEC v1.0](./AIRPAD-DESIGN-SPEC-v1.0.md)  
日期：2026-09-11 · 状态：阶段交付路线，尚未开始实现

下面的命令是各阶段应该交付的目标 CLI，不表示这些命令现在已经存在。每个阶段结束均要运行、测试、记录失败、修复，再进入下一阶段。算法回放通过和真实硬件通过分别记录。

## 通用完成条件

每阶段产出一个可启动版本、一组相关回归测试、一份短验证记录，以及一条清晰的启动命令。记录包含依赖/model hash、设备模式、参数版本、测试结果和未解决问题；没有实测的项目明确写未测量。后续阶段保留前面入口，便于定位相机、识别或输入层故障。

第一条产品闭环是看到相机和手，不先构建插件平台。允许摄像头手势发送真实 button-down 前必须通过输入恢复阶段；Broker 自身的按下/释放先在可见测试靶场验证。开机启动、额外手势、双手和 CAD 都不应阻塞对基本指向体验的检验。

## Stage 0 — 环境与摄像头，第一次就看到画面

**可运行版本**：独立环境中的相机探测与预览。

目标入口：`python -m airpad camera --device 0 --preview`

工作：检查 Windows/Python 架构与 wheel 可用性，锁定初始依赖；枚举/选择相机；显示镜像画面、实际读帧率与请求/实际格式；提供立即停止入口。此阶段不需要 hand model，也不注入任何输入。

验收：连续预览 60 秒；停止后相机可被其他程序打开；无设备/拒绝权限/设备占用时给出具体错误；拔相机不会让窗口无限无响应。对比 MSMF/DirectShow，记录设备、格式与真实 FPS，不靠 CAP_PROP_FPS 返回值宣布成功。

## Stage 1 — 手部跟踪与质量观察

**可运行版本**：相机画面上显示骨骼、21 点、主手候选及拒绝原因。

目标入口：`python -m airpad track --preview`

工作：接入本地模型、递增帧时间戳、VIDEO 模式、身份关联和基础质量门控。显示 handedness score 与 geometry quality，未知的模型置信度显示 N/A。

验收：左/右手、手腕旋转、远近、手进出视野、两只手交叉；对歧义冻结，不瞬间换手。记录 read→inference 耗时、capture/processed FPS 和失败姿态。镜像方向与左右手标注必须当场核验。

模型加载失败时仍能回到相机选择/错误界面，不静默联网下载。此阶段开始提供用户主动的 landmark capture，仅保存明确选择的测试片段。

## Stage 2 — 相对虚拟指针与回放

**可运行版本**：练习窗口内的虚拟指针可由食指相对控制。

目标入口：`python -m airpad pointer --backend mock --practice`

工作：等距坐标、掌尺度、位移归一化、小数残差、可注入时钟、最小 CoreState 与 MockBackend；暂时保留 raw pointer 便于对照。

验收：手重新进入画面第一帧无跳变；相机分辨率变更不直接改变归一化增益；同比缩放 landmark 后识别结果一致；同一路径 30/60 FPS 的总 count 接近（初始容差 5%）。慢速微动不可被逐帧取整吞掉。

额外入口：`python -m airpad replay tests/fixtures/landmarks/pointer.jsonl --backend mock`。fixture 由本阶段生成并审查；命令重复运行产生相同离散动作与容差内相同轨迹。

## Stage 3 — Filter、Clutch 与低帧率保护

**可运行版本**：虚拟指针具备可对照的 raw/filter 轨迹、开掌重定位和暂停。

目标入口：`python -m airpad pointer --backend mock --practice --compare-filter`

工作：One Euro、异常拒绝、带迟滞的静止锁定、手势候选冻结、Clutch 重建基线、lost/rearm、过期帧与低采样率保护。

验收：分别测试静止抖动、持续慢移、快甩/急转、Clutch 时大幅摆手、恢复第一帧、100–500 ms 断帧和两手身份歧义。绘制抖动与动态滞后对比，用同一 fixture 比较 EMA/One Euro/Kalman 基线；禁止只看静止曲线选最平滑参数。

退出门槛：没有系统性漂移或恢复跳变，快速响应与慢速精度达到可以进入真人目标练习的程度。核心回放覆盖 Cancel 与 stale invariants。

## Stage 4 — Windows Input Broker 与故障释放

**可运行版本**：可见的 Windows 本地输入测试靶场，具有独立 Broker 和监督者。

目标入口：`python -m airpad input-test --visible-target`

工作：正确 ctypes INPUT 布局、move/wheel/button/shortcut、epoch/seq/deadline、write-ahead 共享账本、单实例、热键注册、输入所有权、进程互监。测试窗口接收并显示真实 Win32 输入消息。

验收：正常 move/down/up/shortcut 顺序；部分提交模拟；UI/Worker/Broker 分别死亡；在 down 前后及回执前杀 Broker；晚到旧 epoch down 被拒绝；拖拽持有模拟时断开 IPC；锁屏/恢复与热键冲突。OS 测试窗口必须实际收到释放，不能只检查 broker 内存状态。

释放延迟对照 spec 的 50/200/250 ms 目标，记录调度尾部与失败。如果相应故障路径未通过，停止在此阶段，不接入实际手势 click/drag。故障注入仅面向自己的可见测试窗口。

## Stage 5 — 真正的 Windows 相对指针

**可运行版本**：单食指移动系统光标，五指 Clutch，全局急停和失踪保护。

目标入口：`python -m airpad run --mode pointer-only`

工作：将已验证的 Motion 输出接入 Broker；默认跟随 Windows 加速度，记录系统鼠标设置；简单 Enable/Pause 控件与运行状态。

验收：单屏与双屏跨界、负坐标显示器布局、125%/150% 混合 DPI、实体鼠标接管、显示器插拔后重建基线。进行小目标定位和慢/快动作测试，分别记录 count 与真实像素行为。

该阶段不产生手势按钮事件。若实机指针手感不合格，回到归一化/滤波/增益调参，不先增加手势。

## Stage 6 — Pinch Click

**可运行版本**：可点击练习目标及普通桌面按钮。

目标入口：`python -m airpad run --mode pointer-click`

工作：OPEN/APPROACH/START/HOLD/RELEASE 周期、收拢冻结、正常释放提交、取消不点击、重复 Pinch 重武装、基本阈值设置。

验收：快速与慢速 Pinch、Pinch 途中收手、启动时已闭合、低质量释放、过短动作、长捏合、重捕获和自然说话比划。至少 100 次明确意图点击，记录 precision/recall、点击位置漂移和无意图误触；双击由两次完整周期产生，不引入等待第二次点击的额外延迟。

此阶段不发送长期 left-down。手型变化导致的目标漂移未解决时不进入 Drag。

## Stage 7 — Drag 与 MVP 安全闭环

**可运行版本**：Pinch 保持并移动可拖动练习对象和窗口，松开可释放。

目标入口：`python -m airpad run --mode pointer-click-drag`

工作：hold+掌中心位移联合起拖、一次 down、释放 up、禁止 drag 后补 click、起拖基线切换、无移动长捏合取消。

验收：轻微点击抖动不误拖；正常长距离拖动；边拖边出视野、开掌、Pause、杀进程和拔相机；反复测试换手/过期帧，不能恢复旧拖拽。人工评估起拖空程和 180 ms 初值是否自然。

拖拽连续失败或安全测试仍有残留键时优先修复，不添加 Scroll。

## Stage 8 — 两指 Scroll 与应用兼容性

**可运行版本**：连续两指垂直滚动，可选水平滚动。

目标入口：`python -m airpad run --mode desktop-core`

工作：两指候选、平移意图、主轴锁定、自然方向训练、wheel 分数残差、120 单位兼容模式；Zoom 仍关闭。

验收：从 Cursor 进入 Scroll 时光标不乱跑；退出后没有残余 wheel；普通浏览器、文件列表与一个传统桌面应用分别验证方向和粒度；Pinch 过渡不会产生 click。测试两指张合时安全冻结的体验，为后续 Zoom 采集混淆样本。

## Stage 9 — Calibration、Profile 与可用 MVP

**可运行版本**：新用户能完成校准、保存 profile，再启动得到同样配置。

目标入口：`python -m airpad calibrate`，完成后使用 `python -m airpad run`。

工作：开掌统计、运动范围/静止噪声、练习目标、Pinch 样本分布、校准验证、原子保存与恢复、基础 GUI 设置和本地日志。校准不控制 Windows 光标。

验收：不同手大小/距离/主手的用户、阈值分布重叠时拒绝错误校准、损坏配置回退、版本改变提示、设置变更在安全边界生效。进行至少 5 人工程筛查和 20 分钟连续使用；记录点击/拖拽/滚动任务成功率与疲劳。

**里程碑：MVP。** 具备基础连续交互、profile、控制窗口/HUD、异常保护和回放证据。未通过人体任务则仍是开发版本。

## Stage 10 — 正式 UI、Tray 与生命周期

**可运行版本**：像桌面工具一样可最小化、恢复、暂停、校准和退出。

目标入口：`python -m airpad`。

工作：正式状态页、设置、手势指南、托盘、HUD 独立开关、相机关闭策略、当前用户可选启动项、诊断导出、主题/高 DPI 和无托盘回退。

验收：HUD 开关不改变控制状态；关窗口到托盘、无托盘时退出、启动项默认暂停、锁屏/休眠/切用户、相机错误恢复、100 次启动退出无遗留输入/进程。比较 HUD 开关前后 FPS，验证预览背压不阻塞控制。

## Stage 11 — Zoom、三指动作与 JSON Profiles

**可运行版本**：用户显式开启后，支持双指缩放、Task View、桌面切换。

目标入口：主窗口 Gesture Settings 与练习页。

工作：两指 translation/separation 竞争及模式锁定、短 Ctrl+Wheel 事务、三指动态 swipe/latch、动作 allowlist、前台应用变化的释放和 profile 切换。完成至少浏览器缩放与桌面导航两套经过验证的映射。

验收：滚动/缩放双向混淆矩阵、一次 swipe 不重复触发、快捷键不残留修饰键、输入返回部分成功、切应用时不继续旧 gesture。未知应用不假定 Ctrl+Wheel 是 Zoom；插件未知动作/非法参数必须被拒绝。

未达到≤2% 双指混淆目标的设备或用户，Zoom 保持 Experimental/关闭。可先发布其他已通过功能。

## Stage 12 — V1 Benchmark、打包与 Portfolio

**可运行版本**：干净 Windows 机器离线安装后可以启动、校准和使用。

目标入口：安装包入口；开发者保留 `python -m airpad benchmark --suite replay` 与 `python -m airpad benchmark --suite windows`。

工作：固定依赖/model hash、目录式打包、licenses/notices、README、真实 Demo 素材、平台测试矩阵、已知限制。用规范协议收集至少 12 人候选测试和无意图人时数据。

验收：执行完整回归、1 h/4 h 稳态、断网运行、三个进程各自故障、真实设备延迟与 CPU/内存测量。报告分开列内部处理延迟和可见响应延迟，没有条件测的项标明未测。只把审核过的真实结果写 README。

**里程碑：V1。** 不要求 SolidWorks 已完成，但必须明确哪些高级动作默认开启、可选或实验中。

## Stage 13 — Two-Hand 与本地 3D Viewer

**可运行版本**：自有本地 3D Viewer 中完成 orbit/pan/zoom，与桌面模式显式切换。

工作：两手身份锁定、Mode Hand 的语义模式、两手距离与角度变化、任一手失踪取消、直接语义 API 适配器。先单动作锁定，再评估是否开放同时多轴。

验收：双手交叉、单手丢失、角色互换、快速模式切换不产生跳变；3D 模型停止动作可预测；普通用户能完成标准视角任务。比较直接语义控制与合成鼠标输入的体验。

## Stage 14 — Blender / SolidWorks 应用适配

**可运行版本**：针对已安装的具体应用版本和导航配置完成真实 orbit/pan/zoom。

工作：先验证 Blender，再验证 SolidWorks；记录快捷键和设置对应关系，建立限定应用版本的 profile；所有 modifier/mouse-down 仍经 Broker 租约与统一释放。

验收：前台应用切换、弹窗、快捷键自定义、不同权限、退出 3D 模式、两手遮挡/相机断开。完成完整桌面→3D 演示，视频明确显示实际设备和测试环境。

**里程碑：V2/3D 演示版本。** AI 调参、Python 插件、更多应用适配根据真实需求再加入，不先占据基础稳定性工作。

## 阶段门禁

| 发现的问题 | 应采取的行动 |
| --- | --- |
| 残留鼠标按钮/修饰键、旧消息重新按下 | 阻止进入下一阶段，修复恢复与协议 |
| 光标稳定性不足 | 回到相对坐标/滤波/增益，先验证再增加动作 |
| Click 误触高 | 缩小有效姿态、校准和调整 intent；不增加静态手势表 |
| Zoom/Scroll 难以消歧 | 保留 Zoom 可选或移至双手，不牺牲滚动 |
| Python 本身成为已测瓶颈 | 只迁移已定位的小模块，保留回放与接口 |
| 没有硬件/真人验证条件 | 可以继续纯算法与 Mock 工作；对应实机里程碑保持未验收 |

建议下一次实施从 Stage 0 开始，当次就交付能显示真实相机画面、显示实际 FPS 并能安全停止的版本，再进入手部跟踪。
