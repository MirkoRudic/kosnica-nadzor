param([switch]$NoStartup)
$ErrorActionPreference = "Stop"
$d = Split-Path -Parent $MyInvocation.MyCommand.Path
$w = New-Object -ComObject WScript.Shell
$items = @(
  @{ p = [Environment]::GetFolderPath("Desktop") + "\Košnica Nadzor.lnk"; a = "" },
  @{ p = [Environment]::GetFolderPath("Startup") + "\Košnica Nadzor.lnk"; a = "--tiho" }
)
if ($NoStartup) { $items = @($items[0]) }
foreach ($i in $items) {
  $s = $w.CreateShortcut($i.p)
  $s.TargetPath = "$d\.venv\Scripts\pythonw.exe"
  $s.Arguments = ("`"$d\nadzor.pyw`" " + $i.a).Trim()
  $s.WorkingDirectory = $d
  $s.IconLocation = "$d\static\kosnica.ico"
  $s.Save()
  Write-Output "precica: $($i.p)"
}
