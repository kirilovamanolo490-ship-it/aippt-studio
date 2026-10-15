# 第三方组件与来源

本项目通过 Python 和 npm 依赖清单声明运行组件，并通过 `upstreams.lock.json` 记录外部源码来源。下表依据仓库清单整理；它不是完整 SBOM，也不能替代发布时对直接及传递依赖版本、许可证、通知文件和实际分发内容的核验。

## 包清单中声明的组件

Python 运行依赖来自 `pyproject.toml`：FastAPI、MarkItDown（PDF、DOCX、PPTX extra）、Pillow、python-pptx、PyMuPDF、PyYAML、filelock、Pydantic、Typer 和 Uvicorn。开发额外依赖为 HTTPX 和 pytest。精确解析版本记录在 `uv.lock`。

Node 渲染依赖来自 `package.json`：`@office-kit/pptx`、`@office-kit/pptx-preview`、`dom-to-pptx`、`pptx-automizer`、`pptxgenjs` 和 Puppeteer，锁定版本见 `package-lock.json`。扩展包另声明 Mermaid CLI 与 ECharts，版本见 `extensions/package-lock.json`。

源码还包含 `node/vendor/pixelmatch/` 的供应代码及许可证/来源记录；发布者应一并保留并复核其通知和来源文件。上表未展开全部 npm、Python 传递依赖。

## 可选的宿主工具与上游来源

PowerPoint、OfficeCLI、Node.js、浏览器、PowerShell、FFmpeg、Mediabunny、Mermaid/ECharts 扩展运行时，以及 `upstreams.lock.json` 中记录的上游源码，都不因仓库包含集成代码而自动成为随包分发或已安装组件。上游锁文件记录来源目录、版本及用途；若对应目录或二进制不存在，相应功能可能不可用。

不要把私有 `github/` 上游副本、`.tools/` 二进制、Work 运行时或 `模板库/` 内容放入公开代码仓库。模板、字体、图片和其他创作素材可能有独立许可或再分发限制，必须逐项确认；程序中的来源登记能力不是授权证明。

## 发布者核验建议

每次发布应以实际打包内容为准，检查直接和传递依赖的许可证、必要的版权声明、专有或受限资产、锁文件与源代码的一致性。本文只说明仓库当前清单和组件边界，不作法律意见，也不表示第三方权利已经完成审核。
