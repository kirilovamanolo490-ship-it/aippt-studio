# 公开快照验证

日期：2026-10-15。检查对象是本次发布目录中的代码副本，Python 模块导入指向该副本；原工作区继续保留。

## 已运行

| 检查 | 结果与范围 |
| --- | --- |
| Python 语法解析 | 318 个 Python 文件解析成功 |
| npm 锁文件安装 | 在公开副本的核心目录执行 `npm ci --ignore-scripts --no-audit --no-fund`，成功安装 209 个包；依赖目录不进入 Git |
| 定向回归 | 644 项通过、0 失败、0 错误；覆盖 PPTX 新建/另存/编辑/CLI、图片几何、设计计划、意图优先级和可移植交接包 |
| 合成路径匿名化回归 | 1 项通过；一条测试用例路径由本机用户名改成 `example`，测试语义保持 |
| 敏感内容标记检查 | 检查私钥、常见 token、本机身份路径、带凭据 URL 等模式；命中的私钥头和 URL 仅是刻意拒绝危险输入的合成测试字符串，未发现真实凭据 |
| 核心源码身份 | 发布时逐文件核对 Python 核心与 Node 执行源码，文件清单见 `source-manifest.json` |

首次回归在未安装 Node 依赖的公开目录中暴露了缺少 npm 包与 OfficeCLI 的环境条件。按锁文件安装后重新运行相关范围，取得以上 644 项通过结果。

## 复现定向检查

在仓库根目录，完成文档中的 Python 与 npm 安装后运行：

```sh
./aippt-studio/.venv-macos/bin/python -B -m pytest -q -p no:cacheprovider \
  aippt-studio/tests/test_pptx_file_ops.py \
  aippt-studio/tests/test_pptx_edit.py \
  aippt-studio/tests/test_pptx_cli.py \
  aippt-studio/tests/test_image_geometry.py \
  aippt-studio/tests/test_design_plan.py \
  aippt-studio/tests/test_authoring_intent_precedence.py \
  aippt-studio/tests/test_presentation_intent.py \
  aippt-studio/tests/test_portable_packets.py \
  -k 'not test_preview_slide_cli_supports_managed_officecli_backend'
```

## 本次未覆盖

上述命令显式排除一个需要 Studio 管理的 OfficeCLI 二进制的预览测试；仓库不分发这个工具，因此不能把它算作通过。浏览器预览使用本机已安装的 Chrome，Python 包使用现有的已验证 Studio 环境；此次没有声称在全新机器上重建了完整 Python/Office 环境。

本次没有运行全部测试，也没有验证所有外部生成服务、所有上游适配器、Windows 原生 PowerPoint、动画或视频播放。此记录验证代码与发布范围，不代表案例内业务数据的真实性、第三方权利或客户接受。
