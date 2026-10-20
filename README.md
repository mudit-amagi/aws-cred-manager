# AWS Credential Manager

A small desktop app that creates and automatically refreshes temporary AWS
credentials in `~/.aws/credentials` for every account and role you have
access to through **AWS IAM Identity Center (SSO)**.

Use it if you frequently switch between AWS accounts, run scripts or the
`aws` CLI with named profiles, or browse S3 in Cyberduck, and you are tired
of repeating the SSO login every time credentials expire.

- Credentials never leave your machine. The app only talks to AWS itself
  (IAM Identity Center and the AWS sign-in endpoint). No third-party servers.
- Built and tested on **macOS**. The code is written to run on Windows and
  Linux too (see [Platform support](#platform-support)), but those paths
  haven't been built or run yet.

## Features

- **Add Row**: pick an Account, then a Role (cascading dropdowns, fetched live
  from AWS). Each row becomes a named profile.
- **Automatic refresh**: a background thread checks every 60 seconds and renews
  credentials before they expire. Keeps running when the window is closed.
- **Pause / Resume** per row, plus **Pause All** and **Resume All**. A paused
  row is skipped by refresh but keeps whatever credentials it already has.
- **AWS Console** button: opens the console in your browser, signed in as that
  role.
- **Cyberduck** button: opens that profile in Cyberduck, listing all buckets.
- **Verification code** shown in large type at the top right while a browser
  sign-in is pending; it disappears when the refresh finishes.
- **Start at login** option in Settings.

The AWS Console and Cyberduck buttons are only enabled while a row's
credentials are valid.

## Requirements

- Python 3.10+ (only needed to build). The app itself has code paths for
  macOS, Windows and Linux, but **only macOS has actually been built, run
  and tested** — see [Platform support](#platform-support) below.
- An IAM Identity Center **access portal URL**, usually
  `https://<your-org>.awsapps.com/start`
- Cyberduck: use the **latest version** (macOS integration only)

## Platform support

The app is written to be cross-platform — `app_paths.py` and
`platform_startup/` branch on `sys.platform` for app-data locations and
start-at-login, and there's a build script per OS
(`packaging/{macos,linux,windows}/`). In practice **only the macOS build has
been built and run**; the Windows and Linux paths are untested and may need
fixes. If you try one of them, issues and PRs are welcome.

## Install

### From source (macOS: builds the .app and a .dmg)

```bash
cd acm
rm -rf .venv-build
bash packaging/macos/build.sh
```

This creates `dist/AWS-Credential-Manager.dmg`. Open it and drag the app to
Applications. The build is ad-hoc signed only, so on first launch
**right-click the app and choose Open**.

If the build fails with `bad interpreter ... python3.14`, the old
`.venv-build` points at a Python that no longer exists. Delete it
(`rm -rf .venv-build`) and rebuild.

Untested scripts also exist for `packaging/linux/build.sh` and
`packaging/windows/build.ps1`.

### Run without building

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src python -m aws_cred_manager
```

## First-time setup

1. Open the app. Settings opens automatically on first run.
2. Enter your **access portal URL** and **SSO region**, and set the default
   profile region. Tick **Start automatically at login** if you want it.
3. Click **Add Row**. Your browser opens; check that the code on the AWS page
   matches the one shown in the app, and approve.
4. Choose an account and role, name the profile, and click Add.

Then use the profile as normal:

```bash
aws s3 ls --profile <profile-name>
```

## How it works

1. Sign-in uses the OIDC device-code flow (`sso-oidc`) and caches the SSO token
   in `~/.aws/sso/cache`, the same way the `aws` CLI does.
2. For each active row, the app calls `sso:GetRoleCredentials` and writes the
   result as static keys under `[<profile-name>]` in `~/.aws/credentials`
   (atomic writes, file mode 0600).
3. It does **not** write to `~/.aws/config`. Older versions did, which
   conflicted with the static keys. The app removes any sections it left
   there before.
4. Sections in `~/.aws/credentials` that the app does not manage are never
   touched.
5. If an SSO sign-in is ignored or times out, the app shows "Sign-in required"
   and waits. It does not keep opening browser tabs. Click **Refresh Now** to
   try again -- this also cancels any earlier sign-in that's still waiting
   on browser approval, so a closed/ignored tab never blocks the new
   attempt.

## Using the buttons

- **Pause / Resume** stops or restarts refresh for one row. **Pause All** and
  **Resume All** do the same for every row; they just set each row's own flag,
  so you can resume rows individually afterward.
- **AWS Console** uses AWS's documented federation sign-in. It needs network
  access to `signin.aws.amazon.com`.
- **Cyberduck**: the first click installs Cyberduck's "S3 (Credentials from AWS
  Command Line Interface)" connection profile; approve it in Cyberduck, then
  click again. After that it opens a bookmark that reads the profile from
  `~/.aws/credentials`. This depends on Cyberduck's own credential handling.

## Files and locations

| What | Where |
|---|---|
| Settings and profile list | `~/Library/Application Support/AWSCredManager/` |
| Log | `~/Library/Application Support/AWSCredManager/app.log` |
| Credentials written | `~/.aws/credentials` |
| SSO token cache | `~/.aws/sso/cache/` |
| Start-at-login agent | `~/Library/LaunchAgents/com.awscredmanager.agent.plist` |

## Behaviour notes

- Closing the window hides it; the app keeps refreshing in the background.
  Quit with Cmd+Q or the app menu.
- Only one instance runs. A second launch asks the first to show its window
  (loopback port `127.0.0.1:51823`).
- The app cannot close browser tabs it opened. After a sign-in you may need to
  close the old AWS tab yourself.

## Project layout

```
src/aws_cred_manager/
  aws_sso.py          SSO device-code flow, token cache, role credentials
  sync.py             refresh/add/remove profiles, credentials-file writes
  creds_file.py       section-aware INI editor with atomic writes
  config_store.py     settings.json and managed_profiles.json
  refresh_worker.py   background refresh thread
  external_tools.py   AWS Console and Cyberduck integration
  single_instance.py  one-instance lock and show-window IPC
  gui/                window, row widget, Add Row and Settings dialogs
  platform_startup/   start-at-login per OS
packaging/            PyInstaller spec and per-OS build scripts
tests/                unit tests (pytest)
```

The app version is defined once, in `src/aws_cred_manager/__init__.py`.

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest tests -q
```

The tests cover the non-GUI logic (credentials-file editing, config store,
refresh rules, pause behaviour, SSO helpers). The GUI is only smoke-tested.

## Troubleshooting

- **"Sign-in required" in the status bar**: click Refresh Now and approve in
  the browser.
- **A row shows an error**: your role may have been removed, or you lack
  access. Check `app.log`.
- **Nothing happens when reopening from the Dock**: quit fully (Cmd+Q) and
  relaunch; check `app.log` if it persists.
- **Cyberduck prompts for a Secret Access Key**: use the latest Cyberduck, make
  sure its "S3 (Credentials from AWS Command Line Interface)" profile is
  installed, and check `app.log`.

## License

[BSD Zero Clause License](LICENSE) (0BSD) — do whatever you want with it.
