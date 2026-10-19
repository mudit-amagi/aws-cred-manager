"""One row in the profile table: account/role/profile/region/status labels
plus a Pause/Resume toggle and two action buttons (open AWS Console, open
in Cyberduck) that are only enabled while that row's credentials are
currently valid.

Kept as its own small widget class because rows are entirely rebuilt on
every table refresh (see window.py's _refresh_table) rather than diffed in
place -- simpler and far less bug-prone than trying to patch an existing
row's widgets in place when profiles are added/removed/reordered.

Each row occupies two grid rows in the shared table frame: a thin
separator line, then the actual content. Grid row numbering is owned by
window.py (see HEADER_GRID_ROWS there) -- this class just fills in
whatever starting row it's given.
"""
from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Callable

from .. import aws_sso, config_store

# Must match the number of entries in window.HEADINGS -- used so each
# row's separator line spans the full width of the table.
NUM_COLUMNS = 10

ICON_CONSOLE = "\U0001F310"  # globe
ICON_CYBERDUCK = "\U0001F986"  # duck -- literally Cyberduck's own mascot
ICON_PAUSE = "⏸"  # pause bars
ICON_RESUME = "▶"  # play triangle


def is_row_active(profile: config_store.ManagedProfile) -> bool:
    """True when this profile's credentials are currently valid -- i.e.
    there's something usable to hand off to another tool right now. Not
    affected by `paused` -- pausing only stops further refreshing, it
    doesn't revoke credentials that are already sitting in the
    credentials file and still valid.
    """
    if profile.last_error:
        return False
    if profile.last_expiration_epoch is None:
        return False
    return not aws_sso.is_expired(profile.last_expiration_epoch)


def status_text_for(profile: config_store.ManagedProfile) -> str:
    if profile.last_error:
        base = f"⚠ {profile.last_error[:40]}"
    elif profile.last_expiration_epoch is None:
        base = "not yet fetched"
    elif aws_sso.is_expired(profile.last_expiration_epoch):
        base = "expired"
    else:
        remaining = profile.last_expiration_epoch / 1000.0 - time.time()
        base = f"✓ valid ({int(remaining // 60)} min left)"
    return f"{ICON_PAUSE} paused -- {base}" if profile.paused else base


class ProfileRow:
    def __init__(
        self,
        parent: tk.Widget,
        grid_row: int,
        profile: config_store.ManagedProfile,
        on_console: Callable[[config_store.ManagedProfile], None],
        on_cyberduck: Callable[[config_store.ManagedProfile], None],
        on_toggle_pause: Callable[[str], None],
    ):
        self.profile_name = profile.profile_name
        self.selected_var = tk.BooleanVar(value=False)

        pad = {"padx": 6, "pady": 5}

        self._widgets = []

        sep_row = grid_row
        data_row = grid_row + 1

        sep = ttk.Separator(parent, orient="horizontal")
        sep.grid(row=sep_row, column=0, columnspan=NUM_COLUMNS, sticky="ew")
        self._widgets.append(sep)

        cb = ttk.Checkbutton(parent, variable=self.selected_var)
        cb.grid(row=data_row, column=0, **pad)
        self._widgets.append(cb)

        for col_index, field in enumerate(
            ("account_name", "account_id", "role_name", "profile_name", "region"), start=1
        ):
            lbl = ttk.Label(parent, text=getattr(profile, field), anchor="w")
            lbl.grid(row=data_row, column=col_index, sticky="w", **pad)
            self._widgets.append(lbl)

        self.status_label = ttk.Label(parent, text=status_text_for(profile), anchor="w")
        self.status_label.grid(row=data_row, column=6, sticky="w", **pad)
        self._widgets.append(self.status_label)

        # NOTE: this command closes over self.profile_name (a plain,
        # never-reassigned string) and calls back with just that name --
        # NOT over the `profile` object itself. An earlier version closed
        # over `profile` directly, which meant every click after the
        # first re-toggled based on the *original, stale* paused value
        # from when the row was built (update_from() only ever updates
        # this widget's displayed text, it doesn't -- and can't -- reach
        # into and mutate the `profile` object this closure captured).
        # That made Resume appear to do nothing: it just re-paused an
        # already-paused profile every time. Passing the name and having
        # the window look up the *current* profile fresh each click fixes
        # it for good, rather than patching this one symptom.
        self.pause_button = ttk.Button(
            parent, command=lambda: on_toggle_pause(self.profile_name)
        )
        self.pause_button.grid(row=data_row, column=7, sticky="ew", **pad)
        self._widgets.append(self.pause_button)

        self.console_button = ttk.Button(
            parent,
            text=f"{ICON_CONSOLE} AWS Console",
            command=lambda: on_console(profile),
        )
        self.console_button.grid(row=data_row, column=8, sticky="ew", **pad)
        self._widgets.append(self.console_button)

        self.cyberduck_button = ttk.Button(
            parent,
            text=f"{ICON_CYBERDUCK} Cyberduck",
            command=lambda: on_cyberduck(profile),
        )
        self.cyberduck_button.grid(row=data_row, column=9, sticky="ew", **pad)
        self._widgets.append(self.cyberduck_button)

        self.update_from(profile)

    def update_from(self, profile: config_store.ManagedProfile) -> None:
        self.status_label.configure(text=status_text_for(profile))
        state = "normal" if is_row_active(profile) else "disabled"
        self.console_button.configure(state=state)
        self.cyberduck_button.configure(state=state)
        if profile.paused:
            self.pause_button.configure(text=f"{ICON_RESUME} Resume")
        else:
            self.pause_button.configure(text=f"{ICON_PAUSE} Pause")

    def destroy(self) -> None:
        for w in self._widgets:
            w.destroy()
