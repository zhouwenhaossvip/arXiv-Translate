param(
    [string]$OutputDir = (Join-Path $PSScriptRoot "generated")
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

function New-RoundedPath([single]$X, [single]$Y, [single]$Width, [single]$Height, [single]$Radius) {
    $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $diameter = $Radius * 2
    $path.AddArc($X, $Y, $diameter, $diameter, 180, 90)
    $path.AddArc($X + $Width - $diameter, $Y, $diameter, $diameter, 270, 90)
    $path.AddArc($X + $Width - $diameter, $Y + $Height - $diameter, $diameter, $diameter, 0, 90)
    $path.AddArc($X, $Y + $Height - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    return $path
}

function Fill-RoundedRect($Graphics, $Brush, [single]$X, [single]$Y, [single]$Width, [single]$Height, [single]$Radius) {
    $path = New-RoundedPath $X $Y $Width $Height $Radius
    try { $Graphics.FillPath($Brush, $path) } finally { $path.Dispose() }
}

function New-Graphics($Bitmap) {
    $graphics = [System.Drawing.Graphics]::FromImage($Bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    return $graphics
}

function Draw-Mark($Graphics, [single]$X, [single]$Y, [single]$Size, [bool]$WithBackground) {
    if ($WithBackground) {
        $blue = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(48, 92, 234))
        try { Fill-RoundedRect $Graphics $blue $X $Y $Size $Size ($Size * 0.25) } finally { $blue.Dispose() }
    }
    $white = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)
    $pale = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(194, 219, 255))
    $cyan = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(111, 229, 237))
    try {
        Fill-RoundedRect $Graphics $pale ($X + $Size * 0.31) ($Y + $Size * 0.19) ($Size * 0.43) ($Size * 0.52) ($Size * 0.055)
        Fill-RoundedRect $Graphics $white ($X + $Size * 0.23) ($Y + $Size * 0.27) ($Size * 0.43) ($Size * 0.52) ($Size * 0.055)
        $Graphics.FillRectangle($cyan, $X + $Size * 0.31, $Y + $Size * 0.43, $Size * 0.25, [Math]::Max(1, $Size * 0.055))
        $Graphics.FillRectangle($cyan, $X + $Size * 0.31, $Y + $Size * 0.55, $Size * 0.18, [Math]::Max(1, $Size * 0.055))
    } finally {
        $white.Dispose()
        $pale.Dispose()
        $cyan.Dispose()
    }
}

function New-MarkBitmap([int]$Size) {
    $bitmap = [System.Drawing.Bitmap]::new($Size, $Size)
    $graphics = New-Graphics $bitmap
    try {
        $graphics.Clear([System.Drawing.Color]::Transparent)
        Draw-Mark $graphics 0 0 $Size $true
    } finally { $graphics.Dispose() }
    return $bitmap
}

function New-WizardImage {
    # 164:314 matches Inno Setup's large-image area at every DPI scale.
    $bitmap = [System.Drawing.Bitmap]::new(656, 1256)
    $graphics = New-Graphics $bitmap
    try {
        $bounds = [System.Drawing.Rectangle]::new(0, 0, 656, 1256)
        $gradient = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
            $bounds,
            [System.Drawing.Color]::FromArgb(16, 34, 78),
            [System.Drawing.Color]::FromArgb(43, 103, 232),
            62.0
        )
        try { $graphics.FillRectangle($gradient, $bounds) } finally { $gradient.Dispose() }

        $glow = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(16, 164, 212, 255))
        $grid = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(25, 214, 231, 255))
        try {
            $graphics.FillEllipse($glow, -190, 350, 810, 810)
            $graphics.FillEllipse($glow, 390, -210, 520, 520)
            for ($x = 68; $x -lt 630; $x += 48) {
                for ($y = 430; $y -lt 1140; $y += 48) {
                    $graphics.FillEllipse($grid, $x, $y, 4, 4)
                }
            }
        } finally {
            $glow.Dispose()
            $grid.Dispose()
        }

        $logoBack = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(52, 255, 255, 255))
        $white = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)
        $muted = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(211, 226, 255))
        $cardBack = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(54, 119, 226))
        $paper = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(246, 249, 255))
        $ink = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(48, 96, 210))
        $accent = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(112, 213, 238))
        $titleFont = [System.Drawing.Font]::new("Segoe UI", 72, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
        $captionFont = [System.Drawing.Font]::new("Segoe UI", 32, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
        $labelFont = [System.Drawing.Font]::new("Segoe UI", 27, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
        try {
            Fill-RoundedRect $graphics $logoBack 72 88 116 116 31
            Draw-Mark $graphics 72 88 116 $false
            $graphics.DrawString("arXiv", $titleFont, $white, 70, 244)
            $graphics.DrawString("Translate", $titleFont, $white, 70, 324)
            $graphics.DrawString("Research, in your language.", $captionFont, $muted, 74, 422)

            # A document enters on the left and a translated page comes forward.
            Fill-RoundedRect $graphics $cardBack 58 555 540 514 44
            Fill-RoundedRect $graphics $paper 115 612 354 390 24
            Fill-RoundedRect $graphics $white 181 663 354 390 24
            $graphics.FillRectangle($ink, 225, 710, 180, 13)
            $graphics.FillRectangle($accent, 225, 762, 260, 13)
            $graphics.FillRectangle($accent, 225, 810, 225, 13)
            $graphics.FillRectangle($accent, 225, 858, 253, 13)
            $graphics.FillRectangle($ink, 225, 923, 120, 13)
            Fill-RoundedRect $graphics $ink 387 913 104 102 23
            $glyphFont = [System.Drawing.Font]::new("Segoe UI", 45, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
            try { $graphics.DrawString("ZH", $glyphFont, $white, 397, 935) } finally { $glyphFont.Dispose() }

            $graphics.DrawString("PDF IN / CHINESE OUT", $labelFont, $white, 76, 1124)
            $graphics.DrawString("Local  /  Private  /  Yours", $captionFont, $muted, 76, 1170)
        } finally {
            foreach ($resource in @($logoBack, $white, $muted, $cardBack, $paper, $ink, $accent, $titleFont, $captionFont, $labelFont)) {
                $resource.Dispose()
            }
        }
    } finally { $graphics.Dispose() }
    return $bitmap
}

$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
[System.IO.Directory]::CreateDirectory($OutputDir) | Out-Null
$large = New-WizardImage
$small = New-MarkBitmap 256
try {
    $large.Save((Join-Path $OutputDir "wizard-large.png"), [System.Drawing.Imaging.ImageFormat]::Png)
    $small.Save((Join-Path $OutputDir "wizard-small.png"), [System.Drawing.Imaging.ImageFormat]::Png)
} finally {
    $large.Dispose()
    $small.Dispose()
}

# An ICO container with PNG frames stays sharp in Setup, Start, and the taskbar.
$frames = @()
foreach ($size in @(16, 32, 48, 64, 256)) {
    $bitmap = New-MarkBitmap $size
    $stream = [System.IO.MemoryStream]::new()
    try {
        $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
        $frames += ,@($size, $stream.ToArray())
    } finally {
        $bitmap.Dispose()
        $stream.Dispose()
    }
}
$iconFile = [System.IO.File]::Create((Join-Path $OutputDir "app.ico"))
$writer = [System.IO.BinaryWriter]::new($iconFile)
try {
    $writer.Write([uint16]0)
    $writer.Write([uint16]1)
    $writer.Write([uint16]$frames.Count)
    $offset = 6 + 16 * $frames.Count
    foreach ($frame in $frames) {
        $size = [int]$frame[0]
        $bytes = [byte[]]$frame[1]
        $writer.Write([byte]($size % 256))
        $writer.Write([byte]($size % 256))
        $writer.Write([byte]0)
        $writer.Write([byte]0)
        $writer.Write([uint16]1)
        $writer.Write([uint16]32)
        $writer.Write([uint32]$bytes.Length)
        $writer.Write([uint32]$offset)
        $offset += $bytes.Length
    }
    foreach ($frame in $frames) { $writer.Write([byte[]]$frame[1]) }
} finally { $writer.Dispose() }

Write-Host "Installer artwork generated in $OutputDir"
