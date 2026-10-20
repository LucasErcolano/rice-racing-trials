Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;using System.Runtime.InteropServices;
public class W{[DllImport("user32.dll")]public static extern bool SetProcessDPIAware();
[DllImport("user32.dll")]public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")]public static extern int GetWindowText(IntPtr h,System.Text.StringBuilder s,int n);}
"@
[W]::SetProcessDPIAware()|Out-Null
$p=[System.Windows.Forms.Cursor]::Position
$sb=New-Object System.Text.StringBuilder 256
[W]::GetWindowText([W]::GetForegroundWindow(),$sb,256)|Out-Null
"cursor=$($p.X),$($p.Y) screen=$([System.Windows.Forms.SystemInformation]::PrimaryMonitorSize) fg='$($sb.ToString())'"
