"""Windows desktop launcher for the loopback-only translation UI."""

from __future__ import annotations

import os
import queue
import shutil
import sys
import threading
import webbrowser
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path

from arxiv_translate.local_compiler import probe_latexmk
from arxiv_translate.web_app import TaskApp, make_handler


MIKTEX_URL = "https://miktex.org/download"
PERL_URL = "https://strawberryperl.com/"
FONT_URL = "https://fonts.google.com/noto/specimen/Noto+Serif+SC"


@dataclass(frozen=True)
class Dependency:
    name: str
    ready: bool
    detail: str


def bundled_root() -> Path:
    """Find read-only assets both in source checkout and PyInstaller onedir."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])).resolve()


def user_data_root() -> Path:
    """Keep papers and results outside the installed application directory."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "arXiv-Translate"


def configure_dependency_path() -> None:
    """Expose per-user MiKTeX and the installer-managed portable Perl to children."""
    local_app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    application_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else bundled_root()
    candidates = (
        application_dir / "tools" / "strawberry" / "perl" / "bin",
        application_dir / "tools" / "strawberry" / "c" / "bin",
        local_app_data / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64",
    )
    current = os.environ.get("PATH", "").split(os.pathsep)
    known = {os.path.normcase(os.path.normpath(item)) for item in current if item}
    additional = [str(path) for path in candidates if path.is_dir() and
                  os.path.normcase(os.path.normpath(str(path))) not in known]
    if additional:
        os.environ["PATH"] = os.pathsep.join([*additional, *current])


def check_dependencies() -> list[Dependency]:
    configure_dependency_path()
    latexmk, detail = probe_latexmk("latexmk")
    checks = [Dependency("latexmk", latexmk is not None, detail if latexmk is None else latexmk)]
    for program in ("pdflatex", "xelatex", "lualatex"):
        location = shutil.which(program)
        checks.append(Dependency(program, location is not None, location or f"未找到 {program}"))
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    user_fonts = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Microsoft" / "Windows" / "Fonts"
    font_ready = any((folder / name).is_file() for folder in (fonts, user_fonts)
                     for name in ("NotoSerifSC-VF.ttf", "simsun.ttc"))
    checks.append(Dependency("中文字体", font_ready,
                             "已找到 Noto Serif SC 或 SimSun" if font_ready else "未找到 Noto Serif SC 或 SimSun"))
    return checks


def ready_to_translate(checks: list[Dependency]) -> bool:
    return all(item.ready for item in checks)


def acquire_instance_lock(data_root: Path):
    """Prevent two desktop processes from writing the same paper workspace."""
    import msvcrt

    data_root.mkdir(parents=True, exist_ok=True)
    lock = (data_root / ".desktop.lock").open("a+b")
    try:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        return None
    return lock


def main() -> int:
    if sys.platform != "win32":
        print("桌面安装包目前仅支持 Windows。", file=sys.stderr)
        return 1
    if "--self-test" in sys.argv:
        root = bundled_root()
        return 0 if ((root / "web" / "index.html").is_file() and
                     (root / "arxiv_translate" / "assets" / "cas-email.jpeg.b64").is_file() and
                     (root / "packaging" / "generated" / "app.ico").is_file()) else 1
    import tkinter as tk
    from tkinter import messagebox, ttk

    assets = bundled_root() / "web"
    if not (assets / "index.html").is_file():
        messagebox.showerror("arXiv Translate", f"安装不完整，找不到界面文件：{assets}")
        return 1
    data_root = user_data_root()
    try:
        instance_lock = acquire_instance_lock(data_root)
    except OSError as error:
        messagebox.showerror("arXiv Translate", f"无法创建用户数据目录：{error}")
        return 1
    if instance_lock is None:
        messagebox.showinfo("arXiv Translate", "程序已在运行，请使用已打开的窗口。")
        return 0

    window = tk.Tk()
    window.title("arXiv Translate")
    icon = bundled_root() / "packaging" / "generated" / "app.ico"
    if icon.is_file():
        try:
            window.iconbitmap(default=str(icon))
        except tk.TclError:
            pass
    window.geometry("760x620")
    window.minsize(700, 570)
    window.configure(bg="#F3F6FB")

    ui_font = "Microsoft YaHei UI"
    navy = "#17284A"
    muted = "#64748B"
    blue = "#355CE6"
    green = "#16825D"
    header = tk.Frame(window, bg=navy, padx=30, pady=22)
    header.pack(fill="x")
    tk.Label(header, text="arXiv Translate", bg=navy, fg="white",
             font=(ui_font, 19, "bold")).pack(anchor="w")
    tk.Label(header, text="把 arXiv 论文翻译成中文 PDF", bg=navy, fg="#C9D5EC",
             font=(ui_font, 10)).pack(anchor="w", pady=(4, 0))

    body = tk.Frame(window, bg="#F3F6FB", padx=30, pady=22)
    body.pack(fill="both", expand=True)
    status_title = tk.StringVar(value="正在检查编译环境…")
    status_text = tk.StringVar(value="请稍候，正在确认 LaTeX 和中文字体是否可用。")
    tk.Label(body, textvariable=status_title, bg="#F3F6FB", fg=navy,
             font=(ui_font, 17, "bold")).pack(anchor="w")
    tk.Label(body, textvariable=status_text, bg="#F3F6FB", fg=muted,
             font=(ui_font, 10), wraplength=680, justify="left").pack(anchor="w", pady=(5, 17))

    card = tk.Frame(body, bg="white", highlightbackground="#DCE5F1", highlightthickness=1)
    card.pack(fill="both", expand=True)
    card_header = tk.Frame(card, bg="white", padx=20, pady=14)
    card_header.pack(fill="x")
    tk.Label(card_header, text="环境检查", bg="white", fg=navy,
             font=(ui_font, 12, "bold")).pack(side="left")
    check_count = tk.StringVar(value="检查中")
    tk.Label(card_header, textvariable=check_count, bg="#EDF3FF", fg=blue,
             font=(ui_font, 9, "bold"), padx=10, pady=4).pack(side="right")
    tk.Frame(card, bg="#E9EEF5", height=1).pack(fill="x")
    check_rows = tk.Frame(card, bg="white", padx=20, pady=10)
    check_rows.pack(fill="both", expand=True)

    help_row = tk.Frame(body, bg="#F3F6FB")
    tk.Label(help_row, text="缺少组件？", bg="#F3F6FB", fg=muted,
             font=(ui_font, 9)).pack(side="left")
    for title, target in (("安装 MiKTeX", MIKTEX_URL), ("安装 Perl", PERL_URL),
                          ("下载中文字体", FONT_URL)):
        link = tk.Label(help_row, text=title, bg="#F3F6FB", fg=blue,
                        cursor="hand2", font=(ui_font, 9, "underline"))
        link.pack(side="left", padx=(12, 0))
        link.bind("<Button-1>", lambda _event, url=target: webbrowser.open(url))

    actions = tk.Frame(body, bg="#F3F6FB")
    actions.pack(fill="x", pady=(18, 0))
    start_button = tk.Button(actions, text="打开翻译界面  →", font=(ui_font, 16, "bold"),
                             bg="#BAC7E3", fg="white", disabledforeground="#F7F9FF",
                             activebackground="#294CCB", activeforeground="white",
                             relief="flat", bd=0, cursor="hand2", pady=13,
                             state="disabled")
    start_button.pack(fill="x")
    footer = tk.Frame(body, bg="#F3F6FB")
    footer.pack(fill="x", pady=(13, 0))
    refresh_button = ttk.Button(footer, text="重新检测环境")
    refresh_button.pack(side="left")
    details_button = ttk.Button(footer, text="查看详细信息")
    details_button.pack(side="left", padx=(9, 0))
    ttk.Button(footer, text="退出", command=lambda: close()).pack(side="right")

    server: ThreadingHTTPServer | None = None
    app: TaskApp | None = None
    address: str | None = None
    pending: queue.Queue[list[Dependency]] = queue.Queue()
    last_checks: list[Dependency] = []

    def show_details() -> None:
        dialog = tk.Toplevel(window)
        dialog.title("环境检查详情")
        dialog.geometry("700x420")
        dialog.minsize(560, 320)
        pane = ttk.Frame(dialog, padding=16)
        pane.pack(fill="both", expand=True)
        ttk.Label(pane, text="编译环境详情", font=(ui_font, 14, "bold")).pack(anchor="w", pady=(0, 12))
        text_box = tk.Text(pane, wrap="word", font=("Consolas", 10),
                           bg="#F8FAFD", fg=navy, relief="flat", padx=12, pady=12)
        text_box.pack(fill="both", expand=True)
        lines = [f"{'✓' if item.ready else '✗'}  {item.name}\n{item.detail}"
                 for item in last_checks]
        text_box.insert("1.0", "\n\n".join(lines) if lines else "环境检查仍在进行中，请稍候。")
        text_box.configure(state="disabled")
        ttk.Button(pane, text="关闭", command=dialog.destroy).pack(anchor="e", pady=(12, 0))

    def render_checks(checks: list[Dependency]) -> None:
        for child in check_rows.winfo_children():
            child.destroy()
        for item in checks:
            row = tk.Frame(check_rows, bg="white", pady=8)
            row.pack(fill="x")
            color = green if item.ready else "#C65344"
            tk.Label(row, text="✓" if item.ready else "!", bg="#E7F5EE" if item.ready else "#FFF0EC",
                     fg=color, width=2, font=(ui_font, 11, "bold"), pady=2).pack(side="left")
            tk.Label(row, text=item.name, bg="white", fg=navy,
                     font=(ui_font, 10, "bold"), width=10, anchor="w").pack(side="left", padx=(12, 0))
            summary = "已就绪" if item.ready else "未就绪 · 点击“查看详细信息”了解原因"
            tk.Label(row, text=summary, bg="white", fg=muted if item.ready else color,
                     font=(ui_font, 9), anchor="w").pack(side="left", padx=(10, 0))

    def refresh() -> None:
        start_button.configure(state="disabled", bg="#BAC7E3")
        refresh_button.configure(state="disabled")
        status_title.set("正在检查编译环境…")
        status_text.set("请稍候，正在确认 LaTeX 和中文字体是否可用。")
        check_count.set("检查中")
        help_row.pack_forget()
        def run_check() -> None:
            try:
                pending.put(check_dependencies())
            except Exception as error:
                pending.put([Dependency("编译环境检查", False, str(error))])
        threading.Thread(target=run_check, daemon=True).start()
        window.after(100, show_checks)

    def show_checks() -> None:
        nonlocal last_checks
        try:
            checks = pending.get_nowait()
        except queue.Empty:
            window.after(100, show_checks)
            return
        refresh_button.configure(state="normal")
        last_checks = checks
        render_checks(checks)
        passed = sum(item.ready for item in checks)
        check_count.set(f"{passed}/{len(checks)} 项通过")
        if ready_to_translate(checks):
            if server is None:
                status_title.set("准备就绪，可以开始翻译")
                status_text.set("编译环境检查通过。首次处理论文时，MiKTeX 可能按需下载额外宏包。")
            else:
                status_title.set("翻译界面正在运行")
                status_text.set(f"已在本机打开 {address}。关闭此窗口会停止翻译服务。")
            start_button.configure(state="normal", bg=blue)
            help_row.pack_forget()
        else:
            status_title.set("还差一点，先补齐编译环境")
            status_text.set("有组件未就绪。查看详细信息并补装后，点击“重新检测环境”。")
            start_button.configure(state="disabled", bg="#BAC7E3")
            help_row.pack(fill="x", pady=(12, 0), before=actions)

    def start() -> None:
        nonlocal server, app, address
        if server is None:
            try:
                data_root.mkdir(parents=True, exist_ok=True)
                app = TaskApp(data_root, web=assets)
                server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
                threading.Thread(target=server.serve_forever, daemon=True).start()
                address = f"http://127.0.0.1:{server.server_port}/"
            except OSError as error:
                server = None
                app = None
                messagebox.showerror("无法启动", str(error))
                return
        status_title.set("翻译界面正在运行")
        status_text.set(f"已在本机打开 {address}。关闭此窗口会停止翻译服务。")
        start_button.configure(text="重新打开翻译界面  →")
        if not webbrowser.open(address):
            messagebox.showinfo("手动打开", f"请在浏览器中打开 {address}")

    def close() -> None:
        if app is not None:
            with app.lock:
                busy = app.busy
            if busy and not messagebox.askyesno("翻译进行中", "关闭程序会中断当前翻译任务。确定退出吗？"):
                return
        if server is not None:
            server.shutdown()
            server.server_close()
        window.destroy()

    start_button.configure(command=start)
    refresh_button.configure(command=refresh)
    details_button.configure(command=show_details)
    window.protocol("WM_DELETE_WINDOW", close)
    refresh()
    try:
        window.mainloop()
    finally:
        import msvcrt

        instance_lock.seek(0)
        msvcrt.locking(instance_lock.fileno(), msvcrt.LK_UNLCK, 1)
        instance_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
