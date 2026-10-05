<#
.SYNOPSIS
  Download portable Blender and FFmpeg into tools/ for local development.

.DESCRIPTION
  Public downloads only; nothing is installed system-wide.
  Blender is fetched from public blender.org release mirrors and verified against the
  official blender.org SHA-256 list. FFmpeg is fetched from the public BtbN GitHub
  release and verified against that release's checksums file.
  Writes tools/tools.json, which `wbs doctor` and the pipeline read.
#>
param(
    [string]$BlenderVersion = "5.1.2",
    [string[]]$BlenderMirrors = @(
        "https://mirrors.aliyun.com/blender/release",
        "https://download.blender.org/release"
    ),
    [string]$FfmpegAsset = "ffmpeg-master-latest-win64-gpl",
    [string]$ToolsDir = (Join-Path $PSScriptRoot "..\tools")
)

$ErrorActionPreference = "Stop"
$ToolsDir = [IO.Path]::GetFullPath($ToolsDir)
$Downloads = Join-Path $ToolsDir "downloads"
New-Item -ItemType Directory -Force -Path $ToolsDir, $Downloads | Out-Null

function Get-Remote([string]$Url, [string]$OutFile) {
    & curl.exe -fL --retry 3 --retry-delay 5 -C - -o $OutFile $Url
    if ($LASTEXITCODE -ne 0) { throw "download failed (curl exit $LASTEXITCODE): $Url" }
}

function Assert-Sha256([string]$File, [string]$Expected) {
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $File).Hash.ToLower()
    if ($actual -ne $Expected.ToLower()) {
        throw "sha256 mismatch for $File (expected $Expected, got $actual)"
    }
}

function Get-ExpectedHash([string[]]$Lines, [string]$FileName) {
    foreach ($line in $Lines) {
        $parts = $line.Trim() -split '\s+'
        if ($parts.Count -ge 2 -and $parts[-1].TrimStart('*') -eq $FileName) { return $parts[0] }
    }
    return $null
}

# --- Blender -----------------------------------------------------------------
$minor = ($BlenderVersion -split '\.')[0..1] -join '.'
$blenderName = "blender-$BlenderVersion-windows-x64"
$blenderExe = Join-Path $ToolsDir "$blenderName\blender.exe"
if (Test-Path $blenderExe) {
    Write-Host "Blender already present: $blenderExe"
} else {
    $sums = & curl.exe -fsL "https://download.blender.org/release/Blender$minor/blender-$BlenderVersion.sha256"
    $expected = Get-ExpectedHash $sums "$blenderName.zip"
    if (-not $expected) { throw "official checksum for $blenderName.zip not found on blender.org" }
    $zip = Join-Path $Downloads "$blenderName.zip"
    $ok = $false
    foreach ($mirror in $BlenderMirrors) {
        try {
            Write-Host "Downloading Blender from $mirror"
            Get-Remote "$mirror/Blender$minor/$blenderName.zip" $zip
            Assert-Sha256 $zip $expected
            $ok = $true
            break
        } catch {
            Write-Warning $_
            if (Test-Path $zip) { Remove-Item -Force $zip }
        }
    }
    if (-not $ok) { throw "Blender download failed from every mirror" }
    & tar.exe -xf $zip -C $ToolsDir
    if ($LASTEXITCODE -ne 0) { throw "failed to extract $zip" }
}

# --- FFmpeg ------------------------------------------------------------------
$ffmpegDir = Join-Path $ToolsDir "ffmpeg"
if (Test-Path (Join-Path $ffmpegDir "bin\ffmpeg.exe")) {
    Write-Host "FFmpeg already present: $ffmpegDir"
} else {
    $base = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download"
    $zip = Join-Path $Downloads "$FfmpegAsset.zip"
    $sums = & curl.exe -fsL "$base/checksums.sha256"
    $expected = Get-ExpectedHash $sums "$FfmpegAsset.zip"
    Write-Host "Downloading FFmpeg from $base"
    Get-Remote "$base/$FfmpegAsset.zip" $zip
    if ($expected) { Assert-Sha256 $zip $expected } else { Write-Warning "no published checksum for $FfmpegAsset.zip" }
    & tar.exe -xf $zip -C $ToolsDir
    if ($LASTEXITCODE -ne 0) { throw "failed to extract $zip" }
    if (Test-Path $ffmpegDir) { Remove-Item -Recurse -Force $ffmpegDir }
    Move-Item (Join-Path $ToolsDir $FfmpegAsset) $ffmpegDir
}

# --- YuNet face detector (OpenCV Zoo, Apache-2.0) for wbs.privacy -------------
$modelDir = Join-Path $ToolsDir "models"
$yunet = Join-Path $modelDir "face_detection_yunet_2023mar.onnx"
$yunetSha = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
if (-not (Test-Path $yunet)) {
    New-Item -ItemType Directory -Force -Path $modelDir | Out-Null
    Write-Host "Downloading YuNet face detector from OpenCV Zoo"
    Get-Remote "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx" $yunet
}
Assert-Sha256 $yunet $yunetSha

$info = [ordered]@{
    blender         = $blenderExe
    blender_version = $BlenderVersion
    ffmpeg          = (Join-Path $ffmpegDir "bin\ffmpeg.exe")
    ffprobe         = (Join-Path $ffmpegDir "bin\ffprobe.exe")
    face_model      = $yunet
    fetched_at      = (Get-Date).ToString("s")
}
$json = $info | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $ToolsDir "tools.json"), $json, (New-Object Text.UTF8Encoding $false))
Write-Host "tools.json written:"
Write-Host $json
