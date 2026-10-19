"""Modal dialog for the AWS access portal (SSO start URL), default region,
and the "start at login" toggle.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .. import config_store, platform_startup


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Settings")
        self.geometry("480x260")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        settings = config_store.load_settings()

        pad = {"padx": 10, "pady": 6}
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="AWS access portal start URL").grid(row=0, column=0, sticky="w", **pad)
        self.start_url_var = tk.StringVar(value=settings.sso_start_url)
        ttk.Entry(frame, textvariable=self.start_url_var, width=48).grid(row=1, column=0, columnspan=2, sticky="we", **pad)

        ttk.Label(frame, text="SSO region").grid(row=2, column=0, sticky="w", **pad)
        self.sso_region_var = tk.StringVar(value=settings.sso_region)
        ttk.Entry(frame, textvariable=self.sso_region_var, width=20).grid(row=3, column=0, sticky="w", **pad)

        ttk.Label(frame, text="Default profile region").grid(row=2, column=1, sticky="w", **pad)
        self.default_region_var = tk.StringVar(value=settings.default_region)
        ttk.Entry(frame, textvariable=self.default_region_var, width=20).grid(row=3, column=1, sticky="w", **pad)

        # Reflects the *actual* OS registration (LaunchAgent / registry Run
        # key / XDG autostart file), not just whatever's in settings.json --
        # those two can drift apart (e.g. the file was deleted by hand), so
        # this always asks platform_startup directly.
        self.start_on_login_var = tk.BooleanVar(value=platform_startup.is_enabled())
        ttk.Checkbutton(
            frame,
            text="Start automatically at login",
            variable=self.start_on_login_var,
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(14, 6))

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=10, padx=10)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="Save", command=self._on_save).pack(side="right")

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

    def _on_save(self):
        start_url = self.start_url_var.get().strip()
        sso_region = self.sso_region_var.get().strip()
        default_region = self.default_region_var.get().strip()

        if not start_url.startswith("https://"):
            messagebox.showerror("Invalid URL", "The access portal URL should start with https://")
            return
        if not sso_region:
            messagebox.showerror("Missing region", "SSO region is required.")
            return

        want_start_on_login = self.start_on_login_var.get()
        try:
            if want_start_on_login and not platform_startup.is_enabled():
                platform_startup.enable()
            elif not want_start_on_login and platform_startup.is_enabled():
                platform_startup.disable()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Couldn't update login item",
                f"Settings will still be saved, but the start-at-login registration failed: {exc}",
            )

        # Load fresh (rather than constructing a brand-new Settings from
        # just this dialog's four fields) so that any field a future
        # version adds that this dialog doesn't manage isn't silently
        # reset to its dataclass default every time someone hits Save
        # here. (This bit us for real once already, with a since-removed
        # refresh_paused field -- keeping this pattern even though there's
        # currently nothing else on Settings to lose.)
        settings = config_store.load_settings()
        settings.sso_start_url = start_url
        settings.sso_region = sso_region
        settings.default_region = default_region or sso_region
        settings.start_on_login = want_start_on_login
        config_store.save_settings(settings)
        self.destroy()
