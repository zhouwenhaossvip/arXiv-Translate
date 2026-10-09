# arXiv Translate v0.1

在自己的电脑上上传 arXiv 论文 PDF，选 DeepSeek Flash 或 OpenAI GPT-4o mini，输入自己的 API Key，生成可预览和下载的中文 PDF。程序按论文版本查找 LaTeX 源码，先验证原文可编译，再翻译正文并编译中文 PDF。公式、引用和大部分结构会保留，但译文与排版仍需人工检查。

这是供个人本地运行的版本，**不是公网翻译服务**。论文内容会发送给所选模型服务商，可能产生 API 费用；LaTeX 编译不是完整的安全沙箱，请只处理可信论文源码。

## 克隆后启动（Windows）

1. 安装 Python 3.11 或更新版本、Git、MiKTeX（或 TeX Live）、Perl（MiKTeX 的 `latexmk` 在 Windows 上需要，例如 Strawberry Perl）和一个中文字体（推荐 Noto Serif SC）。确保 `python`、`latexmk`、`xelatex` 可在 PowerShell 中运行。MiKTeX 建议先完成首次设置，并启用缺失宏包的按需安装。可选安装 Poppler：提供 `pdftotext` 自动读取 PDF 首页的 arXiv 版本号，以及 `pdfinfo`/`pdftoppm` 用于页数检查和质检预览。
2. 克隆项目并进入目录：

   ```powershell
   git clone https://github.com/zhouwenhaossvip/arXiv-Translate.git
   cd arXiv-Translate
   ```

3. 检查本地编译环境：

   ```powershell
   python -m arxiv_translate.stage1 doctor
   python -m arxiv_translate.stage3 doctor
   ```

4. 启动本地页面，然后在浏览器打开 <http://127.0.0.1:8765/>：

   ```powershell
   python -m arxiv_translate.web_app
   ```

本项目的 Python 核心只使用标准库，因此从仓库根目录运行时不需要 `pip install`。网页会在使用过程中按需创建本地 `pdf/`、`latex/`、`results/`、`output/` 等目录；这些个人数据不会被 Git 跟踪。上传文件应包含带版本的 arXiv 编号，如 `2511.02841v2.pdf`；若安装了 Poppler，也可从 PDF 首页自动识别。没有可用 LaTeX 源码、版本不一致、原文编译失败或模型请求失败时，任务会停下并显示原因。更多页面说明见 [web/README.md](web/README.md)。

API Key 仅用于本次本地任务，不应写入源码、提交到 GitHub 或发给他人。服务只监听 `127.0.0.1`；不要修改为公网监听地址。若要给陌生用户提供托管服务，需另行实现身份认证、任务隔离、受限编译环境、HTTPS 与密钥管理。

## 工作原理与命令行

项目分三步：阶段 1 建立原文编译基线，阶段 2 生成中文 LaTeX 草稿，阶段 3 在本机编译并检查中文 PDF。编译在临时副本中进行，不会修改 `pdf/`、`latex/` 或阶段 2 的草稿。以下命令行示例供已有本地论文和源码的开发者使用；首次克隆后可直接使用上面的网页流程。先前的在线原文编译方式仍可显式选用，方便比较结果。

## 本地 Web 页面

已提供聚焦于“新建翻译任务”的 [本地页面](web/README.md)。在项目根目录运行 `python -m arxiv_translate.web_app`，打开 `http://127.0.0.1:8765/`，即可上传 PDF、选择模型、输入 API Key、查看进度并预览或下载中文 PDF。页面会接入现有的阶段一至三流程；本机没有对应 LaTeX 源码时会按 arXiv 版本号下载。请先完成下面的本地编译环境配置。此页面只适合本机个人使用，不能直接公开部署。

## 准备本地编译环境

安装并完成 MiKTeX 或 TeX Live 的首次设置，确保 `latexmk`、`pdflatex`、`xelatex` 和 `lualatex` 可在命令行中找到。Windows 上的 MiKTeX 还需要 Perl（例如 Strawberry Perl）在 `PATH` 中。在 Windows 的 MiKTeX Console 中，可启用缺失宏包的按需安装。首次编译可能因此需要联网；无人值守运行前应完成这一设置。运行以下命令检查 `latexmk` 是否真正可用：

```powershell
python -m arxiv_translate.stage1 doctor
```

若系统中安装了多个发行版，可用 `--latexmk "D:\path\to\latexmk.exe"` 指定使用哪一个。仅检测到可执行文件并不代表安装已初始化；`doctor` 会实际运行版本检查。

## 运行

使用 Python 3.11 或更新版本，在项目根目录执行：

```powershell
python -m arxiv_translate.stage1 scan
python -m arxiv_translate.stage1 baseline
```

第一条命令只扫描本地文件；第二条命令默认用本机 `latexmk` 编译。可用 `--id 2412.17114v3` 先测试一篇；`--force` 可重新编译已有结果。每篇论文默认最多编译 180 秒，可用 `--timeout` 修改。执行 `python -m arxiv_translate.stage1 --help` 查看全部参数。

本地编译结果保存在 `results/stage1/local/`：

- `baseline.json`：本地编译状态、编译器版本和耗时。
- `summary.md`：便于阅读的逐篇汇总。
- `compiled/`：成功生成的原文 PDF。
- `previous/`：重跑后未通过校验时，从 `compiled/` 移出的旧 PDF；保留供诊断，不当作合格产物。
- `logs/`：逐篇 `latexmk` 输出和错误日志。

论文清单仍保存在 `results/stage1/manifest.json`。`compiled` 表示本机环境生成了带有效 PDF 文件头的文件，且没有检测到未解析文献引用；仍需用页数和内容核对排版。`compile_failed` 的日志需要进一步判断是否缺少宏包、字体或其他文件；`validation_failed` 表示生成 PDF 后检测到缺失文献引用或自带 `.bbl` 被破坏。`compiler_unavailable` 和 `timeout` 不会当成论文源码错误。默认禁用 shell escape，也忽略 `latexmkrc`；这提高了安全性，但少数依赖自定义构建规则的论文可能因此失败。LaTeX 源码仍可能读取本机可访问的文件，因此不要把临时工作目录当作完整的安全沙箱。

只要本地基线中仍有编译或校验失败，命令就返回非零退出码，方便后续自动化阻止不完整论文进入翻译流程；逐篇原因以 `summary.md` 和日志为准。

本地编译器会在临时副本中做三项已知兼容处理，并在结果的 `compatibility_actions` 中逐项记录：若 Elsevier CAS 模板缺少邮件图标，补入来自 CTAN 模板包的原始图标；若源码自带与主文件同名的 `.bbl`，则保留它并跳过 BibTeX，避免误用系统中的同名 `.bib`；对一篇论文的 TikZ 图注采用保留下标外观的兼容写法。不会改动 `latex/` 中的原始文件。失败结果不会被缓存为最终结果，解决环境问题后直接重跑 `baseline` 即可；已成功且源码、编译器及编译流程版本不变的论文仍会跳过。

若安装了 Poppler 的 `pdfinfo`，本地基线还会比较原 PDF 与重编 PDF 的页数，在 `summary.md` 用 ⚠ 标记差异。页数一致只是初步检查，不代表两份 PDF 的内容和排版完全相同；没有 `pdfinfo` 也不妨碍编译。

如果仍需运行旧版在线基线，可显式执行 `python -m arxiv_translate.stage1 baseline --backend online`；其结果继续保存在 `results/stage1/`，不会覆盖本地结果。此选项会将源码包发送至 [LaTeX.Online](https://latexonline.cc/)，需要网络访问。

## 阶段 2：生成中文 LaTeX 草稿

阶段 2 先保守提取正文、标题、摘要、关键词和说明文字中的纯文本片段，也识别 `\section{\uppercase{...}}` 一类标题；把句中 LaTeX 命令与公式替换成必须原样返回的占位符。注释、图表代码和参考文献不会送去翻译。模型返回后会核对占位符并恢复原始 LaTeX，缺失或乱序则拒绝保存。旧检查点继续使用创建时的提取规则，不会因提取器升级而更改片段编号；旧草稿中遗漏的摘要、关键词等需人工补译。只跟踪主文件中的字面 `\input` / `\include` / `\subfile`，因此动态生成的文件路径或复杂自定义宏仍需人工检查。先运行无需联网和密钥的计划命令，查看片段数量及预览：

```powershell
python -m arxiv_translate.stage2 plan --id 2608.17220v1
```

真实翻译可使用 [OpenAI Responses API 的结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)，也可使用 [DeepSeek Responses API](https://api-docs.deepseek.com/zh-cn/api/create-response/) 的 `deepseek-flash`。分别在自己的环境中设置 `OPENAI_API_KEY` 或 `DEEPSEEK_API_KEY`；不要把密钥写进源码或提交到 GitHub。程序根据以 `deepseek-` 开头的模型名自动选择 DeepSeek，并关闭其默认思考模式以控制翻译成本。下例每次最多翻译 20 个新片段，便于控制费用并可重复执行以续跑：

```powershell
python -m arxiv_translate.stage2 translate --id 2608.17220v1 --model gpt-4o-mini
python -m arxiv_translate.stage2 translate --id 2608.17220v1 --model deepseek-flash
```

确认计划和费用后，可用 `--all` 翻译剩余片段。阶段 2 默认只允许阶段 1 `compiled` 的论文；仅在审查源码后才使用 `--allow-unverified`。每批结果会保存到 `results/stage2/<文章 ID>/translations.json`；全部片段完成后才生成 `translated/` 源码树及 `report.json`。中断后以同一模型重跑即可续传；源文件或模型变化时会拒绝沿用旧检查点。不会修改 `latex/` 中的原始源码，也不会在 `plan` 或缺少密钥时发出 API 请求。

这是一份供下一阶段编译和人工校对的草稿，不是最终中文 PDF。当前提取器偏向保护语法：复杂宏、图表、参考文献中的文本可能保持英文；纯文本片段被命令拆开时也可能需要人工调整语句衔接。中文字体、编译引擎和排版验证留待下一阶段处理。模型调用可能产生费用，且论文片段会发送至所选 API 服务。

## 阶段 3：本地编译中文 PDF

阶段 3 只接受阶段 1 已通过、阶段 2 已完整生成草稿的论文。它将草稿复制到临时目录，在副本的主文件中加入中文字体配置，使用 XeLaTeX 编译；原始 LaTeX 和阶段 2 草稿均不改动。默认先尝试 `xeCJK`，若模板或宏包冲突则回退到 `fontspec`（此模式会改变拉丁字体，需重点检查排版）。Windows 上如检测到 Noto Serif SC 则优先使用，否则用 `SimSun`；其他系统默认 `FandolSong-Regular`。可用 `--font` 指定已安装的字体，也可用 `--cjk-mode` 固定编译方式。先检查工具，再逐篇编译：

```powershell
python -m arxiv_translate.stage3 doctor
python -m arxiv_translate.stage3 compile --id 2511.02841v2
python -m arxiv_translate.stage3 compile --all
```

成功的 PDF 保存在 `output/pdf/`，逐篇状态、页数、缺字数和每次尝试的日志保存在 `results/stage3/<文章 ID>/`。同一草稿、字体和编译器版本的结果会复用；修改草稿后会生成新版本 PDF。检测到缺失字形会拒绝将 PDF 标记为合格。中文译文通常比英文更长，页数变化本身不是失败。若模板冲突、缺字体或宏包，查看报告指向的 `compile-xecjk.log` / `compile-fontspec.log`；可以指定 `--font`、`--timeout`，修复环境后重试。仍需人工检查字形、图片、公式、图表、参考文献和译文质量；能够编译不等于可直接发表。

## 译稿质量验收

在完成阶段 2 和阶段 3 后运行验收；也可以先检查尚未重新编译的草稿，报告会把旧 PDF 明确标为“已过期”：

```powershell
python -m arxiv_translate.qa --id 2511.02841v2
python -m arxiv_translate.qa --all
```

逐篇 `results/qa/<文章 ID>/report.md` 汇总疑似漏译字段、原文残留、引用和公式结构变化、缺字及超宽文字盒警告，并附 PDF 的逐页预览；同目录的 `report.json` 可供自动化使用。`blocked` 表示 PDF 已过期或有结构/缺字问题，`needs_review` 表示需要人工核对，`pass` 表示这些自动检查未发现问题；任何非 `pass` 都会使命令返回非零退出码。`pass` 仍不代表译文准确或视觉排版完美，尤其是图中文字、表格和参考文献需要人工复核。只有安装 Poppler 的 `pdfinfo` 和 `pdftoppm` 才能生成完整的页数与逐页预览；用 `--no-previews` 可跳过图片生成。

## 开发验证

```powershell
python -m unittest discover -s tests -v
```

本地编译由 `arxiv_translate/local_compiler.py` 封装，线上编译由 `online_compile` 函数封装。两种编译基线分开保存，方便比较。

## 示例数据

开发期间使用的论文 PDF、arXiv LaTeX 源码、翻译检查点、编译产物和质检预览都只保留在开发者本机，不随 GitHub 仓库发布。克隆后的目录一开始没有论文；请通过网页上传自己的 PDF。

## 许可证

本项目原创代码采用 [MIT 许可证](LICENSE)。`arxiv_translate/assets/cas-email.jpeg.b64` 是来自 CTAN 模板包的第三方资源，按其原有 LPPL 1.3c 许可证提供；来源和校验值见 [素材说明](arxiv_translate/assets/README.md)。仓库不包含论文 PDF、论文 LaTeX 源码或翻译结果；这些文件的权利归各自权利人。
