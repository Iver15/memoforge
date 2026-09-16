@echo off
rem memoforge launcher (Windows): interpreter discovery -> __main__.py (TZ 5.6).
rem The plugin_data_dir chain mirrors scripts/memoforge/pylauncher.py (TZ 2.5).
setlocal EnableExtensions

set "MF_SCRIPT_DIR=%~dp0"
for %%I in ("%MF_SCRIPT_DIR%..") do set "MF_PLUGIN_ROOT=%%~fI"
set "MF_PROBE=import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)"

rem --- plugin_data_dir chain (TZ 2.5) --------------------------------------
rem A candidate is taken only when it can be created AND written to - the same criterion as
rem pylauncher.plugin_data_dir(), otherwise the wrapper and the package would disagree.
set "MF_DATA_DIR="
if defined CLAUDE_PLUGIN_DATA (
  call :mf_usable_dir "%CLAUDE_PLUGIN_DATA%"
  if not errorlevel 1 set "MF_DATA_DIR=%CLAUDE_PLUGIN_DATA%"
)
set "MF_HOME_CANDIDATE="
if defined LOCALAPPDATA set "MF_HOME_CANDIDATE=%LOCALAPPDATA%\claude\plugin-data\memoforge"
if not defined LOCALAPPDATA if defined USERPROFILE set "MF_HOME_CANDIDATE=%USERPROFILE%\.claude\plugin-data\memoforge"
if not defined MF_DATA_DIR if defined MF_HOME_CANDIDATE (
  call :mf_usable_dir "%MF_HOME_CANDIDATE%"
  if not errorlevel 1 set "MF_DATA_DIR=%MF_HOME_CANDIDATE%"
)
if not defined MF_DATA_DIR set "MF_DATA_DIR=%MF_PLUGIN_ROOT%\.data"
if not exist "%MF_DATA_DIR%\" mkdir "%MF_DATA_DIR%" >nul 2>&1

rem Service flag: print the resolved chain result and stop (identity test of TZ 5.6).
if /i "%~1"=="--print-plugin-data-dir" (
  echo %MF_DATA_DIR%
  exit /b 0
)

rem --- discovery cache (<plugin_data_dir>\launcher.json) --------------------
rem Read first: a hit costs one interpreter start instead of up to three (TZ 5.6, G7).
set "MF_CACHE=%MF_DATA_DIR%\launcher.json"
set "MF_PYTHON="
set "MF_PYTHON_ARGS="
set "MF_FROM_CACHE="
set "MF_CACHED="
if exist "%MF_CACHE%" for /f "usebackq tokens=2 delims=[]" %%A in (`findstr /c:"python_cmd" "%MF_CACHE%"`) do set "MF_CACHED=%%A"
if defined MF_CACHED set MF_CACHED=%MF_CACHED:"=%
if defined MF_CACHED set "MF_CACHED=%MF_CACHED:, = %"
if defined MF_CACHED for /f "tokens=1,2" %%A in ("%MF_CACHED%") do (
  set "MF_PYTHON=%%A"
  set "MF_PYTHON_ARGS=%%B"
)
if defined MF_PYTHON (
  "%MF_PYTHON%" %MF_PYTHON_ARGS% -c "%MF_PROBE%" >nul 2>&1
  if not errorlevel 1 set "MF_FROM_CACHE=1"
)
if not defined MF_FROM_CACHE set "MF_PYTHON="
if not defined MF_FROM_CACHE set "MF_PYTHON_ARGS="
if defined MF_FROM_CACHE goto :found

rem --- interpreter discovery: python -> python3 -> py -3 --------------------
rem A Windows Store alias returns a non-zero exit code and is skipped.
python -c "%MF_PROBE%" >nul 2>&1
if not errorlevel 1 (
  set "MF_PYTHON=python"
  goto :discovered
)
python3 -c "%MF_PROBE%" >nul 2>&1
if not errorlevel 1 (
  set "MF_PYTHON=python3"
  goto :discovered
)
py -3 -c "%MF_PROBE%" >nul 2>&1
if not errorlevel 1 (
  set "MF_PYTHON=py"
  set "MF_PYTHON_ARGS=-3"
  goto :discovered
)

echo mf: no Python ^>= 3.9 found ^(tried python, python3, py -3^) 1>&2
exit /b 2

:discovered
rem A miss (or a stale cache) rewrites the cache with what discovery just found.
if "%MF_PYTHON%"=="py" (
  >"%MF_CACHE%.tmp" echo {"schema_version": 1, "kind": "launcher", "python_cmd": ["py", "-3"], "source": "mf.cmd"}
) else (
  >"%MF_CACHE%.tmp" echo {"schema_version": 1, "kind": "launcher", "python_cmd": ["%MF_PYTHON%"], "source": "mf.cmd"}
)
move /y "%MF_CACHE%.tmp" "%MF_CACHE%" >nul 2>&1

:found
rem --- PYTHONPATH += <plugin_data_dir>\site-packages -----------------------
if defined PYTHONPATH (
  set "PYTHONPATH=%MF_DATA_DIR%\site-packages;%PYTHONPATH%"
) else (
  set "PYTHONPATH=%MF_DATA_DIR%\site-packages"
)

"%MF_PYTHON%" %MF_PYTHON_ARGS% "%MF_PLUGIN_ROOT%\scripts\memoforge\__main__.py" %*
exit /b %ERRORLEVEL%

:mf_usable_dir
rem Create the directory and prove it is writable; exit code 0 = usable.
set "MF_TRY=%~1"
if not defined MF_TRY exit /b 1
if not exist "%MF_TRY%\" mkdir "%MF_TRY%" >nul 2>&1
if not exist "%MF_TRY%\" exit /b 1
(echo ok)>"%MF_TRY%\.mf-write-probe" 2>nul
if not exist "%MF_TRY%\.mf-write-probe" exit /b 1
del /q "%MF_TRY%\.mf-write-probe" >nul 2>&1
exit /b 0
