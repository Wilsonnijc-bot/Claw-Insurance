$ErrorActionPreference = 'Stop'
$taskDoc = 'D:\桌面\Claw-Insurance\.tmp-doc-cleanlist\清单-内容整理版.docx'
$taskRenderDir = 'D:\桌面\Claw-Insurance\.tmp-doc-cleanlist\render-word-final'
$taskPdf = Join-Path $taskRenderDir '清单-内容整理版.pdf'
New-Item -ItemType Directory -Force -Path $taskRenderDir | Out-Null
$taskWord = New-Object -ComObject Word.Application
$taskWord.Visible = $false
$taskWord.DisplayAlerts = 0
try {
    $taskOpened = $taskWord.Documents.Open($taskDoc, $false, $true)
    $taskOpened.ExportAsFixedFormat($taskPdf, 17)
    $taskOpened.Close($false)
} finally {
    $taskWord.Quit()
}
Write-Output $taskPdf
