import ctypes
import os
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageOps, ImageTk
import minecraft_launcher_lib as mll

from . import updater


ROOT_DIR = (
    Path(getattr(sys, "_MEIPASS"))
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parents[2]
)

COLORS = {
    "background": "#1f1f1f",
    "panel": "#2b2b2b",
    "panel_light": "#383838",
    "overlay": "#202020",
    "accent": "#3c8527",
    "accent_hover": "#4c9a31",
    "accent_light": "#8bc34a",
    "text": "#f2f2f2",
    "muted": "#b5b5b5",
    "line": "#4b4b4b",
    "warning": "#f3ce68",
    "success": "#7abf54",
    "danger": "#e36c61",
    "disabled": "#777d77",
}

STATUS_FILE_ENV = "STODO_LAUNCHER_STATUS_FILE"
GAME_MUTEX_NAME = "Local\\StodoMcLauncherGame"


def minecraft_directory():
    return Path(mll.utils.get_minecraft_directory())


def get_installed_fabric_versions(game_dir=None):
    game_dir = game_dir or minecraft_directory()
    versions = []
    for version in mll.utils.get_installed_versions(game_dir):
        version_id = version["id"]
        if not version_id.startswith("fabric-loader-"):
            continue
        version_json = game_dir / "versions" / version_id / (version_id + ".json")
        if version_json.exists():
            versions.append((version_json.stat().st_mtime, version_id))
    return [version for _, version in sorted(versions, reverse=True)]


def launch_command(fabric_version):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--backend", "--fabric-version", fabric_version]

    launcher_script = ROOT_DIR / "main.py"
    if launcher_script.exists():
        return [
            sys.executable,
            str(launcher_script),
            "--backend",
            "--fabric-version",
            fabric_version,
        ]
    raise FileNotFoundError("Die Startdatei main.py wurde nicht gefunden.")


def _backend_process_environment(status_file):
    environment = os.environ.copy()
    environment[STATUS_FILE_ENV] = str(status_file)
    if getattr(sys, "frozen", False):
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return environment


def _backend_working_directory():
    if getattr(sys, "frozen", False):
        return Path.home()
    return ROOT_DIR


def _minecraft_is_running():
    if os.name != "nt":
        return False

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenMutexW.restype = ctypes.c_void_p
    kernel32.OpenMutexW.argtypes = [
        ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p,
    ]
    handle = kernel32.OpenMutexW(0x00100000, False, GAME_MUTEX_NAME)
    if not handle:
        error = ctypes.get_last_error()
        if error == 2:
            return False
        raise ctypes.WinError(error)

    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle(handle)
    return True


class StodoLauncher:
    def __init__(self, root, current_version):
        self.root = root
        self.current_version = current_version
        with Image.open(ROOT_DIR / "background.jpg") as background:
            self._background_image = background.convert("RGB")
        self._background_photo = None
        self.events = queue.Queue()
        self.process = None
        self.launching = False
        self.update_checking = getattr(sys, "frozen", False)
        self.pending_update = None
        self.update_replacement_scheduled = False
        self.last_backend_status = ""
        self.status_file = self._create_status_file()
        self.game_status_error = False
        try:
            self.game_running = _minecraft_is_running()
        except OSError:
            self.game_running = False
            self.game_status_error = True
        self.state = "Bereit zum Spielen"
        try:
            self.fabric_versions = get_installed_fabric_versions()
            self.fabric_versions_error = False
        except Exception:
            self.fabric_versions = []
            self.fabric_versions_error = True
        self.fabric_version = tk.StringVar()
        self._make_window()
        self._make_layout()
        self._sync_play_button()
        if self.update_checking:
            self._set_status("Prüfe auf Launcher-Updates ...", COLORS["warning"])
        elif self.game_running:
            self._set_status("Minecraft läuft")
        elif self.game_status_error:
            self._set_status("Spielstatus konnte nicht geprüft werden.", COLORS["danger"])
            self.root.after_idle(lambda: messagebox.showerror(
                "Status konnte nicht geprüft werden",
                "Der Minecraft-Status konnte nicht ermittelt werden. Der Start bleibt "
                "zur Sicherheit deaktiviert.",
                parent=self.root,
            ))
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        if self.fabric_versions_error:
            self.root.after_idle(lambda: messagebox.showerror(
                "Fehler beim Laden",
                "Die installierten Fabric-Versionen konnten nicht ermittelt werden.",
                parent=self.root,
            ))
        if self.update_checking:
            self.root.after_idle(self._start_update_check)
        self.root.after(350, self._poll)

    def _create_status_file(self):
        descriptor, path = tempfile.mkstemp(prefix="stodo-launcher-", suffix=".status")
        os.close(descriptor)
        return Path(path)

    def _make_window(self):
        self.root.title("Stodo Mc Launcher")
        self.root.geometry("1120x720")
        self.root.minsize(900, 600)
        self.root.configure(bg=COLORS["background"])
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Fabric.TCombobox", fieldbackground=COLORS["panel_light"],
            background=COLORS["panel_light"], foreground=COLORS["text"],
            arrowcolor=COLORS["accent_light"], bordercolor=COLORS["line"],
            lightcolor=COLORS["line"], darkcolor=COLORS["line"],
            padding=(10, 8), arrowsize=14,
        )
        style.map(
            "Fabric.TCombobox",
            fieldbackground=[("readonly", COLORS["panel_light"])],
            foreground=[("readonly", COLORS["text"])],
            bordercolor=[("focus", COLORS["accent"])],
            lightcolor=[("focus", COLORS["accent"])],
            darkcolor=[("focus", COLORS["line"])],
            arrowcolor=[("active", COLORS["accent_light"])],
        )
        self.root.option_add("*TCombobox*Listbox.background", COLORS["panel"])
        self.root.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", COLORS["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", COLORS["text"])
        self.root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))
        icon = ROOT_DIR / "launcher_icon.ico"
        if icon.exists():
            try:
                self.root.iconbitmap(str(icon))
            except tk.TclError:
                pass

    def _make_layout(self):
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        self.content = tk.Frame(self.root, bg=COLORS["background"])
        self.content.grid(row=0, column=0, sticky="nsew")
        self.content.grid_rowconfigure(1, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        self._make_header()
        self._make_home()
        self._make_footer()

    def _make_header(self):
        header = tk.Frame(self.content, bg=COLORS["background"], height=68)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        brand = tk.Frame(header, bg=COLORS["background"])
        brand.pack(side="left", padx=(24, 20))
        mark = tk.Canvas(
            brand, width=34, height=34, bg=COLORS["background"], highlightthickness=0,
        )
        mark.pack(side="left")
        self._draw_cube(mark, 4, 4, 26)
        tk.Label(
            brand, text="STODO", fg=COLORS["text"], bg=COLORS["background"],
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left", padx=(10, 0))
        tk.Frame(header, bg=COLORS["line"], width=1, height=26).pack(
            side="left", padx=(0, 20),
        )
        tk.Label(
            header, text="MINECRAFT: JAVA EDITION", fg=COLORS["muted"],
            bg=COLORS["background"], font=("Segoe UI", 9, "bold"),
        ).pack(side="left")
        tk.Frame(header, bg=COLORS["line"], height=1).pack(side="bottom", fill="x")

    def _make_home(self):
        home = tk.Frame(self.content, bg=COLORS["background"])
        home.grid(row=1, column=0, sticky="nsew", padx=24, pady=(18, 12))
        home.grid_rowconfigure(1, weight=1)
        home.grid_columnconfigure(0, weight=1)

        intro = tk.Frame(home, bg=COLORS["background"])
        intro.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        tk.Label(
            intro, text="Was geht du Stricher,", fg=COLORS["muted"],
            bg=COLORS["background"], font=("Segoe UI", 10),
        ).pack(anchor="w")
        tk.Label(
            intro, text="Wilkommen im privaten Stodo Launcher", fg=COLORS["text"],
            bg=COLORS["background"], font=("Segoe UI", 20, "bold"),
        ).pack(anchor="w", pady=(1, 0))

        cards = tk.Frame(home, bg=COLORS["background"])
        cards.grid(row=1, column=0, sticky="nsew")
        cards.grid_rowconfigure(0, weight=1)
        cards.grid_columnconfigure(0, weight=1)

        self.hero = tk.Frame(cards, bg=COLORS["panel"])
        self.hero.grid(row=0, column=0, sticky="nsew")
        self.hero.grid_rowconfigure(0, weight=1)
        self.hero.grid_columnconfigure(0, weight=1)
        self.scene = tk.Canvas(
            self.hero, bg=COLORS["panel"], highlightthickness=0, bd=0,
        )
        self.scene.grid(row=0, column=0, sticky="nsew")
        self.scene.bind("<Configure>", self._draw_background)

        self.hero_copy = tk.Frame(
            self.scene, bg=COLORS["overlay"], highlightbackground=COLORS["line"],
            highlightthickness=1,
        )
        self.scene.create_window(28, 27, window=self.hero_copy, anchor="nw", tags="copy")
        copy_header = tk.Frame(self.hero_copy, bg=COLORS["overlay"])
        copy_header.pack(anchor="w", padx=18, pady=(15, 0))
        tk.Frame(
            copy_header, bg=COLORS["accent"], width=3, height=14,
        ).pack(side="left", padx=(0, 8))
        tk.Label(
            copy_header, text="SERVER MODPACK", fg=COLORS["accent_light"],
            bg=COLORS["overlay"], font=("Segoe UI", 9, "bold"),
        ).pack(side="left")
        tk.Label(
            self.hero_copy, text="Es ist nur möglich\nmit den festgelegten Mods\nzu spielen.",
            fg=COLORS["text"], bg=COLORS["overlay"], justify="left",
            font=("Segoe UI", 17, "bold"),
        ).pack(anchor="w", padx=18, pady=(9, 16))

        controls = tk.Frame(self.hero, bg=COLORS["panel"], height=94)
        controls.grid(row=1, column=0, sticky="ew")
        controls.grid_propagate(False)
        game_info = tk.Frame(controls, bg=COLORS["panel"])
        game_info.pack(side="left", fill="y", padx=18, pady=15)
        tk.Label(
            game_info, text="MINECRAFT", fg=COLORS["text"],
            bg=COLORS["panel"], font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")
        tk.Label(
            game_info, text="Java Edition  •  Fabric", fg=COLORS["muted"],
            bg=COLORS["panel"], font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(4, 0))
        picker = tk.Frame(controls, bg=COLORS["panel"])
        picker.pack(side="left", fill="y", pady=15)
        tk.Label(
            picker, text="FABRIC VERSION", fg=COLORS["muted"],
            bg=COLORS["panel"], font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", pady=(0, 5))
        self.fabric_picker = ttk.Combobox(
            picker, textvariable=self.fabric_version, state="readonly",
            values=self.fabric_versions or ("Keine Fabric-Version gefunden",),
            width=30, style="Fabric.TCombobox",
        )
        self.fabric_picker.pack(side="left")
        if self.fabric_versions:
            self.fabric_picker.current(0)
        else:
            self.fabric_version.set("Keine Fabric-Version gefunden")
            self.fabric_picker.configure(state="disabled")
        self._button(
            picker, "↻", self._refresh_fabric_versions,
            bg=COLORS["panel_light"], hover=COLORS["line"], width=3,
            height=35, font=("Segoe UI", 11, "bold"),
        ).pack(side="left", padx=(6, 0), fill="y")
        self.play_button = self._button(
            controls, "▶   PLAY", self._launch, bg=COLORS["accent"],
            hover=COLORS["accent_hover"], height=52, width=20,
            font=("Segoe UI", 13, "bold"),
        )
        self.play_button.pack(side="right", padx=18, pady=18)
        if not self.fabric_versions:
            self.play_button.configure(state="disabled")
            self.state = "Fabric installieren, um zu spielen"

    def _make_footer(self):
        footer = tk.Frame(self.content, bg=COLORS["background"], height=45)
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_propagate(False)
        tk.Frame(footer, bg=COLORS["line"], height=1).pack(fill="x")
        self.status_dot = tk.Canvas(
            footer, width=12, height=12, bg=COLORS["background"], highlightthickness=0,
        )
        self.status_dot.pack(side="left", padx=(25, 7), pady=15)
        self.status_dot.create_oval(2, 2, 10, 10, fill=COLORS["success"], outline="")
        self.status_label = tk.Label(
            footer, text=self.state, fg=COLORS["muted"], bg=COLORS["background"],
            font=("Segoe UI", 9),
        )
        self.status_label.pack(side="left", pady=12)
        self.version_label = tk.Label(
            footer, text=f"Version {self.current_version}", fg=COLORS["muted"],
            bg=COLORS["background"], font=("Segoe UI", 9),
        )
        self.version_label.pack(side="right", padx=(7, 25), pady=12)

    def _button(self, parent, text, command, bg, hover, fg=COLORS["text"], height=40,
                width=None, font=("Segoe UI", 9, "bold")):
        button = tk.Button(
            parent, text=text, command=command, bg=bg, fg=fg,
            activebackground=hover, activeforeground=fg,
            relief="raised", bd=4, overrelief="raised", cursor="hand2", height=1,
            font=font, padx=14, pady=max(2, int((height - 30) / 2)),
            highlightthickness=2, highlightbackground=COLORS["overlay"],
            highlightcolor=COLORS["overlay"],
        )
        if width:
            button.configure(width=width)
        def on_enter(event):
            if event.widget["state"] != "disabled":
                event.widget.configure(bg=hover)

        def on_leave(event):
            if event.widget["state"] != "disabled":
                event.widget.configure(bg=bg)

        button.bind("<Enter>", on_enter)
        button.bind("<Leave>", on_leave)
        return button

    def _draw_cube(self, canvas, x, y, size):
        mid = size / 2
        top = (x + mid, y, x + size, y + mid / 2, x + mid, y + mid, x, y + mid / 2)
        left = (x, y + mid / 2, x + mid, y + mid, x + mid, y + size, x, y + size - mid / 2)
        right = (
            x + mid, y + mid, x + size, y + mid / 2,
            x + size, y + size - mid / 2, x + mid, y + size,
        )
        canvas.create_polygon(top, fill=COLORS["accent_light"], outline="")
        canvas.create_polygon(left, fill=COLORS["accent_hover"], outline="")
        canvas.create_polygon(right, fill=COLORS["accent"], outline="")

    def _draw_background(self, event):
        canvas = event.widget
        width, height = event.width, event.height
        if width < 1 or height < 1:
            return
        fitted_image = ImageOps.fit(
            self._background_image,
            (width, height),
            method=Image.Resampling.LANCZOS,
        )
        self._background_photo = ImageTk.PhotoImage(fitted_image, master=self.root)
        canvas.delete("background")
        canvas.create_image(
            0, 0, image=self._background_photo, anchor="nw", tags="background",
        )
        canvas.tag_lower("background")
        canvas.tag_raise("copy")

    def _set_status(self, text, color=COLORS["success"]):
        self.state = text
        self.status_label.configure(text=text)
        self.status_dot.delete("all")
        self.status_dot.create_oval(2, 2, 10, 10, fill=color, outline="")

    def _sync_play_button(self):
        if self.game_running:
            self.play_button.configure(
                state="disabled", text="SPIEL LÄUFT",
                bg=COLORS["disabled"], activebackground=COLORS["disabled"],
                disabledforeground=COLORS["text"], cursor="arrow",
            )
        elif self.launching or self.process is not None:
            self.play_button.configure(
                state="disabled", text="WIRD GESTARTET ...",
                bg=COLORS["disabled"], activebackground=COLORS["disabled"],
                disabledforeground=COLORS["text"], cursor="arrow",
            )
        elif self.update_checking or self.pending_update is not None:
            self.play_button.configure(
                state="disabled", text="UPDATE WIRD GEPRÜFT ...",
                bg=COLORS["disabled"], activebackground=COLORS["disabled"],
                disabledforeground=COLORS["text"], cursor="arrow",
            )
        elif self.game_status_error or not self.fabric_versions:
            self.play_button.configure(
                state="disabled", text="▶   SPIELEN",
                bg=COLORS["disabled"], activebackground=COLORS["disabled"],
                disabledforeground=COLORS["text"], cursor="arrow",
            )
        else:
            self.play_button.configure(
                state="normal", text="▶   SPIELEN",
                bg=COLORS["accent"], activebackground=COLORS["accent_hover"],
                disabledforeground=COLORS["text"], cursor="hand2",
            )

    def _read_backend_status(self):
        try:
            status = self.status_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return
        if status and status != self.last_backend_status:
            self.last_backend_status = status
            self._set_status(status, COLORS["warning"])

    def _refresh_game_state(self):
        if os.name != "nt":
            return
        try:
            running = _minecraft_is_running()
        except OSError:
            if not self.game_status_error:
                self.game_status_error = True
                self._sync_play_button()
                self._set_status("Spielstatus konnte nicht geprüft werden.", COLORS["danger"])
                messagebox.showerror(
                    "Status konnte nicht geprüft werden",
                    "Der Minecraft-Status konnte nicht ermittelt werden. Der Start bleibt "
                    "zur Sicherheit deaktiviert.",
                    parent=self.root,
                )
            return

        was_running = self.game_running
        was_error = self.game_status_error
        self.game_status_error = False
        self.game_running = running
        if running != was_running or was_error:
            self._sync_play_button()
            if running:
                self._set_status("Minecraft läuft")
            elif self.process is None and not self.launching:
                self._set_status(
                    "Bereit zum Spielen" if self.fabric_versions
                    else "Fabric installieren, um zu spielen"
                )

    def _close(self):
        try:
            self.status_file.unlink()
        except FileNotFoundError:
            pass
        self.root.destroy()

    def _start_update_check(self):
        threading.Thread(target=self._check_for_update, daemon=True).start()

    def _check_for_update(self):
        try:
            result = updater.prepare_update(
                self.current_version,
                Path(sys.executable),
                lambda text: self.events.put(("update_status", text)),
            )
        except updater.UpdateError as error:
            self.events.put(("update_error", str(error)))
        except Exception:
            self.events.put((
                "update_error",
                "Beim Prüfen des Launcher-Updates ist ein unerwarteter Fehler aufgetreten.",
            ))
        else:
            if result is None:
                self.events.put(("update_current", None))
            else:
                self.events.put(("update_ready", result))

    def _apply_pending_update(self):
        if (
            self.pending_update is None or self.update_replacement_scheduled
            or self.game_running or self.game_status_error or self.process is not None
            or self.launching
        ):
            return

        version, staged_path = self.pending_update
        try:
            updater.schedule_replacement(staged_path, Path(sys.executable))
        except updater.UpdateError as error:
            self.pending_update = None
            try:
                staged_path.unlink()
            except FileNotFoundError:
                pass
            self._sync_play_button()
            self._set_status(str(error), COLORS["danger"])
            messagebox.showerror("Launcher-Update", str(error), parent=self.root)
            return

        self.update_replacement_scheduled = True
        self._set_status(
            f"Launcher-Version {version} wird installiert. Der Launcher startet neu ...",
            COLORS["warning"],
        )
        self._close()

    def _launch(self):
        if (
            self.process is not None or self.launching or self.game_running
            or self.game_status_error or self.update_checking
            or self.pending_update is not None
        ):
            return
        try:
            selected_version = self.fabric_version.get()
            if selected_version not in self.fabric_versions:
                raise ValueError("Installiere zuerst ein Fabric-Profil.")
            command = launch_command(selected_version)
        except (FileNotFoundError, OSError, ValueError) as error:
            message = str(error) if isinstance(error, (FileNotFoundError, ValueError)) else \
                "Der Launcherprozess konnte nicht gestartet werden."
            messagebox.showerror("Startfehler", message, parent=self.root)
            return

        self.launching = True
        self.last_backend_status = ""
        self.status_file.write_text("", encoding="utf-8")
        self._sync_play_button()
        self.fabric_picker.configure(state="disabled")
        self._set_status("Launcher wird vorbereitet ...", COLORS["warning"])
        threading.Thread(target=self._run_launcher, args=(command,), daemon=True).start()

    def _refresh_fabric_versions(self):
        selected = self.fabric_version.get()
        try:
            self.fabric_versions = get_installed_fabric_versions()
        except Exception:
            messagebox.showerror(
                "Fehler beim Aktualisieren",
                "Die installierten Fabric-Versionen konnten nicht geladen werden.",
                parent=self.root,
            )
            return
        self.fabric_picker.configure(
            values=self.fabric_versions or ("Keine Fabric-Version gefunden",),
        )
        if self.fabric_versions:
            self.fabric_picker.configure(state="readonly")
            self.fabric_version.set(
                selected if selected in self.fabric_versions else self.fabric_versions[0]
            )
            self._sync_play_button()
            if not self.game_running and not self.launching and self.process is None:
                self._set_status("Fabric-Versionen aktualisiert")
        else:
            self.fabric_version.set("Keine Fabric-Version gefunden")
            self.fabric_picker.configure(state="disabled")
            self._sync_play_button()
            if not self.game_running and not self.launching and self.process is None:
                self._set_status("Fabric installieren, um zu spielen", COLORS["warning"])

    def _run_launcher(self, command):
        try:
            process = subprocess.Popen(
                command,
                cwd=str(_backend_working_directory()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=_backend_process_environment(self.status_file),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.events.put(("started", process))
            self.events.put(("exited", process.wait()))
        except OSError:
            self.events.put(("error", "Der Launcherprozess konnte nicht gestartet werden."))

    def _poll(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "started":
                self.process = value
                self.launching = False
                self._sync_play_button()
                self._set_status("Minecraft wird vorbereitet ...", COLORS["warning"])
            elif kind == "exited":
                self.process = None
                self.launching = False
                self._sync_play_button()
                self.fabric_picker.configure(
                    state="readonly" if self.fabric_versions else "disabled",
                )
                if value == 0:
                    if not self.game_running:
                        self._set_status("Bereit zum Spielen")
                else:
                    self._set_status("Beim Start ist ein Fehler aufgetreten.", COLORS["danger"])
                    messagebox.showerror(
                        "Startfehler",
                        "Minecraft konnte nicht gestartet werden. Bitte versuche es erneut.",
                        parent=self.root,
                    )
            elif kind == "error":
                self.process = None
                self.launching = False
                self._sync_play_button()
                self.fabric_picker.configure(
                    state="readonly" if self.fabric_versions else "disabled",
                )
                self._set_status("Launcher konnte nicht gestartet werden.", COLORS["danger"])
                messagebox.showerror("Startfehler", value, parent=self.root)
            elif kind == "update_status":
                self._set_status(value, COLORS["warning"])
            elif kind == "update_current":
                self.update_checking = False
                self._sync_play_button()
                if not self.game_running:
                    self._set_status("Der Launcher ist auf dem neuesten Stand.")
            elif kind == "update_error":
                self.update_checking = False
                self._sync_play_button()
                self._set_status(value, COLORS["warning"])
            elif kind == "update_ready":
                self.update_checking = False
                self.pending_update = value
                self._sync_play_button()
                if self.game_running:
                    self._set_status(
                        "Update bereit. Es wird nach dem Schließen von Minecraft installiert.",
                        COLORS["warning"],
                    )
                else:
                    self._set_status("Update wird vorbereitet ...", COLORS["warning"])

        self._read_backend_status()
        self._refresh_game_state()
        self._apply_pending_update()
        self.root.after(350, self._poll)

def run(current_version):
    app_root = tk.Tk()
    StodoLauncher(app_root, current_version)
    app_root.mainloop()
