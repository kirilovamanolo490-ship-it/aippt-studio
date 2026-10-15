# 平台运行与能力边界

更新：2026-09-25，Work 37。

## 制作模式

当前对话模型承担策划和决策；Studio 与 GitHub 工具承担执行。按用户要求，不部署本地模型，不下载权重，不把额外模型接口列为默认制作依赖。下表的 PPTAgent/Presenton 独立生成服务为可选能力。

## 当前 Mac 环境

工作区根目录通过 `scripts/pwsh.sh` → 当前 `work/<N>/workspace/aippt.ps1` 启动。Python 是 `.venv-macos/bin/python`；OfficeCLI 是 `.tools/officecli`；两者均为 Apple Silicon 原生运行环境。Node 与 Chrome 使用本机安装。

Studio 不再以翻译层进程的 `platform.machine()` 推断物理 Mac。它固定用 `/usr/sbin/sysctl -in hw.optional.arm64` 确认硬件；Apple Silicon 上 Rosetta x64、被 shadow 的 `sysctl`、不可用或模糊探测都会在写 Work/学习/核心状态前失败关闭。已确认的 Intel `sysctl=0` 仍可使用原生 x64。公开的 CLI、维护、归档、案例索引、语义学习和核心演进入口均复用这一检查，且先解析参数以保留 `--help`。PowerPoint automation 只走 `/usr/bin/osascript`，launchd 安装/状态/唤醒只走 `/bin/launchctl`；它们不从 `PATH` 接受同名可执行文件。

旧 Windows `.venv` 和 `.tools/officecli.exe` 已从活动 Studio 目录删除，清单/哈希保留在 Work 17。Windows 后端源代码保留，以免删除仍有用途的功能；Windows 主机需要重新安装自己的环境。历史 Work 的完整证据不做破坏性清理。

## 按操作区分平台能力

| 操作 | Mac 后端 | 实际边界 |
|---|---|---|
| 策划、状态、证据、审阅 | Python | 不需要 Windows |
| HTML / PptxGenJS 原生制作 | Node / Chromium | 使用锁定运行包，非整个上游仓库功能承诺 |
| 模板、图片稿、重建 | 已注册 Adapter | 模型型步骤另需配置与实测 |
| PPTX 检查、基础动画 | OfficeCLI / Studio OOXML | 精确时长修改保留非目标 XML 字节 |
| 高级动画、路径、重复、反向、轮播 | ppt-master 动画库，Studio 隔离适配器 | 当前 native plan 合同；改写已有序列须 replace_existing=true |
| Morph 身份与切换 | OOXML 身份重命名、原生切换扩展 | 相邻页端点由用户/作者设计；导出不会发明端点几何 |
| 已保存 PPTX 的结构读取、新建、另存 | `pptx-inspect` / `pptx-new` / `pptx-save-as` | 直接处理磁盘文件，不启动 PowerPoint；`pptx-save-as --replace` 可原子更新指定目标，不包含应用内未保存编辑 |
| 制作过程的单页/全稿图片预览 | `export-png` + 本机 OfficeCLI HTML | 直接渲染当前 PPTX，不经 PowerPoint 打开/保存/重开；近似预览，不能宣称原生显示验收 |
| 在 PowerPoint 中打开 | `pptx-open` + `/usr/bin/open` | 仅按需请求系统打开，成功返回不证明已查看或原生验收 |
| PPTX 原生保存、重开验证 | Mac PowerPoint AppleScript | 只在正式需要原生兼容性证据时运行；自动化拒绝或超时明确失败 |
| 已完成 PPTX → PDF | `export-pdf` + Mac PowerPoint 原生保存 | 仅当前用户明确要求时直接转换；不运行 Studio 制作/QA/结项流程 |
| 已完成 PPTX → MP4 | `export-mp4` + Mac PowerPoint 原生影片 + FFmpeg | 仅当前用户明确要求时直接转换；优先无重编码封装，不做解码/抽帧/动效验收。Mac 影片可能不含对象动画，部分转场也会变化 |
| 动效预览 / 媒体验证 | Mac PowerPoint save as movie + 隔离 FFmpeg | 属于明确要求动画制作/验收时的完整链路；解码和抽帧也不能单独证明动画播放。当前 AppleScript 未配置原生导出的分辨率、录制计时或旁白选项 |
| 媒体轨道和时长检查 | Mediabunny 1.55.7 独立 Node 运行包 | 不运行模型；包解析不等于完整解码或有声/语义同步验收 |
| COM | Windows 后端 | Mac 不使用 COM；有 Mac 替代能力不等于 COM 在 Mac 上可用 |
| PPTAgent / Presenton | 配置的隔离模型运行环境 / HTTP 服务 | 不能凭目录或配置存在宣称真实模型生成成功 |

`doctor` 单独列出 `mac-native-effects`、`mac-native-video`、`pptagent-model-runtime`、`presenton-service-config`。可用性检测与一次任务的执行成功分开：静态检测不保证系统权限、接口健康、每个效果保真。

## 重建依赖

在 `aippt-studio/` 内使用 uv 重建 Python 环境和锁定 npm 包，见 README。macOS/Linux 从工作区根目录运行 `./aippt-studio/scripts/setup-media.sh` 安装 `imageio-ffmpeg==0.6.0`；Windows 可直接运行同名 `.ps1`。两个入口都先验证本机 uv 与 Studio Python，不会借用旧 Work、历史 job 或 Windows 缓存。可用 `AIPPT_FFMPEG` 显式指定经验证的本机可执行文件；不会改变主项目 Python 依赖锁或上游源码。

Mac PowerPoint 影片优先使用其容器临时目录；若当前进程无写入权限，则使用 Work 输出目录内的隔离暂存目录。原生影片经复制/转码到 Work 后清理暂存。所有 Office 操作只打开、关闭本次唯一命名的副本，不退出 PowerPoint、不关闭用户无关文稿。

本机 Codex 提供可运行的 LibreOffice 和 Poppler 路径。它们属于当前应用环境，换到其他终端或机器应重新检测；不是 Studio 全局固定依赖。上游额外功能所需的 pandoc、模型、字体构建工具等也须按实际操作配置。

## 剩余验收范围

原生动画 XML 正确、Office 保存正常、影片可解码、逐效果视觉通过是不同证据。复杂文件或未测试的参数组合仍需实际样例验证。独立模型/服务/视频工程不会因为本机 PPTX 后端已经就绪而自动配置完成。
