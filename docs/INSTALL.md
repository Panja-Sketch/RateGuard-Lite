# Installing RateGuard-Lite

A step-by-step guide for people who are **not developers**. Windows is the primary platform; macOS and Linux are covered at the end of each step where they differ.

Every step shows the **exact command** and **what you should see**. If what you see is different, go to [Troubleshooting](#9-troubleshooting).

> RateGuard-Lite runs **entirely on your laptop**. It does not need internet access to run. Internet (or an internal package mirror, or a prepared "wheelhouse") is needed **once**, to install its Python packages.

---

## 1. Prerequisites

| What | Needed | Notes |
|---|---|---|
| **Operating system** | Windows 10 or 11, 64-bit (tested on Windows 11). macOS 12+ or a current Linux (secondary; setup script provided, not tested on this team's laptops). | |
| **Python** | **3.12.x tested** (3.12.10). **3.11 and newer** is what the project declares (`requires-python >= 3.11`). 3.11 is expected to work but was not run here; 3.13 and newer were **not tested**. 64-bit only. | Install from python.org or your organisation's software centre. In the installer **tick "Add python.exe to PATH"**, and you can **untick "for all users"**: a per-user install needs **no admin rights**. |
| **Git** | Any recent version (tested 2.55). Optional if you download a ZIP instead. | Git for Windows per-user install needs no admin rights where your organisation allows it. |
| **Disk space** | About **1 GB** free (the Python environment is about 0.5 GB; the offline wheelhouse is about 0.1 GB more; reports are small). | |
| **RAM** | **4 GB** minimum, **8 GB** recommended. Large exports (hundreds of tables) use more. | |
| **Browser** | Any current Chrome, Edge or Firefox, for the HTML report and the local UI (`rgl ui`). | |
| **Excel** | **Optional.** Only needed if you want to open the Excel reports yourself. The tool does not use Excel to read or write files. | Close Excel before running the tool on a file you have open there. |
| **Admin rights** | **Not needed** for: Python per-user install, Git per-user install, cloning, the virtual environment, all tool commands, the UI. You may need IT only if your organisation blocks the installers or the package download. | |

**Check what you already have** (PowerShell, from the Start menu):

```powershell
python --version
git --version
```

You should see, for example:

```
Python 3.12.10
git version 2.55.0.windows.5
```

If `python` is "not recognized", see [Troubleshooting](#9-troubleshooting). On some laptops the command is `py` instead: try `py -3.12 --version`.

---

## 2. Get the code

**Option A: Git (recommended)**

```powershell
cd $HOME
git clone <your internal repository URL> RateGuard-Lite
cd RateGuard-Lite
```

You should see `Cloning into 'RateGuard-Lite'...` followed by `done.`, and your prompt ends in `...\RateGuard-Lite>`.

**Option B: ZIP (if Git is not allowed)**

1. In the repository web page choose **Download ZIP**.
2. Right-click the ZIP, **Extract All...**, for example into `C:\Users\<you>\RateGuard-Lite`.
3. Open PowerShell and go there:

```powershell
cd $HOME\RateGuard-Lite
dir
```

You should see `README.md`, `pyproject.toml`, `requirements.lock`, `setup.ps1` and the folders `config`, `docs`, `src`.

> Keep the repo folder **free of real data**. Real files go to `C:\RGL-Data\<ticket>\` (see [FIRST_REAL_RUN.md](FIRST_REAL_RUN.md)).

---

## 3. Install

### 3a. One command (recommended)

**Windows**

```powershell
.\setup.ps1
```

**macOS / Linux**

```bash
chmod +x setup.sh
./setup.sh
```

You should see, in this order: `Using Python: Python 3.12.x`, a few minutes of quiet while packages install, then `Running self-test...`, a table of scenarios ending in **`Self-test passed.`**, and finally `Ready. Activate with: ...`. Typical time: **3 to 6 minutes** on a normal connection.

If PowerShell says that scripts are disabled, do section 4a first, then repeat.

### 3b. The same thing by hand

Use this if `setup.ps1` cannot run.

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.lock
pip install -e .
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.lock
pip install -e .
```

What you should see: after `activate`, your prompt starts with **`(.venv)`**. `pip install -r requirements.lock` prints about 55 `Collecting ...` / `Downloading ...` lines and ends with `Successfully installed ...`. `pip install -e .` ends with `Successfully installed rateguard-lite-1.1.0`.

(`setup.ps1` additionally installs the developer tools used by the test suite, `pip install -e ".[dev]"`. You do **not** need them to use the tool.)

---

## 4. Corporate laptop

### 4a. PowerShell blocks scripts ("running scripts is disabled")

You may see: `.\setup.ps1 cannot be loaded because running scripts is disabled on this system.` Allow scripts **for your user only** (no admin rights):

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Answer `Y`. Then check:

```powershell
Get-ExecutionPolicy -List
```

`CurrentUser` should now show `RemoteSigned`. If a group policy overrides it (the `MachinePolicy` or `UserPolicy` line shows a value), ask IT, or use the manual steps in 3b and run the tool through `.\.venv\Scripts\rgl.exe` which needs no script permission. If a ZIP-downloaded script is "blocked", run `Unblock-File .\setup.ps1`.

### 4b. pip behind a proxy or an internal mirror

Ask IT for two things: the **proxy address** and/or the **internal package index (mirror) URL**. Use only what IT gives you.

One-off, on the command line:

```powershell
pip install --proxy http://proxy.example.corp:8080 -r requirements.lock
pip install --index-url https://pypi.example.corp/simple -r requirements.lock
```

Permanent for your user, with a pip config file:

```powershell
pip config set global.proxy http://proxy.example.corp:8080
pip config set global.index-url https://pypi.example.corp/simple
pip config list
```

`pip config list` should show the values you set.

If you get SSL certificate errors and IT gives you a company root certificate, point pip to it: `pip config set global.cert C:\path\to\company-root.pem`. Use `--trusted-host pypi.example.corp` **only** if IT explicitly tells you to for that host; never use it to silence an error you do not understand.

To make `setup.ps1` use your settings, set the variables for the session first, for example `$env:PIP_INDEX_URL = "https://pypi.example.corp/simple"` and `$env:HTTPS_PROXY = "http://proxy.example.corp:8080"`.

### 4c. Offline install (no internet on the laptop)

You need **one machine with internet** and **the same Python version** (for example both 3.12, 64-bit Windows) to build the package folder (the "wheelhouse"), then you move it to the organisation laptop.

**On the machine with internet** (in the repo folder):

```powershell
.\scripts\build_wheelhouse.ps1
```

This runs, behind the scenes: `pip download -r requirements.lock -d wheelhouse --only-binary=:all: --platform win_amd64 --python-version <your Python minor version>` (plus the build tools). You should see `Done: 58 wheel files, ~97 MB in ...\wheelhouse`. If the target laptop has a different Python version than this machine: `.\scripts\build_wheelhouse.ps1 -PythonVersion 3.11`.

Copy the **whole repo folder including `wheelhouse\`** to the organisation laptop (USB stick or file share).

**On the organisation laptop** (in the repo folder; no internet needed):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --no-index --find-links wheelhouse -r requirements.lock
pip install --no-index --find-links wheelhouse --no-deps -e .
```

You should see `Successfully installed ...` for each command and no `Collecting` lines that try to reach a web address. Then continue with [section 5](#5-verify-the-installation). The `--find-links wheelhouse` on the second command lets pip find the build tools it needs offline.

If pip says `Could not find a version that satisfies the requirement ...`, the wheelhouse was built for a different Python version or platform than the laptop's: rebuild it with the matching `-PythonVersion`.

### 4d. Antivirus and locked files

- **Antivirus** may scan thousands of small files during `pip install` and make it slow, or quarantine `.pyd`/`.dll` files. If the install hangs or a file "disappears", ask IT to **exclude the repo folder (including `.venv\`)** from real-time scanning, or to allow-list `python.exe`.
- **Excel must be closed** on any workbook you are about to use as an input, and on any report in the output folder that you want to re-create. An open workbook is locked by Windows.
- **Do not put the repo in OneDrive / SharePoint-synced folders**: syncing locks files while the tool writes them. `C:\Users\<you>\RateGuard-Lite` or `C:\Tools\RateGuard-Lite` is fine.
- Avoid very long folder paths (Windows has a ~260 character limit): keep the repo close to the root.

---

## 5. Verify the installation

Open PowerShell in the repo folder and activate the environment (every new window needs this, see [section 6](#6-daily-use)):

```powershell
.\.venv\Scripts\Activate.ps1
```

**1. Version**

```powershell
rgl --version
```

You should see:

```
RateGuard-Lite 1.1.0
Data folder: C:\Users\<you>\AppData\Local\RateGuardLite
```

**2. Self-test.** It builds small synthetic rate books, runs the tool on them and checks every verdict:

```powershell
rgl selftest
```

You should see a table with `Scenario`, `Expected`, `Got`, `Result` (16 scenarios, every `Result` is `ok`) and the line **`Self-test passed.`** Typical time: **about 35 seconds (up to 1 to 2 minutes on a slow laptop)**.

**3. Sample data and one demo run.** This uses made-up data only:

```powershell
rgl sample --out demo
rgl run --approved demo\factor_mismatch\approved.xlsx `
        --jira     demo\factor_mismatch\jira.txt `
        --export   demo\factor_mismatch\export.xlsx `
        --baseline demo\factor_mismatch\baseline.xlsx `
        --out      demo\result
```

You should see a box with the verdict **`FAIL`** (the demo contains planted wrong values), a table "What we found", and `Reports written to ... demo\result`. Open `demo\result\reconciliation_report.html` in your browser: it opens on the **Problems by table** tab. The exit code is `1` for FAIL; that is expected. (Check it with `echo $LASTEXITCODE` in PowerShell.)

The `demo\` folder is git-ignored. Delete it when you are done: `Remove-Item -Recurse -Force demo`.

---

## 6. Daily use

Every time you open a new PowerShell window:

```powershell
cd $HOME\RateGuard-Lite
.\.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
```

Your prompt shows `(.venv)`. Then either:

- **Command line**: `rgl run --approved ... --jira ... --export ... --out ...` (see [FIRST_REAL_RUN.md](FIRST_REAL_RUN.md)), `rgl inspect FILE`, `rgl map --run RESULTFOLDER`.
- **Local UI**: `rgl ui`. You should see `Starting RateGuard-Lite at http://127.0.0.1:8501`; open that address in your browser. Stop it with `Ctrl+C`. It is reachable from your own laptop only.

`rgl --help` lists everything. You can also skip activation by calling the program directly: `.\.venv\Scripts\rgl.exe run ...`.

---

## 7. Update to a new version

```powershell
cd $HOME\RateGuard-Lite
git pull
.\.venv\Scripts\Activate.ps1
pip install -r requirements.lock
pip install -e .
rgl --version
rgl selftest
```

You should see the new version number and `Self-test passed.` If `git pull` complains about local changes, look at `git status`: the only changes you should ever have are in `config/` (for example `config/team_mappings.yaml`); ask your admin before discarding anything.

Without Git: download the new ZIP, extract it next to the old folder, copy your `config\team_mappings.yaml` over, and run `setup.ps1` in the new folder.

---

## 8. Uninstall

1. Close PowerShell windows that use the tool (and stop `rgl ui` with `Ctrl+C`).
2. **Delete the repo folder** (it contains the `.venv`):

```powershell
cd $HOME
Remove-Item -Recurse -Force .\RateGuard-Lite
```

3. **Delete the data folder** (learned mappings and, if you switched Snapshot History on, saved exports):

| OS | Data folder |
|---|---|
| Windows | `%LOCALAPPDATA%\RateGuardLite` (`Remove-Item -Recurse -Force "$env:LOCALAPPDATA\RateGuardLite"`) |
| macOS | `~/Library/Application Support/RateGuardLite` |
| Linux | `~/.local/share/rateguard-lite` |

4. Delete your ticket folders (`C:\RGL-Data\...`) according to your organisation's retention policy.

The tool installs nothing outside these places (no registry entries, no services).

---

## 9. Troubleshooting

| What you see | Cause | Fix |
|---|---|---|
| `'python' is not recognized as an internal or external command` | Python is not installed, or "Add python.exe to PATH" was not ticked. On Windows the Microsoft Store shortcut can also intercept `python`. | Install Python 3.12 (per user is fine) and tick **Add python.exe to PATH**; open a **new** PowerShell window. Try `py -3.12 --version`. If a Store window opens, turn off the *python.exe* app execution alias in *Settings > Apps > Advanced app settings > App execution aliases*. |
| `.\.venv\Scripts\Activate.ps1 cannot be loaded because running scripts is disabled` | PowerShell execution policy. | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (section 4a). Or run `.\.venv\Scripts\rgl.exe` directly, or use `cmd` and `.venv\Scripts\activate.bat`. |
| `pip` errors: `SSL: CERTIFICATE_VERIFY_FAILED`, `ProxyError`, `Connection timed out`, `Could not fetch URL` | Corporate proxy or TLS inspection. | Section 4b (proxy / index URL / company certificate), or the offline route in section 4c. |
| `Could not find a version that satisfies the requirement ...` | Python version or platform differs from the wheels, or the mirror lacks the package. | Check `python --version` (3.11+ 64-bit). For offline installs, rebuild the wheelhouse with the matching `-PythonVersion`. |
| `PermissionError: [Errno 13] ... .xlsx` or "file is being used by another process" | The workbook (or a report) is open in Excel. | Close Excel, run again. Use `--overwrite` only for an output folder you really want to replace. |
| `UnicodeEncodeError` / strange characters when output is redirected or pasted | The Windows console uses a legacy code page. | Update to the current version (it re-encodes output safely). Otherwise run `chcp 65001` first, or `$env:PYTHONIOENCODING = "utf-8"`. |
| `rgl ui`: `Port 8501 is already in use` | Another UI (or another program) uses the port. | `rgl ui --port 8502`. Or find and stop the old one: `Get-NetTCPConnection -LocalPort 8501`. |
| `No data-type row (DECIMAL/INTEGER/STRING...) found in the first N rows` | The tool does not recognise the layout of this Guidewire export (different type words or a different sheet structure). | Run `rgl inspect <export> --max-sheets 5`, then follow the calibration steps in [FIRST_REAL_RUN.md](FIRST_REAL_RUN.md) section 3 (`config/gw_layouts.yaml`). Send the **masked** `inspect` output, not the file, if you need help. |
| `rgl.exe ... An Application Control policy has blocked this file` (or antivirus quarantines `rgl.exe`) | The organisation blocks newly created `.exe` files. | Run the same commands through Python instead: `python -m rateguard_lite --version`, `python -m rateguard_lite run ...` (with the environment activated, or `.\.venv\Scripts\python.exe -m rateguard_lite ...`). If Python itself is blocked, ask IT to allow-list the repo folder. |
| `rgl` is not recognized | The environment is not activated. | `.\.venv\Scripts\Activate.ps1` (prompt must show `(.venv)`), or call `.\.venv\Scripts\rgl.exe`. |
| `setup.ps1`: `Python 3.11 or newer was not found` | Python missing or older than 3.11. | Install Python 3.12 (section 1). |
| `Output folder already exists` | `--out` points at an existing folder. | Choose a new folder name, or add `--overwrite` if you are sure. |
| `Internal error (...)` | A bug, not your mistake. | Re-run with `--debug`, and send the error text and `run.log` from the result folder (they contain no input data). Never send the input files. |

Still stuck? See [FIRST_REAL_RUN.md](FIRST_REAL_RUN.md) section 7 for exactly what to send back.
