# Windows 桌面安装包

桌面版将 Python 与现有网页一起打包，用户不必安装 Python。论文、下载的源码、检查点和结果 PDF 存放在 `%LOCALAPPDATA%\arXiv-Translate`，卸载程序不会删除个人数据。程序仅监听本机回环地址；窗口关闭后服务停止。

安装向导始终显示安装位置，可直接编辑路径或点击“浏览”选择目录；默认位置为 `%LOCALAPPDATA%\Programs\arXiv Translate`。更改程序安装路径不会迁移或改变上述论文和结果数据目录。

安装向导默认勾选“联网自动安装缺少的编译环境”。它检查现有 `latexmk`、`pdflatex`、`xelatex`、`lualatex`、Perl 和 Noto Serif SC；仅下载缺少的组件。当前固定版本为 MiKTeX Basic 25.12、Strawberry Perl Portable 5.42.3.1 和 Noto Serif SC 2.003，来源分别是 MiKTeX、Strawberry Perl 与 Noto 官方站点，下载后按 SHA-256 校验。MiKTeX 安装到当前用户的 `%LOCALAPPDATA%\Programs\MiKTeX`；Perl 解压到所选程序目录的 `tools\strawberry`，只供本程序使用，不改系统 PATH；字体装在当前用户的字体目录。已有符合要求的组件会跳过，也可取消勾选并自行配置。

全新电脑上最多下载约 470 MB，解压和宏包缓存还需要额外磁盘空间。网络中断或校验失败会停在向导中，可重试；如自动 MiKTeX 安装失败，可返回取消自动安装，随后在程序中手动配置。首次打开仍会检查编译环境，不满足条件时不会开始翻译。MiKTeX Basic 只含基础宏包，处理具体论文时还可能按需联网下载宏包；如出现 MiKTeX Console 初始化或宏包权限提示，需要按提示完成。Poppler 是可选项：安装后可更可靠地从 PDF 首页识别 arXiv 版本、核对页数；未安装时需在 PDF 文件名中包含版本号。

## 构建

在 Windows 构建机上安装 Python 3.11+、PyInstaller 和 Inno Setup 7（含 ISCC.exe）。从仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File packaging/build_windows.ps1
```

如果 ISCC.exe 不在常见位置，可传入 `-IsccPath "C:\path\to\ISCC.exe"`。脚本先生成 `dist/arXiv-Translate/` 并执行资源自检，再生成 `dist/installer/arXiv-Translate-Setup-0.1.0-Windows.exe`。构建结果不要提交到 Git；可作为 GitHub Release 附件发布。Windows 包须在 Windows 上构建。

安装向导的品牌插画、窗口图标由 `packaging/generate_installer_assets.ps1` 在构建时生成到 `packaging/generated/`，无需下载额外素材。安装界面根据系统语言显示简体中文或英文。

安装包本身**不内嵌 MiKTeX、Perl、字体或全部宏包**，仍保持较小体积。构建安装包不会下载或运行第三方安装器；只有最终用户勾选联网配置并在自己的电脑上运行安装向导时，才会下载、校验和安装缺失组件。MiKTeX 的用户级安装与应用卸载相互独立；卸载 arXiv Translate 不会卸载 MiKTeX 或移除个人论文。用户 API Key 仍由本机页面接收，仅在当前翻译任务期间使用，不包含在安装包中。

发布前应在没有 Python、MiKTeX 和 Perl 的干净 Windows 10/11 环境测试：安装 → 联网配置编译环境 → 首次启动检查 → 上传示例论文 → 翻译、预览及下载 PDF → 卸载后确认个人数据仍在。另需覆盖断网、校验失败、已有 TeX Live/Perl、非管理员账户和自定义安装路径。此构建流程只验证安装脚本和程序自检，不能替代上述新机器验收。
