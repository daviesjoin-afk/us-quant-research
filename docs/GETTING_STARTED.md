# Getting Started — Clean Windows Machine

## Supported source setup

Requirements:

- Windows 10/11
- Python 3.12 or 3.13
- PowerShell

From the repository root, run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1
```

The bootstrap script creates `.venv`, installs the desktop and test dependencies, runs the full pytest suite, runs the configuration doctor, compiles the source tree, and starts the desktop once in offscreen self-test mode.

After bootstrap succeeds, launch with:

```text
启动美股量化研究台.cmd
```

The launcher intentionally does not fall back to an arbitrary system Python. It uses `.venv` and retains `.venv313` only as a compatibility fallback for older developer machines.

## Verification

Run the same verification contract used by CI:

```powershell
.\scripts\verify.ps1
```

For core-only verification without the PySide6 desktop smoke test:

```powershell
.\scripts\verify.ps1 -CoreOnly
```

## IBKR Python API

The official IBKR Python API is an optional integration capability and is not installed from the normal project dependency set.

After installing the official TWS API on Windows, install its Python client into the managed environment with:

```powershell
.\scripts\install_ibkr_api.ps1
```

If the TWS API source is installed somewhere other than the default `C:\TWS API\source\pythonclient`, pass it explicitly:

```powershell
.\scripts\install_ibkr_api.ps1 -PythonClientPath "D:\path\to\pythonclient"
```

IBKR Gateway Paper connectivity is a separate integration check and requires the local Gateway/TWS process and Paper account state.

## Build the Windows client

Packaging requires the desktop dependencies and the official IBKR Python API in the managed environment.

```powershell
.\scripts\build_windows_client.ps1
```

The build script uses the installed `us-quant` package version for release metadata and skips optional local data folders that are absent in a clean clone instead of failing because of developer-machine state.
