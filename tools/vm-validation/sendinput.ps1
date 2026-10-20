param([string]$Action="key",[string]$Scan="1C",[int]$X=0,[int]$Y=0)
Add-Type @"
using System;using System.Runtime.InteropServices;
public class SI{
[StructLayout(LayoutKind.Sequential)]public struct MOUSEINPUT{public int dx,dy;public uint mouseData,dwFlags,time;public IntPtr extra;}
[StructLayout(LayoutKind.Sequential)]public struct KEYBDINPUT{public ushort vk,scan;public uint flags,time;public IntPtr extra;}
[StructLayout(LayoutKind.Explicit,Size=40)]public struct INPUT{[FieldOffset(0)]public uint type;[FieldOffset(8)]public MOUSEINPUT mi;[FieldOffset(8)]public KEYBDINPUT ki;}
[DllImport("user32.dll",SetLastError=true)]public static extern uint SendInput(uint n,INPUT[] i,int sz);
[DllImport("user32.dll")]public static extern bool SetProcessDPIAware();
public static void Key(ushort sc,bool up){var i=new INPUT[1];i[0].type=1;i[0].ki.scan=sc;i[0].ki.flags=(uint)(0x8|(up?2:0));SendInput(1,i,Marshal.SizeOf(typeof(INPUT)));}
public static void Mouse(int x,int y,uint fl){var i=new INPUT[1];i[0].type=0;i[0].mi.dx=x;i[0].mi.dy=y;i[0].mi.dwFlags=fl;SendInput(1,i,Marshal.SizeOf(typeof(INPUT)));}
}
"@
[SI]::SetProcessDPIAware()|Out-Null
if($Action -eq "key"){foreach($s in $Scan.Split(",")){$c=[Convert]::ToUInt16($s,16);[SI]::Key($c,$false);Start-Sleep -m 120;[SI]::Key($c,$true);Start-Sleep -m 150}}
if($Action -eq "click"){
  $ax=[int]($X*65535/1366);$ay=[int]($Y*65535/768)
  [SI]::Mouse($ax,$ay,0x8001);Start-Sleep -m 400   # MOVE|ABSOLUTE
  [SI]::Mouse($ax,$ay,0x8001+0x2);Start-Sleep -m 120   # LEFTDOWN
  [SI]::Mouse($ax,$ay,0x8001+0x4)                      # LEFTUP
}
"$Action ok $(Get-Date -f HH:mm:ss)"
