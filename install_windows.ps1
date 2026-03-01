$ErrorActionPreference = 'Stop'

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LauncherDir = Join-Path $HOME 'bin'
$LauncherPath = Join-Path $LauncherDir 'gdcan-consola.cmd'

function Get-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @('py', '-3')
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        return @('python')
    }
    else {
        throw "No se encontró Python. Instala Python 3 y vuelve a ejecutar este script."
    }
}

$Py = Get-PythonCommand

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    if ($Py.Count -gt 1) {
        & $Py[0] $Py[1..($Py.Count - 1)] @Arguments
    }
    else {
        & $Py[0] @Arguments
    }
}

Write-Host "[1/4] Instalando dependencias Python..."
Invoke-Python @('-m', 'pip', 'install', '--user', '--upgrade', 'pip')
Invoke-Python @('-m', 'pip', 'install', '--user', 'playwright', 'textual', 'rich')

Write-Host "[2/4] Instalando Chromium para Playwright..."
Invoke-Python @('-m', 'playwright', 'install', 'chromium')

Write-Host "[3/4] Creando lanzador en $LauncherPath..."
New-Item -ItemType Directory -Path $LauncherDir -Force | Out-Null

$MainPy = Join-Path $ProjectDir 'main.py'
$CmdLine = if ($Py.Count -gt 1) {
    "$($Py[0]) $($Py[1]) \"$MainPy\" %*"
}
else {
    "$($Py[0]) \"$MainPy\" %*"
}

$CmdContent = "@echo off`r`n$CmdLine`r`n"
Set-Content -Path $LauncherPath -Value $CmdContent -Encoding ASCII

Write-Host "[4/4] Añadiendo $LauncherDir al PATH de usuario..."
$CurrentUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not $CurrentUserPath) { $CurrentUserPath = '' }

$Parts = $CurrentUserPath -split ';' | Where-Object { $_ -ne '' }
if ($Parts -notcontains $LauncherDir) {
    $NewPath = if ($CurrentUserPath.Trim() -eq '') { $LauncherDir } else { "$CurrentUserPath;$LauncherDir" }
    [Environment]::SetEnvironmentVariable('Path', $NewPath, 'User')
    Write-Host "PATH actualizado."
} else {
    Write-Host "PATH ya contiene $LauncherDir."
}

Write-Host ""
Write-Host "Instalación completada."
Write-Host "Puedes ejecutar: gdcan-consola"
Write-Host "Si no lo reconoce, cierra y abre la terminal."
