# Run with powershell.exe -NoProfile -NonInteractive -STA -File this-script.ps1.
# FileDropList is the native CF_HDROP file selection, not image or text data.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

try {
    Add-Type -AssemblyName System.Windows.Forms
    if ([Threading.Thread]::CurrentThread.GetApartmentState() -ne [Threading.ApartmentState]::STA) {
        throw 'Clipboard file reader must run in an STA thread.'
    }
    # Keep the array as an explicit ConvertTo-Json input, including 0/1 items.
    [string[]] $paths = @([System.Windows.Forms.Clipboard]::GetFileDropList() | ForEach-Object { [string] $_ })
    $json = ConvertTo-Json -InputObject $paths -Compress
    [Console]::Out.WriteLine($json)
} catch {
    [Console]::Error.WriteLine('Cannot read Windows clipboard file list: ' + $_.Exception.Message)
    exit 1
}
