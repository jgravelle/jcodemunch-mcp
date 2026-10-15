<#
.SYNOPSIS
  Collapse the duplicate / stale jMunch distributions on one interpreter and
  reinstall each from its local dev tree.

  ⚠ SUITE-WIDE tooling that lives in the jcodemunch repo. It repairs all five
  products, not just this one; it is here because C:\MCPs is not a git repo and
  this was the flagship with a clean tree. It is EXCLUDED from the sdist -- the
  paths are specific to jjg's dev box and mean nothing to a PyPI consumer.

.DESCRIPTION
  Every suite product has accumulated duplicate distributions on
  C:\Program Files\Python310, plus pip "~" staging directories left by
  uninstalls that were interrupted partway.

  The cause is mechanical and it is why this script exists:
  ⚠⚠ ON WINDOWS, `pip uninstall` CANNOT REMOVE A PACKAGE WHOSE CONSOLE
  SCRIPT IS RUNNING. It fails with WinError 32 on <name>.exe -- but only
  AFTER it has already removed the .pth and dist-info. The package is then
  half-gone: it will not import, and a "~<name>.dist-info" turd is left
  behind. That is exactly how the mess this script repairs was created, most
  recently on 2026-08-29.

  So every MCP server has to be stopped first. Step 1 does it: with -Apply
  it stops every running j*munch process (the jcodemunch, jdocmunch and
  jdatamunch servers, and any hook they spawned), waits until they are gone,
  and refuses if one survives. It stops them again before each uninstall,
  because a client can restart a server while the script runs. Your MCP
  clients lose those servers until you restart the clients, which you need to
  do after the reinstall anyway.

.PARAMETER Apply
  Actually make changes, including stopping the servers. Without it the
  script only reports (dry run), and names the processes it would stop.

.PARAMETER Python
  Interpreter to repair. Defaults to the one the MCP servers launch from.

.PARAMETER StopServersOnly
  Run step 1 alone (with -Apply, stop the servers) and exit.

.PARAMETER ProcessPattern
  Regex over process names that step 1 stops. The default matches the
  suite's console scripts; tests pass a narrower one.

.EXAMPLE
  pwsh -File C:\MCPs\jcodemunch-mcp\scripts\repair-munch-installs.ps1
  pwsh -File C:\MCPs\jcodemunch-mcp\scripts\repair-munch-installs.ps1 -Apply
  pwsh -File C:\MCPs\jcodemunch-mcp\scripts\repair-munch-installs.ps1 -StopServersOnly -Apply
#>

[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$Python = 'C:\Program Files\Python310\python.exe',
    [switch]$StopServersOnly,
    [string]$ProcessPattern = '^j[a-z]*munch'
)

$ErrorActionPreference = 'Stop'

# Packages to repair, and the local tree each is reinstalled from.
# ⚠ Only these names are ever touched. Nothing else on the interpreter is
#   uninstalled, upgraded, or inspected for removal.
$Targets = @(
    @{ Dist = 'jcodemunch-mcp'; Tree = 'C:\MCPs\jcodemunch-mcp' }
    @{ Dist = 'jdocmunch-mcp';  Tree = 'C:\MCPs\jdocmunch-mcp'  }
    @{ Dist = 'jdatamunch-mcp'; Tree = 'C:\MCPs\jdatamunch-mcp' }
    @{ Dist = 'jragmunch';      Tree = 'C:\MCPs\jragmunch-cli'  }
    @{ Dist = 'jmunch-mcp';     Tree = 'C:\MCPs\jmunch-mcp'     }
)

function Write-Head($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }

# ⚠⚠ EVERY call to $Python goes through this. Windows PowerShell 5.1 turns
#    each stderr line of a native command into an ErrorRecord once stderr is
#    redirected, and under `$ErrorActionPreference = 'Stop'` the first one
#    TERMINATES the script. pip writes a blank line to stderr after a
#    successful editable install, so the 2026-10-01 run reinstalled
#    jcodemunch-mcp, then died before the other four products. The exit code
#    is the verdict, read from $LASTEXITCODE; stderr is text.
#    -Merge returns stderr lines too (pip's messages); without it only stdout.
function Invoke-Py {
    param([string[]]$PyArgs, [switch]$Merge)
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $all = & $Python @PyArgs 2>&1 } finally { $ErrorActionPreference = $old }
    foreach ($x in $all) {
        if ($x -is [System.Management.Automation.ErrorRecord]) {
            # A blank stderr line has an empty message and would print as the
            # exception's type name.
            $m = $x.Exception.Message
            if ($Merge -and $m) { $m }
        } else { "$x" }
    }
}

# --------------------------------------------------------------------------
# Step 1: stop every j*munch server, because a running one holds its .exe.
# ⚠⚠ The refusal stays. If a process survives Stop-Process (access denied, a
#    client that restarts it at once), uninstalling would still remove the
#    .pth and dist-info and then fail on the locked .exe -- the exact damage
#    this script repairs. Stopping first only removes the hand-work.
# --------------------------------------------------------------------------
function Get-MunchServers {
    @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match $ProcessPattern })
}

function Stop-MunchServers {
    param([string]$When)
    $procs = Get-MunchServers
    if ($procs.Count -eq 0) { return }
    Write-Host "  stopping $($procs.Count) process(es) $When" -ForegroundColor Yellow
    $procs | Select-Object Id, ProcessName, Path | Format-Table -AutoSize | Out-String | Write-Host
    foreach ($p in $procs) {
        try { Stop-Process -Id $p.Id -Force -ErrorAction Stop }
        catch { Write-Host "  could not stop $($p.ProcessName) ($($p.Id)): $($_.Exception.Message)" -ForegroundColor Red }
    }
    # Stop-Process returns before the process has exited and released its .exe.
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-MunchServers).Count -gt 0 -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 200 }
}

function Assert-NoMunchServers {
    $left = Get-MunchServers
    if ($left.Count -eq 0) { return }
    Write-Host "`nREFUSING TO CONTINUE. These processes still hold their .exe open:" -ForegroundColor Red
    $left | Select-Object Id, ProcessName, Path | Format-Table -AutoSize | Out-String | Write-Host
    Write-Host @'
Uninstalling now would remove each package's .pth and dist-info, then fail on
the locked .exe -- leaving the package unimportable and a "~" turd behind.
That is the exact damage this script repairs.

Close the client that keeps restarting them (Claude Code / Claude Desktop /
an MCP-connected editor), then re-run.
'@ -ForegroundColor Yellow
    exit 1
}

Write-Head 'Step 1: stop the servers'
Write-Host "Mode        : $(if ($Apply) { 'APPLY (will modify)' } else { 'DRY RUN (no changes)' })"
$running = Get-MunchServers
if ($running.Count -eq 0) {
    Write-Host 'No j*munch processes running.' -ForegroundColor Green
} elseif ($Apply) {
    Stop-MunchServers 'before the repair'
    Assert-NoMunchServers
    Write-Host 'All j*munch processes stopped.' -ForegroundColor Green
} else {
    Write-Host "Would stop $($running.Count) process(es):" -ForegroundColor Yellow
    $running | Select-Object Id, ProcessName, Path | Format-Table -AutoSize | Out-String | Write-Host
}
if ($StopServersOnly) { exit 0 }

# --------------------------------------------------------------------------
# Pre-flight
# --------------------------------------------------------------------------
Write-Head 'Pre-flight'

if (-not (Test-Path $Python)) { throw "Interpreter not found: $Python" }
Write-Host "Interpreter : $Python"

# --------------------------------------------------------------------------
# Report current state
# --------------------------------------------------------------------------
# ⚠⚠ SINGLE QUOTES ONLY inside this Python. Windows PowerShell 5.1 strips an
#    embedded double quote when it passes an argument to a native program, so
#    `d.metadata["Name"] or ""` reached python.exe as a syntax error on line 4
#    and the report was empty (2026-10-01, run as `powershell -File`). pwsh 7
#    keeps them, which is why it worked where it was written.
$probe = @'
import importlib.metadata as md
rows = set()
for d in md.distributions():
    n = (d.metadata['Name'] or '')
    if 'munch' in n.lower():
        rows.add((n, d.version, str(d._path)))
for n, v, p in sorted(rows):
    print(chr(9).join((n, v, p)))
'@

function Get-MunchDists {
    $out = Invoke-Py @('-c', $probe)
    if (-not $out) { return @() }
    $out | ForEach-Object {
        $f = $_ -split "`t"
        [pscustomobject]@{ Name = $f[0]; Version = $f[1]; Path = $f[2] }
    }
}

Write-Head 'Current distributions'
$before = @(Get-MunchDists)
$before | Sort-Object Name, Version | Format-Table -AutoSize | Out-String | Write-Host

$targetNames = $Targets.Dist
$unmanaged = $before | Where-Object { $targetNames -notcontains $_.Name }
if ($unmanaged) {
    Write-Host 'NOT MANAGED by this script -- review by hand, they may be deliberate:' -ForegroundColor Yellow
    $unmanaged | Select-Object Name, Version | Format-Table -AutoSize | Out-String | Write-Host
}

# --------------------------------------------------------------------------
# Sweep pip's "~" staging turds for the managed names only.
# ⚠⚠ THIS RUNS BEFORE THE REINSTALLS, AND THE ORDER IS THE FIX.
#    It used to run last, and its safety check looked for a live
#    site-packages package directory. Converting a package to EDITABLE deletes
#    that directory -- so the reinstall destroyed the evidence the guard
#    depended on, and an 8.7 MB orphan was skipped as "may be the only copy".
#    Caught on the first real -Apply run, 2026-08-29.
# --------------------------------------------------------------------------
Write-Head 'Staging leftovers ("~" dist-info)'

$siteDirs = Invoke-Py @('-c', 'import site,sys;[print(p) for p in set(site.getsitepackages()+([site.getusersitepackages()] if site.ENABLE_USER_SITE else []))]')
$turds = foreach ($sd in $siteDirs) {
    if (Test-Path $sd) {
        Get-ChildItem -Path $sd -Directory -Filter '~*munch*' -ErrorAction SilentlyContinue
    }
}

if (-not $turds) {
    Write-Host 'none'
} else {
    # pip stages a removal by replacing the FIRST CHARACTER with "~":
    # jcodemunch_mcp -> ~codemunch_mcp. A turd is safe to delete when the
    # package it belongs to is still INSTALLED (any version, any layout) --
    # which stays true whether that install is regular or editable.
    foreach ($d in $turds) {
        $suffix = $d.Name.Substring(1)
        $stem   = if ($suffix -match '^(?<s>.+?)-[0-9].*\.dist-info$') { $Matches.s } else { $suffix }

        $owner = $Targets | Where-Object { ($_.Dist -replace '-', '_') -like "?$stem" }
        $liveDist = if ($owner) { $before | Where-Object { $_.Name -eq $owner[0].Dist } } else { $null }

        if (-not $liveDist) {
            Write-Host "  SKIP $($d.Name) - no installed package claims it; may be the only copy" -ForegroundColor Yellow
        } elseif ($Apply) {
            $mb = [math]::Round((Get-ChildItem $d.FullName -Recurse -File -ErrorAction SilentlyContinue |
                                 Measure-Object Length -Sum).Sum / 1MB, 1)
            Remove-Item -Recurse -Force $d.FullName
            Write-Host "  removed $($d.Name)  ($mb MB, owned by $($owner[0].Dist))" -ForegroundColor Green
        } else {
            Write-Host "  would remove $($d.Name)  (owned by $($owner[0].Dist))"
        }
    }
}

# --------------------------------------------------------------------------
# Work
# --------------------------------------------------------------------------
foreach ($t in $Targets) {
    $dist = $t.Dist
    $tree = $t.Tree

    Write-Head "$dist"

    $have = @($before | Where-Object { $_.Name -eq $dist })
    if ($have.Count -eq 0) {
        Write-Host '  no distribution installed'
    } else {
        Write-Host "  installed: $(($have.Version | Sort-Object) -join ', ')"
    }

    if (-not (Test-Path $tree)) {
        Write-Host "  SKIP - local tree missing: $tree" -ForegroundColor Yellow
        continue
    }
    $treeVer = (Select-String -Path (Join-Path $tree 'pyproject.toml') `
                              -Pattern '^version\s*=' | Select-Object -First 1).Line
    Write-Host "  tree     : $tree  ($($treeVer -replace '\s',''))"

    # ⚠ Reinstalling editable CHANGES THE NATURE of a regular install: the
    #   package will thereafter track the working tree, so an uncommitted edit
    #   is live for every MCP client. That is usually what a dev box wants, but
    #   it is a decision, so say it out loud rather than performing it quietly.
    $importsFromTree = $false
    $modName = $dist -replace '-', '_'
    $loc = Invoke-Py @('-c', "import $modName as m; print(m.__file__)")
    if ($LASTEXITCODE -eq 0 -and $loc -like "$tree*") { $importsFromTree = $true }
    if (-not $importsFromTree) {
        Write-Host '  NOTE: currently a regular install; this will convert it to editable' -ForegroundColor Yellow
    }

    if (-not $Apply) {
        Write-Host "  would: uninstall x$($have.Count), then 'pip install -e' from the tree"
        continue
    }

    # A client may have restarted a server since step 1; its .exe would be
    # locked again, and this uninstall is the one that would half-remove it.
    Stop-MunchServers "before uninstalling $dist"
    Assert-NoMunchServers

    # Uninstall every copy. pip removes ONE distribution per invocation, so
    # loop until the probe reports none. Bounded so a stuck state cannot spin.
    for ($i = 1; $i -le 6; $i++) {
        $still = @(Get-MunchDists | Where-Object { $_.Name -eq $dist })
        if ($still.Count -eq 0) { break }
        Write-Host "  uninstall pass $i (remaining: $($still.Count))"
        Invoke-Py @('-m', 'pip', 'uninstall', '-y', $dist) -Merge | Out-Null
    }

    $left = @(Get-MunchDists | Where-Object { $_.Name -eq $dist })
    if ($left.Count -gt 0) {
        Write-Host "  WARNING: $($left.Count) copy still present after 6 passes" -ForegroundColor Yellow
        $left | Format-Table -AutoSize | Out-String | Write-Host
    }

    # ⚠ --no-deps is deliberate. This interpreter is a kitchen sink whose
    #   `packaging` is pinned below 25 by three ML packages; a dependency
    #   resolve here can break working installs to satisfy a reinstall.
    #   Dependencies are already present -- only the package itself is being
    #   put back.
    # ⚠ Build isolation is left ON: hatchling is not installed on this
    #   interpreter, so --no-build-isolation fails with
    #   "Cannot import 'hatchling.build'".
    Write-Host '  reinstalling editable from tree'
    Push-Location $tree
    try {
        Invoke-Py @('-m', 'pip', 'install', '-e', '.', '--no-deps') -Merge |
            Select-String -Pattern 'error|ERROR|Successfully' | ForEach-Object {
                Write-Host "    $_"
            }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "    pip install exited $LASTEXITCODE for $dist" -ForegroundColor Red
        }
    } finally { Pop-Location }
}

# --------------------------------------------------------------------------
# Verify
# --------------------------------------------------------------------------
Write-Head $(if ($Apply) { 'After' } else { 'After (unchanged - dry run)' })
$after = @(Get-MunchDists)
$after | Sort-Object Name, Version | Format-Table -AutoSize | Out-String | Write-Host

# ⚠ A dist whose metadata lives INSIDE its own source tree is that tree's
#   build artifact (setuptools writes src/<pkg>.egg-info), not a competing
#   install. jragmunch builds with setuptools and always has one, so counting
#   it reported "STILL DUPLICATED" on a correct result.
$treePaths = $Targets.Tree
$installed = $after | Where-Object {
    $p = $_.Path
    -not ($treePaths | Where-Object { $p -like "$_*" })
}
$dupes = $installed | Group-Object Name | Where-Object { $_.Count -gt 1 }
if ($dupes -and -not $Apply) {
    Write-Host 'Duplicated now (this is the input state, not a failure):' -ForegroundColor Yellow
    $dupes | ForEach-Object { Write-Host "  $($_.Name) x$($_.Count)" }
} elseif ($dupes) {
    Write-Host 'STILL DUPLICATED:' -ForegroundColor Red
    $dupes | ForEach-Object { Write-Host "  $($_.Name) x$($_.Count)" }
} else {
    Write-Host 'One distribution per package.' -ForegroundColor Green
}

Write-Head 'Import + wire version'
foreach ($t in $Targets) {
    $mod = $t.Dist -replace '-', '_'
    if ($mod -eq 'jragmunch') { $mod = 'jragmunch' }
    $code = "import $mod as m; print('$($t.Dist)', getattr(m,'__version__','(no __version__)'), '<-', getattr(m,'__file__','?'))"
    $r = Invoke-Py @('-c', $code) -Merge
    if ($LASTEXITCODE -eq 0) { Write-Host "  $r" } else { Write-Host "  $($t.Dist): NOT IMPORTABLE" -ForegroundColor Red }
}

if (-not $Apply) {
    Write-Host "`nDry run only. Re-run with -Apply to make these changes." -ForegroundColor Yellow
} else {
    Write-Host "`nDone. Restart your MCP clients so they load the new code." -ForegroundColor Green
    Write-Host 'A running server keeps serving what it loaded at startup; the filesystem does not change that.'
}
