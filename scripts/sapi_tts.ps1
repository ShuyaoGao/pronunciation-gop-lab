# Windows SAPI text-to-speech: read a UTF-8 text file, synthesize English speech to a WAV.
# (Comments kept ASCII-only: Windows PowerShell 5.1 reads .ps1 as ANSI/GBK and would
#  mangle non-ASCII comments, causing a parser error.)
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File sapi_tts.ps1 -TextFile in.txt -OutFile out.wav
param(
    [Parameter(Mandatory = $true)][string]$TextFile,
    [Parameter(Mandatory = $true)][string]$OutFile,
    [string]$VoiceMatch = "Zira"
)

Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer

# Pick an English voice: prefer one whose name matches VoiceMatch, else any en-* voice.
$voices = $synth.GetInstalledVoices() | Where-Object { $_.Enabled }
$picked = $null
foreach ($v in $voices) { if ($v.VoiceInfo.Name -match $VoiceMatch) { $picked = $v.VoiceInfo.Name; break } }
if (-not $picked) {
    foreach ($v in $voices) { if ($v.VoiceInfo.Culture.Name -like "en*") { $picked = $v.VoiceInfo.Name; break } }
}
if ($picked) { $synth.SelectVoice($picked) }

$text = Get-Content -Path $TextFile -Raw -Encoding UTF8
$synth.SetOutputToWaveFile($OutFile)
$synth.Speak($text)
$synth.Dispose()
Write-Output ("OK " + $picked)
