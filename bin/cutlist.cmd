@echo off
rem cutlist -- launcher shim, Windows command processor.
rem
rem The sibling of bin/cutlist, and it must stay in step with it. The plugin's
rem bin/ directory is placed on the Bash tool's PATH while the plugin is
rem enabled, so `cutlist ...` resolves to one of these two depending on which
rem shell is asking.
rem
rem Self-locating, for the same reason as the POSIX shim: CLAUDE_PLUGIN_ROOT
rem and CLAUDE_PLUGIN_DATA are exported to hook and MCP subprocesses only and
rem are EMPTY in an ordinary Bash tool call, so a shim that depends on them
rem works under a hook and fails for a person.
rem
rem Search order, identical to bin/cutlist and to venv_home() in
rem scripts/cutlist_env.py:
rem   1. CUTLIST_HOME        -- an explicit pin; empty of an environment is a
rem                             hard error, never a fallback
rem   2. CLAUDE_PLUGIN_DATA  -- the harness's answer; falls through if unusable
rem   3. %USERPROFILE%\.claude\plugins\data\cutlist*
rem
rem USERPROFILE, not HOME: CMD does not define HOME, and Path.home() in
rem cutlist_env.py resolves to USERPROFILE on Windows, so the two agree.
rem
rem Written with gotos rather than parenthesised if-blocks throughout. WHY: a
rem variable assigned inside a block is not readable inside that same block
rem without delayed expansion, so the obvious nested-if version reads the value
rem the variable had BEFORE the block -- it does not error, it silently tests
rem the wrong thing. Straight-line control flow removes the trap instead of
rem depending on remembering it.
setlocal EnableExtensions

set "PY="

if not defined CUTLIST_HOME goto :try_plugin_data
call :find "%CUTLIST_HOME%"
if defined PY goto :run
>&2 echo cutlist: CUTLIST_HOME is set to "%CUTLIST_HOME%", which holds no cutlist environment.
>&2 echo cutlist: refusing to fall back to a different one -- a pin that quietly runs
>&2 echo cutlist: something else is worse than no pin. Unset it, or run
>&2 echo cutlist: /cutlist:cutlist-doctor to install one there.
exit /b 127

:try_plugin_data
if not defined CLAUDE_PLUGIN_DATA goto :try_default
call :find "%CLAUDE_PLUGIN_DATA%"
if defined PY goto :run

:try_default
rem `for /d` expands the glob in sorted order. The first match that actually
rem holds an environment wins, which is what makes this agree with venv_home()
rem whenever there is anything to agree about. Normally exactly one directory
rem matches: the data directory is stable across plugin updates, unlike the
rem version-scoped plugin root.
for /d %%D in ("%USERPROFILE%\.claude\plugins\data\cutlist*") do (
    if not defined PY call :find "%%~fD"
)
if defined PY goto :run

>&2 echo cutlist: no cutlist environment found.
>&2 echo cutlist: searched CUTLIST_HOME, CLAUDE_PLUGIN_DATA, and
>&2 echo cutlist:   %USERPROFILE%\.claude\plugins\data\cutlist*
>&2 echo cutlist: run /cutlist:cutlist-doctor to set one up.
rem 127 is the conventional "command not found", and it sits outside the CLI's
rem own exit contract (0 ok, 1 error, 2 verification failed) on purpose: a
rem caller gating on 2 must never read a missing interpreter as a failed
rem verification.
exit /b 127

:run
rem `-m cutlist.cli`, not the venv's cutlist.exe console script: a console
rem script embeds an absolute path written at install time and breaks the
rem moment the data directory moves or the environment is rebuilt under it.
"%PY%" -m cutlist.cli %*
exit /b %ERRORLEVEL%

:find
rem Both layouts, in the same order as the POSIX shim, so neither file can be
rem the one that finds a second environment the other does not.
if exist "%~1\venv\bin\python" set "PY=%~1\venv\bin\python"
if not defined PY if exist "%~1\venv\Scripts\python.exe" set "PY=%~1\venv\Scripts\python.exe"
goto :eof
