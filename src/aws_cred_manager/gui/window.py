"""The single, long-lived application window.

There is exactly one of these per process, for the whole life of the app.
Closing it (the titlebar close button, or Cmd+W) hides it -- the process
keeps running in the background so the refresh worker keeps doing its job.
Actually quitting is a separate, explicit action (tray "Quit" on
Windows/Linux; the app menu's Quit / Cmd+Q on macOS).
"""
from __future__ import annotations

import logging
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Dict, Optional

from .. import config_store, external_tools, sync
from .add_profile_dialog import AddProfileDialog
from .profile_row import ProfileRow
from .settings_dialog import SettingsDialog

logger = logging.getLogger(__name__)

HEADINGS = ["", "Account", "Account ID", "Role", "Profile Name", "Region", "Status", "", "", ""]

# Column sizing for the single grid that both the header row and every
# data row are placed into (see _build_table). Keeping this in one place,
# applied once via grid_columnconfigure, is what guarantees the header
# actually lines up with the data beneath it -- they used to live in two
# separate Frames with independently-computed column widths, which could
# silently drift out of alignment.
COLUMN_MINSIZE = {0: 34, 1: 130, 2: 110, 3: 130, 4: 140, 5: 90, 6: 170, 7: 110, 8: 140, 9: 120}
COLUMN_WEIGHT = {1: 1, 3: 1, 4: 1, 6: 1}

# Grid rows 0 and 1 (inside _rows_frame) are permanently reserved for the
# header label row and the separator line under it. Every ProfileRow
# after that occupies two grid rows of its own (a separator, then its
# content) -- see profile_row.py.
HEADER_GRID_ROWS = 2

STATUS_LABELS = {
    "idle": "Not configured yet",
    "refreshing": "Refreshing credentials...",
    "ok": "All credentials up to date",
    "needs_login": "Sign-in required",
    "partial_error": "Some profiles failed to refresh",
    "all_paused": "All rows paused",
}

ICON_ADD = "➕"  # heavy plus sign
ICON_REMOVE = "\U0001F5D1"  # wastebasket
ICON_REFRESH = "\U0001F504"  # counterclockwise arrows
ICON_SETTINGS = "⚙"  # gear
ICON_PAUSE = "⏸"  # pause bars
ICON_RESUME = "▶"  # play triangle


class AppWindow(tk.Tk):
    def __init__(self, on_quit: Callable[[], None]):
        super().__init__()
        self._on_quit = on_quit
        self._worker = None  # set via attach_worker()
        self._rows: Dict[str, ProfileRow] = {}

        self.title("AWS Credential Manager")
        self.geometry("1420x500")
        self.minsize(1180, 360)

        self.settings = config_store.load_settings()
        self.profiles = config_store.load_profiles()

        self._build_menu()
        self._build_toolbar()
        self._build_table()
        self._build_statusbar()
        self._refresh_table()

        self.protocol("WM_DELETE_WINDOW", self.hide)

        if not self.settings.is_configured():
            self.after(300, self._open_settings)

    # -- wiring -----------------------------------------------------------

    def attach_worker(self, worker) -> None:
        self._worker = worker

    # -- window visibility --------------------------------------------------

    def show(self) -> None:
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(150, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def hide(self) -> None:
        self.withdraw()

    def show_auth_code(self, code: str) -> None:
        """Show the code of the sign-in tab that was opened most recently
        (a newer call simply replaces an older code)."""
        self.auth_code_label.configure(text=f"Code: {code}")

    def clear_auth_code(self) -> None:
        self.auth_code_label.configure(text="")

    def request_quit(self) -> None:
        self._on_quit()

    # -- layout -------------------------------------------------------------

    def _build_menu(self):
        menubar = tk.Menu(self)
        app_menu = tk.Menu(menubar, tearoff=False)
        app_menu.add_command(label="Settings...", command=self._open_settings)
        app_menu.add_separator()
        app_menu.add_command(label="Quit", command=self.request_quit, accelerator="Cmd+Q" if sys.platform == "darwin" else "Ctrl+Q")
        menubar.add_cascade(label="AWS Credential Manager", menu=app_menu)
        self.configure(menu=menubar)

        if sys.platform == "darwin":
            # Pure-Tcl/Tk hooks -- no AppKit/PyObjC involved.
            #
            # ::tk::mac::Quit handles Cmd+Q / the Quit menu item.
            #
            # ::tk::mac::ReopenApplication is the one that actually matters
            # for "GUI won't reopen": macOS does NOT start a new process
            # when you relaunch an already-running regular (Dock) app --
            # it just reactivates the existing process. That means our
            # single-instance socket/IPC path (which only fires when a
            # *second process* is launched) never even runs for a Dock- or
            # Finder-triggered relaunch. Tk surfaces that reactivation as
            # this command; without binding it, reopening a hidden window
            # silently did nothing.
            self.createcommand("::tk::mac::Quit", self.request_quit)
            self.createcommand("::tk::mac::ReopenApplication", self.show)
        else:
            self.bind_all("<Control-q>", lambda _e: self.request_quit())

    def _build_toolbar(self):
        style = ttk.Style(self)
        style.configure("Toolbar.TButton", padding=(10, 6))

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=10)
        ttk.Button(
            bar, text=f"{ICON_ADD}  Add Row...", style="Toolbar.TButton", command=self._on_add_row
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            bar, text=f"{ICON_REMOVE}  Remove Selected", style="Toolbar.TButton", command=self._on_remove_selected
        ).pack(side="left", padx=6)
        self.refresh_now_button = ttk.Button(
            bar, text=f"{ICON_REFRESH}  Refresh Now", style="Toolbar.TButton", command=self._on_refresh_now
        )
        self.refresh_now_button.pack(side="left", padx=6)
        # Bulk convenience, not a separate switch: these just set every
        # row's own `paused` flag at once (see
        # config_store.set_all_profiles_paused). A row bulk-paused this
        # way is no different from one paused individually, and can be
        # resumed individually afterward with its own row button exactly
        # the same way. Two separate buttons rather than one toggling
        # label -- each is disabled when it would be a no-op (Pause All
        # once everything's already paused, Resume All once nothing is).
        self.pause_all_button = ttk.Button(
            bar, text=f"{ICON_PAUSE}  Pause All", style="Toolbar.TButton", command=self._on_pause_all
        )
        self.pause_all_button.pack(side="left", padx=6)
        self.resume_all_button = ttk.Button(
            bar, text=f"{ICON_RESUME}  Resume All", style="Toolbar.TButton", command=self._on_resume_all
        )
        self.resume_all_button.pack(side="left", padx=6)
        ttk.Button(
            bar, text=f"{ICON_SETTINGS}  Settings...", style="Toolbar.TButton", command=self._open_settings
        ).pack(side="right")
        # Sign-in verification code, shown only while a browser sign-in is
        # pending (see show_auth_code / clear_auth_code).
        self.auth_code_label = ttk.Label(bar, text="", font=("TkDefaultFont", 28, "bold"))
        self.auth_code_label.pack(side="right", padx=(0, 24))

        self._update_global_pause_buttons()

    def _build_table(self):
        outer = ttk.Frame(self, relief="groove", borderwidth=1)
        outer.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # A Canvas + inner Frame is the standard Tkinter way to get a
        # scrollable region of arbitrary widgets (ttk.Treeview can't embed
        # real per-row buttons, which is why this isn't a Treeview).
        canvas_frame = ttk.Frame(outer)
        canvas_frame.pack(fill="both", expand=True, padx=1, pady=1)
        canvas_frame.grid_rowconfigure(0, weight=1)
        canvas_frame.grid_columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(canvas_frame, highlightthickness=0)
        v_scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self._canvas.yview)
        h_scrollbar = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self._canvas.xview)
        self._canvas.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        # The horizontal scrollbar is a safety net, not the primary UX --
        # the default window width (see __init__) is sized to fit every
        # column with no scrolling needed. It only kicks in if the user
        # shrinks the window below that.
        h_scrollbar.grid(row=1, column=0, sticky="ew")

        # Header and every data row are placed into this ONE frame/grid,
        # not separate Frames -- see the COLUMN_MINSIZE comment above for
        # why that matters for keeping columns aligned.
        self._rows_frame = ttk.Frame(self._canvas)
        self._rows_window = self._canvas.create_window((0, 0), window=self._rows_frame, anchor="nw")

        for col_index, minsize in COLUMN_MINSIZE.items():
            self._rows_frame.grid_columnconfigure(
                col_index, minsize=minsize, weight=COLUMN_WEIGHT.get(col_index, 0)
            )

        self._build_header()

        def _sync_scrollregion(_e=None):
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))

        def _sync_width(e):
            # Never shrink the inner frame below its natural (minsize-driven)
            # width -- only stretch it wider when the canvas has extra room.
            # This is what makes the horizontal scrollbar appear instead of
            # silently clipping columns when the window is narrower than
            # the table's natural width.
            natural_width = self._rows_frame.winfo_reqwidth()
            self._canvas.itemconfigure(self._rows_window, width=max(e.width, natural_width))

        self._rows_frame.bind("<Configure>", _sync_scrollregion)
        self._canvas.bind("<Configure>", _sync_width)
        self._canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self._canvas.bind_all("<Button-4>", lambda _e: self._canvas.yview_scroll(-1, "units"))
        self._canvas.bind_all("<Button-5>", lambda _e: self._canvas.yview_scroll(1, "units"))

    def _build_header(self):
        for col_index, text in enumerate(HEADINGS):
            if not text:
                continue
            ttk.Label(
                self._rows_frame, text=text, font=("TkDefaultFont", 10, "bold")
            ).grid(row=0, column=col_index, sticky="w", padx=6, pady=(6, 8))
        ttk.Separator(self._rows_frame, orient="horizontal").grid(
            row=1, column=0, columnspan=len(HEADINGS), sticky="ew"
        )

    def _on_mousewheel(self, event):
        self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _build_statusbar(self):
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status_var, anchor="w", relief="sunken").pack(fill="x", side="bottom")

    # -- table state ----------------------------------------------------

    def _refresh_table(self):
        # Rebuilt from scratch on every refresh rather than diffed in
        # place -- profile lists are small (tens of rows at most), and this
        # avoids an entire class of "row widget out of sync with profile
        # list" bugs for very little cost. The header (grid rows 0-1) is
        # untouched here -- it's built once in _build_header and every
        # ProfileRow's own destroy() only tears down its own two grid rows.
        for row in self._rows.values():
            row.destroy()
        self._rows = {}

        for i, p in enumerate(self.profiles):
            grid_row = HEADER_GRID_ROWS + i * 2
            self._rows[p.profile_name] = ProfileRow(
                self._rows_frame, grid_row, p,
                self._on_open_console, self._on_open_cyberduck, self._on_toggle_pause,
            )

        # Keeps Pause All / Resume All in sync with whatever the new row
        # set looks like -- covers add/remove, not just worker ticks and
        # the pause handlers (which already call this themselves too).
        self._update_global_pause_buttons()

    # -- called from RefreshWorker (already marshaled onto this thread) -----

    def on_worker_status(self, status: str) -> None:
        # "refreshing" is the only state in which a sign-in can be pending;
        # any other status means the refresh finished (or gave up).
        if status != "refreshing":
            self.clear_auth_code()
        self.profiles = config_store.load_profiles()
        by_name = {p.profile_name: p for p in self.profiles}
        if set(by_name) != set(self._rows):
            self._refresh_table()
        else:
            for name, row in self._rows.items():
                row.update_from(by_name[name])
        self._update_global_pause_buttons()

        if status.startswith("error:"):
            self.status_var.set(f"Error: {status[len('error:'):]}")
        else:
            self.status_var.set(STATUS_LABELS.get(status, status))

    # -- actions ------------------------------------------------------------

    def _open_settings(self):
        dlg = SettingsDialog(self)
        self.wait_window(dlg)
        self.settings = config_store.load_settings()
        if self._worker is not None:
            self._worker.refresh_now(force_login=False)

    def _on_add_row(self):
        if not self.settings.is_configured():
            messagebox.showinfo("Settings required", "Configure the AWS access portal URL first.")
            self._open_settings()
            return

        existing_names = [p.profile_name for p in self.profiles]

        def on_added(new_profile: config_store.ManagedProfile):
            self.profiles = sync.add_profile(self.settings, new_profile)
            self._refresh_table()
            self.status_var.set(f"Added {new_profile.profile_name}. Fetching credentials...")
            if self._worker is not None:
                self._worker.refresh_now(force_login=False)

        dlg = AddProfileDialog(self, self.settings, existing_names, on_added)
        self.wait_window(dlg)

    def _on_remove_selected(self):
        selected = [name for name, row in self._rows.items() if row.selected_var.get()]
        if not selected:
            return
        if not messagebox.askyesno(
            "Remove profiles",
            f"Remove {len(selected)} profile(s)? This also deletes them from ~/.aws/config and ~/.aws/credentials.",
        ):
            return
        self.profiles = sync.remove_profiles(selected)
        self._refresh_table()
        self.status_var.set("Removed selected profile(s).")

    def _on_refresh_now(self):
        if self._worker is not None:
            self._worker.refresh_now(force_login=True)
        self.status_var.set("Refreshing credentials...")

    # -- pause / resume ---------------------------------------------------

    def _update_global_pause_buttons(self):
        if not self.profiles:
            self.pause_all_button.configure(state="disabled")
            self.resume_all_button.configure(state="disabled")
            return
        all_paused = all(p.paused for p in self.profiles)
        none_paused = not any(p.paused for p in self.profiles)
        # Each button is disabled exactly when clicking it would be a
        # no-op -- Pause All once everything's already paused, Resume All
        # once nothing is. With a mix of paused/active rows, both stay
        # enabled: Pause All would finish pausing the rest, Resume All
        # would bring back the ones that are paused.
        self.pause_all_button.configure(state="disabled" if all_paused else "normal")
        self.resume_all_button.configure(state="disabled" if none_paused else "normal")

    def _on_pause_all(self):
        self.profiles = config_store.set_all_profiles_paused(True)
        self._refresh_row_display()
        self._update_global_pause_buttons()
        self.status_var.set("Paused refresh for all rows.")

    def _on_resume_all(self):
        self.profiles = config_store.set_all_profiles_paused(False)
        self._refresh_row_display()
        self._update_global_pause_buttons()
        # Nudge the worker to check right away instead of waiting for the
        # next poll interval.
        if self._worker is not None:
            self._worker.refresh_now(force_login=False)
        self.status_var.set("Resumed refresh for all rows.")

    def _on_toggle_pause(self, profile_name: str) -> None:
        # Look up the CURRENT paused state fresh from self.profiles rather
        # than trusting a profile snapshot handed in by the caller -- the
        # button that calls this only ever passes a name (see
        # profile_row.py's comment on why), and self.profiles here is
        # itself kept fresh by every prior toggle/worker-status update.
        current_by_name = {p.profile_name: p for p in self.profiles}
        current = current_by_name.get(profile_name)
        was_paused = current.paused if current is not None else False

        self.profiles = config_store.set_profile_paused(profile_name, not was_paused)
        self._refresh_row_display()
        self._update_global_pause_buttons()
        verb = "Resumed" if was_paused else "Paused"
        self.status_var.set(f"{verb} refresh for {profile_name}.")

    def _refresh_row_display(self) -> None:
        """Push current self.profiles into every existing row widget
        without rebuilding the table -- used after a pause/resume toggle
        so the status text and button labels update immediately.
        """
        by_name = {p.profile_name: p for p in self.profiles}
        for name, row in self._rows.items():
            if name in by_name:
                row.update_from(by_name[name])

    # -- per-row external tool buttons ---------------------------------

    def _on_open_console(self, profile: config_store.ManagedProfile) -> None:
        self.status_var.set(f"Opening AWS Console for {profile.profile_name}...")

        def worker():
            try:
                external_tools.open_aws_console(profile.profile_name, profile.region)
                self.after(0, lambda: self.status_var.set(f"Opened AWS Console for {profile.profile_name}."))
            except external_tools.ExternalToolError as exc:
                self.after(0, lambda: self._show_tool_error("AWS Console", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_open_cyberduck(self, profile: config_store.ManagedProfile) -> None:
        self.status_var.set(f"Opening Cyberduck for {profile.profile_name}...")
        nickname = f"{profile.account_name} - {profile.role_name} ({profile.profile_name})"

        def worker():
            try:
                external_tools.open_in_cyberduck(profile.profile_name, nickname)
                self.after(0, lambda: self.status_var.set(f"Opened Cyberduck for {profile.profile_name}."))
            except external_tools.ExternalToolError as exc:
                self.after(0, lambda: self._show_tool_error("Cyberduck", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _show_tool_error(self, tool: str, message: str) -> None:
        self.status_var.set(f"{tool}: {message}")
        messagebox.showwarning(tool, message)
