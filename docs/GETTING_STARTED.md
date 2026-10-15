# 开始使用

本文介绍公开源码仓库中的基础安装和三个 PPTX 文件命令。示例假设当前目录是仓库根目录，并保留 `aippt-studio/` 与根级 `scripts/` 的相对位置。

## 环境要求

- Python 3.11 或更高版本（项目 README 的 macOS 环境示例使用 Python 3.13）。
- macOS/Linux：`uv`；Node.js 22.12 或更高版本，用于 Node 渲染路径。
- 使用 Work 或跨平台包装脚本时：PowerShell 7.3 或更高版本；macOS Shell 包装器也调用该运行时。
- 需要调用相应功能时，另需浏览器、Microsoft PowerPoint、OfficeCLI 或其他外部工具。它们不由 Python/Node 安装步骤自动提供。

## macOS 安装

在 `aippt-studio/` 目录创建 Python 环境并按锁文件安装核心与开发依赖：

```sh
cd aippt-studio
uv venv .venv-macos --python 3.13
UV_PROJECT_ENVIRONMENT=.venv-macos uv sync --frozen --extra dev
npm ci --ignore-scripts
cd ..
```

这一步安装项目声明的 Python 和 Node 包；它不会安装 PowerPoint、OfficeCLI 或浏览器，也不会下载模板库、上游源码或模型。扩展和媒体工具有单独安装入口，只有相关功能需要时才按对应脚本说明配置。

## 查看 CLI 帮助

安装后从仓库根目录运行：

```sh
./aippt-studio/.venv-macos/bin/aippt --help
./aippt-studio/.venv-macos/bin/aippt pptx-new --help
./aippt-studio/.venv-macos/bin/aippt pptx-inspect --help
./aippt-studio/.venv-macos/bin/aippt pptx-save-as --help
```

也可以从 `aippt-studio/` 运行 `UV_PROJECT_ENVIRONMENT=.venv-macos uv run aippt --help`。对 Work、上游工具或 PowerPoint 原生自动化有依赖的命令，还需要相应的外部目录或宿主工具。

## 最小 PPTX 操作

创建空白 16:9 可编辑 PPTX：

```sh
./aippt-studio/.venv-macos/bin/aippt pptx-new ./blank.pptx
```

检查已有文件的页面和对象结构：

```sh
./aippt-studio/.venv-macos/bin/aippt pptx-inspect ./blank.pptx
```

检查指定页面并将完整报告写入 JSON：

```sh
./aippt-studio/.venv-macos/bin/aippt pptx-inspect ./blank.pptx --slide 1 --output ./inspect.json --json
```

将已保存的 PPTX 复制到新目标：

```sh
./aippt-studio/.venv-macos/bin/aippt pptx-save-as ./blank.pptx --output ./copy.pptx
```

`pptx-save-as` 遇到已存在目标时默认报错；只有要覆盖目标文件时才传 `--replace`。这些命令处理磁盘上的文件，不会读取 PowerPoint 中尚未保存的改动。`pptx-new`、`pptx-inspect` 和 `pptx-save-as` 本身不会打开 PowerPoint，因此结果不等同于原生 PowerPoint 验收。

## 测试入口

项目提供 `aippt-studio/scripts/test.ps1`。从仓库根目录通过共享 PowerShell 启动器运行：

```sh
./scripts/pwsh.sh -NoProfile -File aippt-studio/scripts/test.ps1
```

本页记录的是仓库内的启动命令和配置要求；不表示上述安装或测试已在其他机器上执行。
