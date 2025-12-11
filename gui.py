#!/usr/bin/env python3
import os
import sys
import time
import threading
import subprocess
import queue
import re
import json
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COOM_DL_PATH = os.path.join(SCRIPT_DIR, "main.py")
REPO_URL = "https://github.com/s0ca/C00merFucker"  


# STYLE MODERNE (TTK + SV_TTK)
def setup_style(root: tk.Tk):
    
    style = ttk.Style(root)
    preferred_themes = ["clam", "alt", "default"]
    for theme in preferred_themes:
        if theme in style.theme_names():
            style.theme_use(theme)
            break

    # sv-ttk (optionnel)
    has_svttk = False
    try:
        import sv_ttk
        sv_ttk.set_theme("dark")
        has_svttk = True
    except Exception:
        pass

    base_font = ("Segoe UI", 10) if sys.platform.startswith("win") else ("Sans", 10)
    mono_font = ("Consolas", 9) if sys.platform.startswith("win") else ("Monospace", 9)

    root.option_add("*Font", base_font)

    style.configure("TLabel", padding=2)
    style.configure("TButton", padding=(8, 4))
    style.configure("TCheckbutton", padding=2)
    style.configure("TRadiobutton", padding=2)
    style.configure("TLabelframe.Label", font=(base_font[0], base_font[1], "bold"))

    return mono_font, style, has_svttk

# Clic
def add_context_menu(widget: tk.Widget):
    """Ajoute un menu clic droit basique (Copier/Couper/Coller/Select all) à un widget texte."""
    menu = tk.Menu(widget, tearoff=0)
    menu.add_command(label="Couper", command=lambda: widget.event_generate("<<Cut>>"))
    menu.add_command(label="Copier", command=lambda: widget.event_generate("<<Copy>>"))
    menu.add_command(label="Coller", command=lambda: widget.event_generate("<<Paste>>"))
    menu.add_separator()
    menu.add_command(label="Tout sélectionner", command=lambda: widget.event_generate("<<SelectAll>>"))

    def show_menu(event):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # Clic droit Windows / Linux
    widget.bind("<Button-3>", show_menu)
    # Clic droit "Ctrl+click" macOS
    widget.bind("<Control-Button-1>", show_menu)


# GUI
class CoomGUI:
    ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CoomerFucker")

        mono_font, style, has_svttk = setup_style(root)
        self.style = style
        self.has_svttk = has_svttk

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.process: subprocess.Popen | None = None
        self.reader_thread: threading.Thread | None = None
        self.running = False
        self.last_download_dir: str | None = None
        self.downloaded_count = 0 
        self.paused = False
        self.total_tasks = 0
        self.dl_start_time = None      

        
        self.konami_seq = ["Up", "Up", "Down", "Down", "Left", "Right", "Left", "Right", "b", "a"]
        self.konami_buffer: list[str] = []
        self.konami_triggered = False

        self.build_ui(mono_font)
        self.poll_log_queue()

    # UI LAYOUT
    def build_ui(self, mono_font):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main = ttk.Frame(self.root, padding=12)
        main.grid(row=0, column=0, sticky="nsew")

        for col in range(3):
            main.columnconfigure(col, weight=1)

        row = 0

        # Service
        ttk.Label(main, text="Service :").grid(row=row, column=0, sticky="w")
        self.service_var = tk.StringVar(value="onlyfans")
        svc_frame = ttk.Frame(main)
        svc_frame.grid(row=row, column=1, columnspan=2, sticky="w")

        ttk.Radiobutton(svc_frame, text="OnlyFans", variable=self.service_var,
                        value="onlyfans").pack(side="left", padx=(0, 8))
        ttk.Radiobutton(svc_frame, text="Fansly", variable=self.service_var,
                        value="fansly").pack(side="left")

        # User / ID
        row += 1
        ttk.Label(main, text="User / ID :").grid(row=row, column=0, sticky="w", pady=(6, 0))
        self.user_var = tk.StringVar()
        self.user_entry = ttk.Entry(main, textvariable=self.user_var)
        self.user_entry.grid(row=row, column=1, columnspan=2, sticky="ew")
        add_context_menu(self.user_entry)


        # Options
        row += 1
        opt = ttk.Labelframe(main, text="Options", padding=8)
        opt.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        for c in range(3):
            opt.columnconfigure(c, weight=1)

        self.download_var = tk.BooleanVar(value=True)
        self.only_failed_var = tk.BooleanVar(value=False)
        self.retry_forever_var = tk.BooleanVar(value=False)

        ttk.Checkbutton(opt, text="Download", variable=self.download_var).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(opt, text="Only failed", variable=self.only_failed_var).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(opt, text="Retry forever", variable=self.retry_forever_var).grid(row=0, column=2, sticky="w")

        # Paramètres
        row += 1
        params = ttk.Labelframe(main, text="Paramètres", padding=8)
        params.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 0))

        for c in range(4):
            params.columnconfigure(c, weight=1)

        ttk.Label(params, text="Max concurrent:").grid(row=0, column=0, sticky="w")
        self.max_conc_var = tk.IntVar(value=4)
        ttk.Spinbox(params, from_=1, to=32, width=5, textvariable=self.max_conc_var).grid(row=0, column=1, sticky="w")

        ttk.Label(params, text="Max retries:").grid(row=0, column=2, sticky="w")
        self.max_retries_var = tk.IntVar(value=3)
        ttk.Spinbox(params, from_=1, to=50, width=5, textvariable=self.max_retries_var).grid(row=0, column=3, sticky="w")

        # Tri
        row += 1
        sortf = ttk.Labelframe(main, text="Tri", padding=8)
        sortf.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 0))

        ttk.Label(sortf, text="Sort by:").grid(row=0, column=0, sticky="w")
        self.sort_var = tk.StringVar(value="published")

        ttk.Combobox(
            sortf, textvariable=self.sort_var,
            values=["published", "id", "title"], state="readonly", width=14
        ).grid(row=0, column=1, sticky="w")

        self.reverse_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(sortf, text="Reverse", variable=self.reverse_var).grid(row=0, column=2, sticky="e")

        # Boutons
        row += 1
        btnf = ttk.Frame(main)
        btnf.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 0))

        for c in range(5):
            btnf.columnconfigure(c, weight=1)

        self.run_btn = ttk.Button(btnf, text="▶ Run", command=self.on_run_clicked)
        self.run_btn.grid(row=0, column=0, sticky="ew", padx=(0, 5))

        self.pause_btn = ttk.Button(btnf, text="⏸ Pause", command=self.on_pause_clicked,state="disabled")
        self.pause_btn.grid(row=0, column=1, sticky="ew", padx=5)

        self.stop_btn = ttk.Button(btnf, text="■ Stop", command=self.on_stop_clicked, state="disabled")
        self.stop_btn.grid(row=0, column=2, sticky="ew", padx=5)

        self.open_btn = ttk.Button(btnf, text="📂 Open folder", state="disabled", command=self.on_open_folder_clicked)
        self.open_btn.grid(row=0, column=3, sticky="ew", padx=(5, 0))
        
        self.preview_btn = ttk.Button(btnf, text="👁 Preview", command=self.on_preview_clicked)
        self.preview_btn.grid(row=0, column=4, sticky="ew", padx=(5, 0))
        btnf.columnconfigure(3, weight=1)

        # Barre d’activité
        self.activity_bar = ttk.Progressbar(btnf, mode="indeterminate")
        self.activity_bar.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0))

        # Logbox
        row += 1
        logf = ttk.Labelframe(main, text="Logs", padding=4)
        logf.grid(row=row, column=0, columnspan=3, sticky="nsew", pady=(10, 0))

        logf.rowconfigure(0, weight=1)
        logf.columnconfigure(0, weight=1)

        bg = "#111111" if sys.platform != "win32" else None
        fg = "#EEEEEE" if sys.platform != "win32" else None

        self.log_text = tk.Text(
            logf, wrap="word", height=20, font=mono_font,
            bg=bg, fg=fg
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        add_context_menu(self.log_text)


        scroll = ttk.Scrollbar(logf, command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text["yscrollcommand"] = scroll.set
        self.log_text.tag_configure("cmd", foreground="#B0BEC5")
        self.log_text.tag_configure("info", foreground="#8BC34A")
        self.log_text.tag_configure("err", foreground="#FF5370")
        self.log_text.tag_configure("warn", foreground="#FFB74D")
        self.log_text.tag_configure("progress", foreground="#64B5F6")
        self.log_text.tag_configure("okdl", foreground="#00E676")  # vert flashy
        self.log_text.config(state="disabled")
        main.rowconfigure(row, weight=1)

        # Barre de statut
        status = ttk.Frame(self.root, padding=(4, 2))
        status.grid(row=1, column=0, sticky="ew")

        status.columnconfigure(0, weight=1)
        status.columnconfigure(1, weight=0)
        status.columnconfigure(2, weight=0)
        status.columnconfigure(3, weight=0)
        status.columnconfigure(4, weight=0)

        self.status_label = ttk.Label(status, text="Ready", anchor="w")
        self.status_label.grid(row=0, column=0, sticky="w")

        self.dl_label = ttk.Label(status, text="DL: 0/?", anchor="e")
        self.dl_label.grid(row=0, column=1, sticky="e", padx=(10, 10))

        # Sélecteur de thème
        if self.has_svttk:
            theme_values = ["Dark (sv)", "Light (sv)", "TTK:clam", "TTK:alt", "TTK:default"]
        else:
            theme_values = ["TTK:" + t for t in self.style.theme_names()]

        ttk.Label(status, text="Theme:").grid(row=0, column=2, sticky="e")
        self.theme_var = tk.StringVar(value=theme_values[0])
        theme_box = ttk.Combobox(status, textvariable=self.theme_var, values=theme_values,
                                 state="readonly", width=14)
        theme_box.grid(row=0, column=3, sticky="e")
        theme_box.bind("<<ComboboxSelected>>", self.on_theme_changed)

        # Bouton Dev info
        dev_btn = ttk.Button(status, text="Dev info", command=self.on_dev_info_clicked)
        dev_btn.grid(row=0, column=4, sticky="e", padx=(8, 0))

        # Bind global pour le Konami code
        self.root.bind_all("<Key>", self.on_key_pressed)

    # LOGGING / DL OK
    def append_log(self, text: str):
        clean = self.ANSI_RE.sub("", text)

        # Détection du nombre total de téléchargements
        if "Téléchargements nécessaires :" in clean:
            try:
                m = re.search(r"(\d+)", clean)
                if m:
                    self.total_tasks = int(m.group(1))
                    self.dl_label.config(
                        text=f"DL: {self.downloaded_count}/{self.total_tasks}"
                    )
            except Exception:
                pass

        # Détection dossier
        if "Dossier :" in clean:
            try:
                _, part = clean.split("Dossier :", 1)
                path = part.strip()
                if not os.path.isabs(path):
                    path = os.path.join(SCRIPT_DIR, path)
                self.last_download_dir = path
                if os.path.isdir(path):
                    self.open_btn.config(state="normal")
            except Exception:
                pass

        # Détection fichier OK
        filename_ok = None
        if "[PROGRESS]" in clean and "OK:" in clean:
            try:
                filename_ok = clean.split("OK:", 1)[1].strip()
            except Exception:
                pass

        # Tags
        if clean.startswith("$ "):
            tag = "cmd"
        elif "[ERR]" in clean:
            tag = "err"
        elif "[WARN]" in clean:
            tag = "warn"
        elif "[INFO]" in clean:
            tag = "info"
        elif "[PROGRESS]" in clean or "[DL]" in clean:
            tag = "progress"
        else:
            tag = None

        # Affichage
        self.log_text.config(state="normal")

        if tag:
            self.log_text.insert("end", clean, tag)
        else:
            self.log_text.insert("end", clean)

        # Ligne spéciale pour les DL OK
        if filename_ok:
            self.downloaded_count += 1

            # Premier DL : point de départ du chrono
            if self.dl_start_time is None:
                self.dl_start_time = time.time()

            # Mise à jour du label DL x/y
            if self.total_tasks > 0:
                self.dl_label.config(
                    text=f"DL: {self.downloaded_count}/{self.total_tasks}"
                )
            else:
                self.dl_label.config(text=f"DL: {self.downloaded_count}")

            # ETA / speed approximatif
            if self.running and not self.paused and self.dl_start_time is not None:
                elapsed = max(time.time() - self.dl_start_time, 0.001)
                speed = self.downloaded_count / elapsed  # vidéos / sec
                remaining = max(self.total_tasks - self.downloaded_count, 0)
                if speed > 0 and self.total_tasks > 0:
                    eta_sec = remaining / speed
                    m = int(eta_sec // 60)
                    s = int(eta_sec % 60)
                    eta_txt = f"{m}m{s:02d}s"
                    self.status_label.config(
                        text=f"En cours… [{speed:.2f} DL/s, ETA ~ {eta_txt}]"
                    )
                else:
                    self.status_label.config(text="En cours…")

            self.log_text.insert("end", f"✅ DL: {filename_ok}\n", "okdl")

        self.log_text.see("end")
        self.log_text.config(state="disabled")

    # Process reader + poll log queue
    def reader_target(self, proc: subprocess.Popen):
        try:
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                self.log_queue.put(line)
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
            self.running = False
            self.log_queue.put("\n[INFO] Process terminé.\n")

    def poll_log_queue(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.append_log(line)
        except queue.Empty:
            pass

        if not self.running and self.process:
            self.process = None
            self.run_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.status_label.config(text="Ready")
            self.activity_bar.stop()
            self.activity_bar["value"] = 0
            self.root.title("CoomerFucker")

        self.root.after(100, self.poll_log_queue)

    #Boutons
    def on_run_clicked(self, only_posts=None):
        if self.running:
            return

        pause_flag = Path("pause.flag")
        if pause_flag.exists():
            try:
                pause_flag.unlink()
            except Exception:
                pass

        svc = self.service_var.get()
        user = self.user_var.get().strip()

        if not svc:
            messagebox.showerror("Erreur", "Service manquant")
            return
        if not user:
            messagebox.showerror("Erreur", "User manquant")
            return

        cmd = [sys.executable, "-u", COOM_DL_PATH, "--service", svc, "--user", user]

        if self.download_var.get():
            cmd.append("--download")
        if self.only_failed_var.get():
            cmd.append("--only-failed")
        if self.retry_forever_var.get():
            cmd.append("--retry-forever")

        cmd += ["--max-concurrent", str(self.max_conc_var.get())]
        cmd += ["--max-retries", str(self.max_retries_var.get())]
        cmd += ["--sort", self.sort_var.get()]
        if self.reverse_var.get():
            cmd.append("--reverse")
        # Optionnel: limiter aux posts sélectionnés depuis la preview
        if only_posts:
            # only_posts peut être une liste / set / tuple ou une string déjà join
            if isinstance(only_posts, (list, tuple, set)):
                posts_str = ",".join(str(p) for p in only_posts)
            else:
                posts_str = str(only_posts)
            cmd += ["--only-posts", posts_str]

        # Reset stats DL
        self.downloaded_count = 0
        self.total_tasks = 0
        self.dl_start_time = None
        self.dl_label.config(text="DL: 0/?")

        # Reset logs
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

        self.last_download_dir = None
        self.open_btn.config(state="disabled")
        self.append_log(f"$ {' '.join(cmd)}\n\n")

        try:
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible de lancer le script :\n{e}")
            return

        self.running = True
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.pause_btn.config(state="normal")
        self.status_label.config(text="En cours…")
        self.root.title("CoomerFucker — RUNNING")
        self.activity_bar.start(80)

        self.reader_thread = threading.Thread(
            target=self.reader_target, args=(self.process,), daemon=True
        )
        self.reader_thread.start()

    def on_stop_clicked(self):
        if self.process and self.running:
            try:
                self.process.terminate()
            except Exception:
                pass
            self.append_log("[INFO] Arrêt demandé.\n")
            self.running = False
            self.stop_btn.config(state="disabled")
            self.pause_btn.config(state="disabled")
            self.paused = False
            # supprimer le flag si présent
            try:
                from pathlib import Path
                pause_flag = Path("pause.flag")
                if pause_flag.exists():
                    pause_flag.unlink()
            except Exception:
                pass
            self.status_label.config(text="Arrêt demandé…")
            self.activity_bar.stop()

    def on_pause_clicked(self):
        """Toggle Pause / Resume en créant/supprimant pause.flag."""
        from pathlib import Path
        pause_flag = Path("pause.flag")

        if not self.running:
            return

        if not self.paused:
            # Activer la pause
            self.paused = True
            try:
                pause_flag.write_text("pause")
            except Exception as e:
                messagebox.showerror("Erreur", f"Impossible d'activer la pause:\n{e}")
                self.paused = False
                return

            # UI : bouton + statut + barre
            self.pause_btn.config(text="▶ Resume")
            self.status_label.config(text="En pause…")
            self.activity_bar.stop()
            self.append_log("[INFO] Pause activée.\n")

        else:
            # Désactiver la pause
            self.paused = False
            try:
                if pause_flag.exists():
                    pause_flag.unlink()
            except Exception as e:
                messagebox.showerror("Erreur", f"Impossible de désactiver la pause:\n{e}")
                return

            # UI : bouton + statut + barre
            self.pause_btn.config(text="⏸ Pause")
            self.status_label.config(text="En cours…")
            self.activity_bar.start(80)
            self.append_log("[INFO] Reprise.\n")

    def on_open_folder_clicked(self):
        if not self.last_download_dir:
            messagebox.showinfo("Info", "Aucun dossier détecté.")
            return

        p = self.last_download_dir
        if not os.path.isdir(p):
            messagebox.showerror("Erreur", f"Dossier introuvable:\n{p}")
            return

        try:
            if sys.platform.startswith("win"):
                os.startfile(p)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception as e:
            messagebox.showerror("Erreur", str(e))

    def on_preview_clicked(self):
        svc = self.service_var.get()
        user = self.user_var.get().strip()

        if not svc or not user:
            messagebox.showerror("Erreur", "Service ou user manquant.")
            return

        cmd = [sys.executable, COOM_DL_PATH, "--service", svc, "--user", user, "--preview"]

        try:
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            messagebox.showerror(
                "Erreur",
                f"Commande preview échouée (code {e.returncode})\n\n{e.output}"
            )
            return
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible d'exécuter la preview:\n{e}")
            return

        # On cherche la zone JSON entre les marqueurs
        lines = out.splitlines()
        in_json = False
        buf = []
        for line in lines:
            stripped = line.strip()
            if stripped == "__PREVIEW_JSON_START__":
                in_json = True
                continue
            if stripped == "__PREVIEW_JSON_END__":
                break
            if in_json:
                buf.append(line)

        json_str = "\n".join(buf).strip()
        if not json_str:
            messagebox.showerror(
                "Erreur",
                "Impossible de récupérer la preview:\nAucun bloc JSON trouvé.\n\nSortie brute:\n" + out[:500]
            )
            return

        try:
            data = json.loads(json_str)
        except Exception as e:
            messagebox.showerror(
                "Erreur",
                f"Impossible de parser le JSON de preview:\n{e}\n\nJSON brut:\n{json_str[:500]}"
            )
            return

        self.show_preview_window(data)

    def show_preview_window(self, posts):
        """Affiche une fenêtre avec la liste des posts renvoyés par --preview."""
        win = tk.Toplevel(self.root)
        win.title("Preview des posts")
        win.geometry("800x500")

        frame = ttk.Frame(win, padding=8)
        frame.pack(fill="both", expand=True)

        cols = ("date", "id", "title", "files")
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        tree.pack(fill="both", expand=True)

        tree.heading("date", text="Date")
        tree.heading("id", text="Post ID")
        tree.heading("title", text="Titre")
        tree.heading("files", text="Fichiers vidéo")

        tree.column("date", width=100, anchor="w")
        tree.column("id", width=120, anchor="w")
        tree.column("title", width=420, anchor="w")
        tree.column("files", width=80, anchor="center")

        # Insertion des lignes, on utilise l'index comme iid
        for idx, p in enumerate(posts):
            date = (p.get("published") or "")[:10]
            post_id = str(p.get("post_id") or p.get("id") or "")
            title = (p.get("title") or "").strip()
            title_short = (title[:70] + "…") if len(title) > 70 else title
            files_count = p.get("files_count", 1)  # adapte selon ta structure réelle

            tree.insert("", "end", iid=str(idx),
                        values=(date, post_id, title_short, files_count))

        # Boutons sous le Treeview
        btnf = ttk.Frame(frame)
        btnf.pack(fill="x", pady=(8, 0))

        dl_btn = ttk.Button(
            btnf,
            text="Download selected",
            command=lambda: self.download_selected_from_preview(posts, tree, win)
        )
        dl_btn.pack(side="left")

        close_btn = ttk.Button(btnf, text="Close", command=win.destroy)
        close_btn.pack(side="right")
        
        tree.bind(
            "<Double-1>",
            lambda event, t=tree, pl=posts: self.on_preview_item_double_click(event, t, pl)
        )

    def download_selected_from_preview(self, posts, tree, win):
        """Récupère les posts sélectionnés dans la preview et lance un run limité à ceux-là."""
        if self.running:
            messagebox.showwarning(
                "Déjà en cours",
                "Un téléchargement est déjà en cours.\nVeuillez l'arrêter avant d'en lancer un autre."
            )
            return

        selection = tree.selection()
        if not selection:
            messagebox.showinfo("Info", "Aucun post sélectionné.")
            return

        post_ids = []
        for iid in selection:
            try:
                idx = int(iid)
            except ValueError:
                continue
            if 0 <= idx < len(posts):
                p = posts[idx]
                pid = str(p.get("post_id") or p.get("id") or "").strip()
                if pid:
                    post_ids.append(pid)

        post_ids = sorted(set(post_ids))
        if not post_ids:
            messagebox.showerror("Erreur", "Impossible de déterminer les post_id sélectionnés.")
            return

        # Fermer la fenêtre de preview et lancer un run limité
        win.destroy()
        self.on_run_clicked(only_posts=post_ids)

    def on_preview_item_double_click(self, event, tree, posts):
        """Affiche une mini-fiche détaillée pour le post double-cliqué (sans JSON brut)."""
        iid = tree.focus()
        if not iid:
            return

        try:
            idx = int(iid)
        except ValueError:
            return

        if not (0 <= idx < len(posts)):
            return

        p = posts[idx]

        svc = self.service_var.get()
        user = self.user_var.get().strip()

        title = (p.get("title") or "").strip()
        date = p.get("published") or ""
        post_id = str(p.get("post_id") or p.get("id") or "")
        files_count = p.get("files_count", 1)

        info_lines = [
            f"Service : {svc}",
            f"User    : {user}",
            f"Post ID : {post_id}",
            f"Date    : {date}",
            f"Fichiers vidéo (approx.) : {files_count}",
        ]

        if title:
            info_lines.append(f"Titre   : {title}")

        messagebox.showinfo("Détails du post", "\n".join(info_lines))
    
    # Dev popup
    def on_dev_info_clicked(self):
        text = (
            "CoomerFucker\n"
            "-----------------\n"
            "Dev : s0ca\n"
            "Release approx. : 2025-11\n"
            "\n"
            "Repo :\n"
            f"{REPO_URL}\n"
            "\n"
            "Stack principale :\n"
            "- Python 3\n"
            "- tkinter / ttk\n"
            "- sv_ttk (thème, optionnel)\n"
            "- coom_dl.py (async, aiohttp, requests)\n"
            "\n"
            "Wrapper graphique pour coom_dl.py\n"
            "Downloads OnlyFans / Fansly via Coomer\n"
            "avec reprise, retries, tri et mode only-failed."
        )
        messagebox.showinfo("Dev info", text)

    # Theme changer
    def on_theme_changed(self, event=None):
        val = self.theme_var.get()

        if self.has_svttk:
            try:
                import sv_ttk
                if val.startswith("Dark"):
                    sv_ttk.set_theme("dark")
                    return
                if val.startswith("Light"):
                    sv_ttk.set_theme("light")
                    return
            except Exception:
                pass

        if val.startswith("TTK:"):
            theme = val.split(":", 1)[1]
            if theme in self.style.theme_names():
                self.style.theme_use(theme)

    # Konami code handling
    def on_key_pressed(self, event: tk.Event):
        """Capture toutes les touches pour détecter le Konami code."""
        key = None

        if event.keysym in ("Up", "Down", "Left", "Right"):
            key = event.keysym
        else:
            ch = (event.char or "").lower()
            if ch in ("a", "b"):
                key = ch

        if not key:
            return

        self.konami_buffer.append(key)
        if len(self.konami_buffer) > len(self.konami_seq):
            self.konami_buffer = self.konami_buffer[-len(self.konami_seq):]

        if self.konami_buffer == self.konami_seq:
            self.trigger_konami()

    def trigger_konami(self):
        if self.konami_triggered:
            return
        self.konami_triggered = True

        self.append_log("[INFO] Konami code detected. Coombo breaker activated!\n")

        try:
            self.log_text.config(bg="#12001f")
            self.log_text.tag_configure("okdl", foreground="#FFEA00")
            self.log_text.tag_configure("progress", foreground="#40C4FF")
        except Exception:
            pass

        win = tk.Toplevel(self.root)
        win.title("Coombo Breaker!")
        win.transient(self.root)
        win.resizable(False, False)

        msg = (
            "↑ ↑ ↓ ↓ ← → ← → B A\n\n"
            "Coombo Breaker unlocked!\n"
            "Aucun bonus caché, juste du style."
        )
        ttk.Label(win, text=msg, padding=12, justify="center").grid(row=0, column=0)
        ttk.Button(win, text="Nice 😉", command=win.destroy).grid(row=1, column=0, pady=(0, 8))

        # "Centrer" la popup
        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() // 2) - (win.winfo_width() // 2)
        y = self.root.winfo_rooty() + (self.root.winfo_height() // 2) - (win.winfo_height() // 2)
        win.geometry(f"+{x}+{y}")


def main():
    r = tk.Tk()
    app = CoomGUI(r)
    r.minsize(780, 540)
    r.mainloop()


if __name__ == "__main__":
    main()
