# AIRPAD Windows 使用说明

`dist/AIRPAD-Windows/AIRPAD.exe` 包含 Python 3.12、PySide6、MediaPipe 和已校验模型。必须保留整个目录；无需额外安装 Python。构建命令：

```bat
.venv\Scripts\python.exe tools\build_windows.py --verify --zip
```

项目根目录的 `Start-AIRPAD.cmd` 与 `Start-Practice.cmd` 使用项目现有的 `runtime-data`，以继续使用你调好的识别参数。它们优先启动重建后的 EXE，未构建时回退到源码。若要指定其他数据目录，将 `--data-dir` 作为启动参数传入。

独立解压的便携包默认将配置和日志保存在 `%LOCALAPPDATA%/AIRPAD`。包内不包含本机配置、相机画面或运行日志，也不会读取项目目录里的个人校准值。

## 操控

在主界面点击“启用触控板手势”，然后 Enable：

- 单指移动；拇指与食指短捏后松开为左键。
- 捏住约 180 毫秒并移动手掌为拖拽；指尖弯曲本身不算拖拽。
- 防抖：瞄准时小幅晃动停稳；拖拽须连续同方向移动至少三帧、60 ms，孤立尖峰和往返抖动不再取消点击。明确松开立即响应，模糊释放确认两帧。
- 两指移动滚动；两指短暂停留后收回单指为右键。
- 开掌脱离控制；`Ctrl+Alt+Space` 全局急停；窗口内 `Esc` 暂停。

触控板预设只更改手势开关，保留个人识别阈值。建议先使用 `Start-Practice.cmd` 看左键、右键和滚动反馈，再用于桌面。两指短停手势时间窗为工程初值，手感仍需真人体验。

## 验证

`Self-Check.cmd` 会运行离线自检，检查真实模型加载及空白帧推理、录制后端输入子进程、离屏窗口，并写出报告；不会打开摄像头或向系统注入鼠标输入。模型使用固定 SHA-256。

EXE 未代码签名，不是 MSI 安装器；在本机成功自检不代表已经完成全新 Windows 机器、多显示器或睡眠唤醒的兼容性验证。

第三方依赖声明与许可证在 `THIRD_PARTY_NOTICES.md` 和 `LICENSES` 目录中；项目自身未指定开放源代码许可证。
