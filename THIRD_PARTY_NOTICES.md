# 第三方组件与许可声明 (Third-Party Notices)

本文档列出 AIRPAD 使用的第三方组件及其许可。数据来源分两类，均标注在表中：

- **metadata** —— 从本机已安装包的 `*.dist-info/METADATA` 读取（`License` 字段或 `License ::` 分类器）
- **LICENSE 文件** —— 从本机已安装包内附带的 `LICENSE` / `COPYING` 原文读取

复现命令：

```bat
.venv\Scripts\python.exe tools\license_report.py
```

> ⚠️ 本文档是**工程性汇总**，用于开发与合规准备，**不构成法律意见**。发布二进制交付物前请自行核对上游原文与许可证全文。

---

## 本项目自身

AIRPAD 自身的许可**尚未选择**（仓库内没有 `LICENSE` 文件）。下面的义务约束在选择后将进一步明确——特别是 PySide6 的 LGPL-3.0 条款。

---

## 直接运行时依赖

| 组件 | 版本 | 许可 | 来源 |
|---|---|---|---|
| MediaPipe | 0.10.21 | Apache License 2.0 | metadata |
| NumPy | 1.26.4 | BSD 3-Clause | metadata |
| opencv-contrib-python | 4.11.0.86 | Apache License 2.0 | metadata |
| PySide6 | 6.8.3 | LGPL-3.0-only **或** GPL-2.0-only **或** GPL-3.0-only（三选一） | metadata |
| PySide6-Essentials | 6.8.3 | 同上 | metadata |
| PySide6-Addons | 6.8.3 | 同上 | metadata |
| shiboken6 | 6.8.3 | 同上 | metadata |

### 模型文件

| 资源 | 来源 | 说明 |
|---|---|---|
| `models/hand_landmarker.task` | MediaPipe 官方模型库（`storage.googleapis.com/mediapipe-models/hand_landmarker/`，float16 v1） | 7,819,105 字节<br>SHA-256 `fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1`<br>随 MediaPipe 项目以 Apache License 2.0 提供；**再分发前请核对上游模型页面的最新声明** |

---

## 传递依赖（随上述组件间接安装）

| 组件 | 版本 | 许可 | 来源 |
|---|---|---|---|
| absl-py | 2.5.0 | Apache License 2.0 | metadata |
| attrs | 26.1.0 | MIT | LICENSE 文件 |
| cffi | 2.1.1 | MIT No Attribution (MIT-0) | LICENSE 文件 |
| colorama | 0.4.6 | BSD 3-Clause | metadata |
| contourpy | 1.3.3 | BSD 3-Clause | metadata |
| cycler | 0.12.1 | BSD 3-Clause | metadata |
| flatbuffers | 25.12.19 | Apache License 2.0 | metadata |
| fonttools | 4.65.0 | MIT | metadata |
| jax | 0.7.1 | Apache License 2.0 | metadata |
| jaxlib | 0.7.1 | Apache License 2.0 | metadata |
| kiwisolver | 1.5.1 | BSD 3-Clause（含 Kiwi 附加条款） | metadata |
| matplotlib | 3.11.1 | PSF-based（matplotlib 1.3.0+ 许可协议） | metadata |
| ml_dtypes | 0.5.4 | Apache License 2.0 | LICENSE 文件 |
| opt_einsum | 3.4.0 | MIT | metadata |
| packaging | 26.3 | Apache-2.0 **或** BSD-2-Clause | LICENSE 文件 |
| pillow | 12.3.0 | MIT-CMU (HPND) | LICENSE 文件 |
| protobuf | 4.25.9 | BSD 3-Clause | metadata |
| pycparser | 3.0 | BSD 3-Clause | LICENSE 文件 |
| pyparsing | 3.3.2 | MIT | LICENSE 文件 |
| python-dateutil | 2.9.0.post0 | Dual License（Apache-2.0 / BSD） | metadata |
| scipy | 1.17.1 | BSD 3-Clause | metadata |
| sentencepiece | 0.2.2 | Apache License 2.0 | metadata (`License-Expression`) |
| six | 1.17.0 | MIT | metadata |
| sounddevice | 0.5.6 | MIT | LICENSE 文件 |

## 仅开发/测试使用（不随交付包分发）

| 组件 | 版本 | 许可 | 来源 |
|---|---|---|---|
| pytest | 9.1.1 | MIT | LICENSE 文件 |
| iniconfig | 2.3.0 | MIT | LICENSE 文件 |
| pluggy | 1.6.0 | MIT | metadata |
| Pygments | 2.21.0 | BSD 2-Clause | LICENSE 文件 |

---

## 合规注意事项

### PySide6 / Qt（LGPL-3.0 路径）

若选择 LGPL-3.0 路径分发 AIRPAD，需要满足：

1. 随包提供 **LGPL-3.0 与 GPL-3.0 许可证全文**，以及 Qt 的版权声明
2. 明确告知用户使用了 Qt 及其版本
3. 允许用户**替换/重新链接** Qt 库（目录式绿色包天然满足：Qt 的 DLL 与 `PySide6/` 目录是可替换的独立文件，未静态链接进本项目代码）
4. 不对 Qt 施加额外的许可限制

若本项目自身选择 GPL 系许可，则可直接走 GPL 路径；若为闭源商业分发，则需要 Qt 商业许可。

### 目录式交付包

`tools/package.py` 生成的 `dist/AIRPAD-<version>/` **只包含本仓库自身的源码、模型与文档**，不打包 `.venv` 中的第三方 wheel。第三方依赖由目标机器上的 `Setup-AIRPAD.cmd` 从 PyPI 安装，因此第三方组件的许可文本由 pip 各自落盘在 `*.dist-info/` 中。

若将来改为打入全部依赖的独立分发包，则**必须**在包内附上上述所有组件的许可文本，并重新评估 Qt 的替换性要求。

---

## 待办

- [ ] 选择本项目的 LICENSE 并补充许可证全文
- [ ] 核对 MediaPipe 模型文件页面的当前许可声明
- [ ] 若改为全依赖打包分发，生成聚合的许可证全文清单
