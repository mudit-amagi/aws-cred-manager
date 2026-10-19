"""Modal dialog: sign in (if needed), pick an Account then a Role fetched
live from AWS via two cascaded dropdowns, and give the resulting profile a
name.

Cascaded rather than a single flat list: with more than a handful of
accounts x roles the flat list becomes hard to scan, and picking the
account first narrows the role list to only what's actually valid for it
(you can never end up with an account/role combination that doesn't
exist).
"""
from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Dict, List, Optional

from .. import aws_sso, config_store

logger = logging.getLogger(__name__)


class AddProfileDialog(tk.Toplevel):
    def __init__(
        self,
        parent,
        settings: config_store.Settings,
        existing_profile_names: List[str],
        on_added: Callable[[config_store.ManagedProfile], None],
    ):
        super().__init__(parent)
        self.title("Add Row")
        self.geometry("480x320")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._settings = settings
        self._existing_names = set(existing_profile_names)
        self._on_added = on_added
        self._token: Optional[aws_sso.SsoToken] = None

        # account_id -> account_name, in fetch order (already sorted by
        # aws_sso.list_accounts_and_roles).
        self._accounts: Dict[str, str] = {}
        # account_id -> list of role names available in that account.
        self._roles_by_account: Dict[str, List[str]] = {}
        self._account_ids_by_label: Dict[str, str] = {}

        self._build_ui()
        self._start_login_and_fetch()

    def _build_ui(self):
        top = ttk.Frame(self, padding=14)
        top.pack(fill="both", expand=True)

        self.status_var = tk.StringVar(value="Signing in...")
        ttk.Label(top, textvariable=self.status_var, wraplength=420).pack(anchor="w", pady=(0, 8))

        self.progress = ttk.Progressbar(top, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 12))
        self.progress.start(12)

        form = ttk.Frame(top)
        form.pack(fill="x")
        form.grid_columnconfigure(1, weight=1)

        ttk.Label(form, text="Account").grid(row=0, column=0, sticky="w", pady=6)
        self.account_var = tk.StringVar()
        self.account_combo = ttk.Combobox(
            form, textvariable=self.account_var, state="disabled", values=[]
        )
        self.account_combo.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=6)
        self.account_combo.bind("<<ComboboxSelected>>", self._on_account_selected)

        ttk.Label(form, text="Role").grid(row=1, column=0, sticky="w", pady=6)
        self.role_var = tk.StringVar()
        self.role_combo = ttk.Combobox(
            form, textvariable=self.role_var, state="disabled", values=[]
        )
        self.role_combo.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=6)
        self.role_combo.bind("<<ComboboxSelected>>", self._on_role_selected)

        ttk.Label(form, text="Profile name").grid(row=2, column=0, sticky="w", pady=6)
        self.profile_name_var = tk.StringVar()
        self.name_entry = ttk.Entry(form, textvariable=self.profile_name_var)
        self.name_entry.grid(row=2, column=1, sticky="ew", padx=(10, 0), pady=6)

        btns = ttk.Frame(top)
        btns.pack(fill="x", pady=(16, 0), side="bottom")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        self.add_button = ttk.Button(btns, text="Add", command=self._on_add, state="disabled")
        self.add_button.pack(side="right")

    # -- fetch --------------------------------------------------------------

    def _start_login_and_fetch(self):
        threading.Thread(target=self._login_thread, daemon=True).start()

    def _login_thread(self):
        try:
            token = aws_sso.ensure_valid_token(
                self._settings.sso_start_url,
                self._settings.sso_region,
                self._settings.sso_session_name,
                self._on_device_auth,
            )
            roles = aws_sso.list_accounts_and_roles(token)
            self.after(0, lambda: self._on_fetched(token, roles))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Sign-in / account listing failed")
            self.after(0, lambda: self._on_fetch_error(str(exc)))

    def _on_device_auth(self, info: aws_sso.DeviceAuthInfo):
        self.after(0, lambda: self.status_var.set(
            f"Opening browser -- approve code {info.user_code} to continue"
        ))

    def _on_fetched(self, token: aws_sso.SsoToken, roles: List[aws_sso.AccountRole]):
        self._token = token
        self.progress.stop()
        self.progress.pack_forget()

        if not roles:
            self.status_var.set("No accounts/roles found for this user.")
            return

        for r in roles:
            self._accounts.setdefault(r.account_id, r.account_name)
            self._roles_by_account.setdefault(r.account_id, []).append(r.role_name)
        for account_id in self._roles_by_account:
            self._roles_by_account[account_id].sort()

        account_labels = []
        for account_id, account_name in self._accounts.items():
            label = f"{account_name} ({account_id})"
            self._account_ids_by_label[label] = account_id
            account_labels.append(label)

        self.status_var.set(f"Select an account ({len(self._accounts)} available)")
        self.account_combo.configure(values=account_labels, state="readonly")

    def _on_fetch_error(self, message: str):
        self.progress.stop()
        self.status_var.set("Sign-in failed.")
        messagebox.showerror("Sign-in failed", message)

    # -- cascade --------------------------------------------------------------

    def _on_account_selected(self, _event):
        account_id = self._account_ids_by_label.get(self.account_var.get())
        if account_id is None:
            return
        role_names = self._roles_by_account.get(account_id, [])
        self.role_var.set("")
        self.role_combo.configure(values=role_names, state="readonly" if role_names else "disabled")
        self.add_button.configure(state="disabled")
        if not role_names:
            self.status_var.set("No roles available for this account.")
        else:
            self.status_var.set(f"Select a role ({len(role_names)} available)")

    def _on_role_selected(self, _event):
        account_label = self.account_var.get()
        role_name = self.role_var.get()
        account_id = self._account_ids_by_label.get(account_label)
        if account_id is None or not role_name:
            return
        if not self.profile_name_var.get().strip():
            account_name = self._accounts[account_id]
            suggested = f"{account_name}-{role_name}".replace(" ", "-")
            self.profile_name_var.set(suggested)
        self.add_button.configure(state="normal")

    # -- submit --------------------------------------------------------------

    def _on_add(self):
        account_label = self.account_var.get()
        role_name = self.role_var.get()
        account_id = self._account_ids_by_label.get(account_label)
        if account_id is None or not role_name:
            return

        name = self.profile_name_var.get().strip()
        if not name:
            messagebox.showerror("Missing name", "Give this profile a name.")
            return
        if name in self._existing_names:
            messagebox.showerror("Duplicate name", f"A profile named '{name}' already exists.")
            return

        profile = config_store.ManagedProfile(
            profile_name=name,
            account_id=account_id,
            account_name=self._accounts[account_id],
            role_name=role_name,
            region=self._settings.default_region,
        )
        self._on_added(profile)
        self.destroy()
