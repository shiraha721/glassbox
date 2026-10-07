# Windows Explorer folder dialog for a studio running inside WSL, where no Windows Python exists.
# folders.py starts it through WSL interop with -EncodedCommand. The initial folder arrives in
# RTAGENT_DIALOG_INITIAL, already translated to a Windows path by WSLENV. The selected path is
# written as base64 UTF-8 because the console code page cannot carry every Unicode path.
# Exit status: 0 selected, 1 cancelled, 2 failed.
$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class RtagentFolderDialog
{
    [ComImport, Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")]
    private class FileOpenDialog { }

    [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IShellItem
    {
        void BindToHandler();
        void GetParent();
        void GetDisplayName(uint sigdn, [MarshalAs(UnmanagedType.LPWStr)] out string name);
    }

    // IFileOpenDialog up to GetResult, in vtable order; unused slots only hold their position.
    [ComImport, Guid("D57C7288-D4AD-4768-BE02-9D969532D960"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IFileOpenDialog
    {
        [PreserveSig] int Show(IntPtr owner);
        void SetFileTypes();
        void SetFileTypeIndex();
        void GetFileTypeIndex();
        void Advise();
        void Unadvise();
        void SetOptions(uint options);
        void GetOptions(out uint options);
        void SetDefaultFolder(IShellItem item);
        void SetFolder(IShellItem item);
        void GetFolder();
        void GetCurrentSelection();
        void SetFileName();
        void GetFileName();
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
        void SetFileNameLabel();
        void GetResult(out IShellItem item);
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern int SHCreateItemFromParsingName(
        string path, IntPtr context, [MarshalAs(UnmanagedType.LPStruct)] Guid iid,
        out IShellItem item);

    /// <summary>Create the folder dialog with its options and initial folder.</summary>
    public static object Prepare(string initial)
    {
        IFileOpenDialog dialog = (IFileOpenDialog)new FileOpenDialog();
        uint options;
        dialog.GetOptions(out options);
        // FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST.
        dialog.SetOptions(options | 0x20 | 0x40 | 0x800);
        dialog.SetTitle("Select workspace folder");
        dialog.SetOkButtonLabel("Select folder");
        IShellItem folder;
        if (!string.IsNullOrEmpty(initial)
            && SHCreateItemFromParsingName(initial, IntPtr.Zero, typeof(IShellItem).GUID, out folder) >= 0)
        {
            dialog.SetFolder(folder);
        }
        return dialog;
    }

    /// <summary>Show the dialog and return the selected file-system path, or null if cancelled.</summary>
    public static string Pick(string initial, IntPtr owner)
    {
        IFileOpenDialog dialog = (IFileOpenDialog)Prepare(initial);
        try
        {
            int result = dialog.Show(owner);
            if (result == unchecked((int)0x800704C7)) return null;  // ERROR_CANCELLED
            Marshal.ThrowExceptionForHR(result);
            IShellItem item;
            dialog.GetResult(out item);
            string path;
            item.GetDisplayName(0x80058000, out path);  // SIGDN_FILESYSPATH
            return path;
        }
        finally
        {
            Marshal.ReleaseComObject(dialog);
        }
    }
}
'@
    # The server, not the browser, starts this process, so Windows will not let it take the
    # foreground. An invisible topmost owner keeps the dialog above the browser window.
    $owner = New-Object System.Windows.Forms.Form -Property @{
        TopMost = $true; ShowInTaskbar = $false; FormBorderStyle = 'None'; Opacity = 0
        StartPosition = 'CenterScreen'; Size = New-Object System.Drawing.Size(1, 1)
    }
    $owner.Show()
    $owner.Activate()
    $path = [RtagentFolderDialog]::Pick($env:RTAGENT_DIALOG_INITIAL, $owner.Handle)
    $owner.Close()
    if ($null -eq $path) { exit 1 }
    [Console]::Out.Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($path)))
    exit 0
} catch {
    exit 2
}
