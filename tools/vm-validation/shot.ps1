param([string]$Out="C:\rr\shots\shot.png")
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
Add-Type @"
using System;using System.Runtime.InteropServices;
public class Dpi{[DllImport("user32.dll")]public static extern bool SetProcessDPIAware();}
"@
[Dpi]::SetProcessDPIAware() | Out-Null
$b=[System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height
$g=[System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size)
New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null
$bmp.Save($Out,[System.Drawing.Imaging.ImageFormat]::Png)
"$($b.Width)x$($b.Height)" | Set-Content ($Out+".txt")
