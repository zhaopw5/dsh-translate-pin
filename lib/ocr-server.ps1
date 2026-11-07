# DSH 截图翻译 —— Windows 内置 OCR 常驻工作进程（Windows PowerShell 5.1）
# 协议：stdin 每行一个图片绝对路径；完成后输出一行 "OK <结果json路径>" 或 "ERR <消息>"。
# 结果 JSON：{ "ok": true, "text": "...", "lang": "en|zh" }
#
# 同时跑 en-US 与 zh-Hans 两个识别引擎，再按结果里的 CJK 占比选更可信的一份：
# 英文界面用英文引擎明显更准，中文内容用中文引擎。
param(
  [switch]$Once,
  [string]$Path
)

$ErrorActionPreference = 'Stop'
try {
  [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
  [Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false
} catch {}

Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
Add-Type -AssemblyName System.Drawing | Out-Null

$script:asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($WinRtTask, $ResultType) {
  $asTask = $script:asTaskGeneric.MakeGenericMethod($ResultType)
  $netTask = $asTask.Invoke($null, @($WinRtTask))
  $netTask.Wait(-1) | Out-Null
  $netTask.Result
}

[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime] | Out-Null

function Get-OcrEngine([string]$tag) {
  try {
    $language = [Windows.Globalization.Language]::new($tag)
    return [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
  } catch {
    return $null
  }
}

$script:engineEn = Get-OcrEngine 'en-US'
$script:engineZh = Get-OcrEngine 'zh-Hans-CN'
if ($null -eq $script:engineZh) { $script:engineZh = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if ($null -eq $script:engineEn -and $null -eq $script:engineZh) {
  [Console]::Out.WriteLine('ERR no Windows OCR engine available')
  exit 1
}

$script:tmpDir = Join-Path $env:TEMP 'dsh-translate-pin\ocr'
if (-not (Test-Path -LiteralPath $script:tmpDir)) { New-Item -ItemType Directory -Path $script:tmpDir -Force | Out-Null }

function Prepare-Image([string]$imagePath) {
  # Windows OCR 对 12~14px 的界面文字识别很差，先放大 1.5~2 倍（小图更激进）。
  $src = [System.Drawing.Image]::FromFile($imagePath)
  try {
    $originalWidth = $src.Width
    $originalHeight = $src.Height
    $scale = 1.0
    if ($src.Width -lt 900) { $scale = 2.0 }
    elseif ($src.Width -lt 1500) { $scale = 1.5 }
    if ($src.Width * $scale -gt 3200) { $scale = 3200.0 / $src.Width }
    if ($scale -le 1.01) {
      return @{ path = $imagePath; scale = 1.0; width = $originalWidth; height = $originalHeight }
    }
    $w = [int]($src.Width * $scale)
    $h = [int]($src.Height * $scale)
    $bmp = New-Object System.Drawing.Bitmap -ArgumentList @($w, $h)
    $graphics = [System.Drawing.Graphics]::FromImage($bmp)
    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
    $graphics.DrawImage($src, 0, 0, $w, $h)
    $graphics.Dispose()
    $scaled = Join-Path $script:tmpDir ([guid]::NewGuid().ToString('N') + '.png')
    $bmp.Save($scaled, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    return @{ path = $scaled; scale = $scale; width = $originalWidth; height = $originalHeight }
  } finally {
    $src.Dispose()
  }
}

function Invoke-Engine($engine, [string]$imagePath) {
  if ($null -eq $engine) { return $null }
  $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($imagePath)) ([Windows.Storage.StorageFile])
  $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
  try {
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
  } finally {
    $stream.Dispose()
  }
  return $result
}

function Count-Cjk([string]$text) {
  if ([string]::IsNullOrEmpty($text)) { return 0 }
  return ([regex]::Matches($text, '[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]')).Count
}

function Get-LineBoxes($result, [double]$scale) {
  $list = @()
  if ($null -eq $result -or $null -eq $result.Lines) { return $list }
  foreach ($line in $result.Lines) {
    $minX = 1e9; $minY = 1e9; $maxX = -1e9; $maxY = -1e9
    $hasWord = $false
    foreach ($word in $line.Words) {
      $r = $word.BoundingRect
      if ($null -eq $r) { continue }
      $hasWord = $true
      if ($r.X -lt $minX) { $minX = $r.X }
      if ($r.Y -lt $minY) { $minY = $r.Y }
      if (($r.X + $r.Width) -gt $maxX) { $maxX = $r.X + $r.Width }
      if (($r.Y + $r.Height) -gt $maxY) { $maxY = $r.Y + $r.Height }
    }
    if (-not $hasWord) { continue }
    $list += @{
      text = [string]$line.Text
      x = [int][Math]::Floor($minX / $scale)
      y = [int][Math]::Floor($minY / $scale)
      w = [int][Math]::Ceiling(($maxX - $minX) / $scale)
      h = [int][Math]::Ceiling(($maxY - $minY) / $scale)
    }
  }
  return $list
}

function Get-WordBoxes($result, [double]$scale) {
  $list = @()
  if ($null -eq $result -or $null -eq $result.Lines) { return $list }
  foreach ($line in $result.Lines) {
    if ($null -eq $line.Words) { continue }
    foreach ($word in $line.Words) {
      $r = $word.BoundingRect
      if ($null -eq $r) { continue }
      $list += @{
        text = [string]$word.Text
        x = [int][Math]::Floor($r.X / $scale)
        y = [int][Math]::Floor($r.Y / $scale)
        w = [int][Math]::Ceiling($r.Width / $scale)
        h = [int][Math]::Ceiling($r.Height / $scale)
      }
    }
  }
  return $list
}

function Invoke-Ocr([string]$imagePath) {
  $prepared = Prepare-Image $imagePath
  try {
    $resEn = Invoke-Engine $script:engineEn $prepared.path
    $resZh = Invoke-Engine $script:engineZh $prepared.path
  } finally {
    if ($prepared.path -ne $imagePath) {
      try { Remove-Item -LiteralPath $prepared.path -Force } catch {}
    }
  }
  $textEn = if ($null -ne $resEn) { $resEn.Text } else { '' }
  $textZh = if ($null -ne $resZh) { $resZh.Text } else { '' }
  $cjk = Count-Cjk $textZh
  $length = [Math]::Max(1, $textZh.Length)
  $useZh = ($cjk -ge 2 -and ($cjk / $length) -gt 0.12) -or [string]::IsNullOrWhiteSpace($textEn)
  if (-not $useZh -and $null -eq $resEn) { $useZh = $true }
  $chosen = if ($useZh) { $resZh } else { $resEn }
  $text = if ($useZh) { $textZh } else { $textEn }
  $lines = Get-LineBoxes $chosen $prepared.scale
  $words = Get-WordBoxes $chosen $prepared.scale
  return @{
    text = $text
    lang = $(if ($useZh) { 'zh' } else { 'en' })
    width = $prepared.width
    height = $prepared.height
    lines = $lines
    words = $words
  }
}

function Write-Result([string]$text, [string]$lang, $lines, $words, [int]$width, [int]$height) {
  $name = [guid]::NewGuid().ToString('N') + '.json'
  $out = Join-Path $script:tmpDir $name
  $payload = @{
    ok = $true
    text = $text
    lang = $lang
    width = $width
    height = $height
    lines = $lines
    words = $words
  } | ConvertTo-Json -Compress -Depth 5
  [System.IO.File]::WriteAllText($out, $payload, (New-Object System.Text.UTF8Encoding $false))
  return $out
}

function Handle-Path([string]$imagePath) {
  try {
    if (-not (Test-Path -LiteralPath $imagePath)) {
      [Console]::Out.WriteLine('ERR missing image: ' + $imagePath)
      return
    }
    $result = Invoke-Ocr $imagePath
    $out = Write-Result $result.text $result.lang $result.lines $result.words $result.width $result.height
    [Console]::Out.WriteLine('OK ' + $out)
  } catch {
    [Console]::Out.WriteLine('ERR ' + $_.Exception.Message + ' @line ' + $_.InvocationInfo.ScriptLineNumber)
  }
}

if ($Once) {
  if ([string]::IsNullOrWhiteSpace($Path)) { [Console]::Out.WriteLine('ERR no path'); exit 1 }
  Handle-Path $Path
  exit 0
}

[Console]::Out.WriteLine('READY')
while ($true) {
  $line = [Console]::In.ReadLine()
  if ($null -eq $line) { break }
  $line = $line.Trim()
  if ($line -eq '') { continue }
  if ($line -eq 'QUIT') { break }
  Handle-Path $line
}
