# Offline Windows speech fixtures; generated audio is committed for cross-platform replay.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$root = Split-Path $PSScriptRoot -Parent
$raw = Join-Path $root 'work/raw_audio'
New-Item -ItemType Directory -Force -Path $raw | Out-Null
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
try {
    $scenarios = Get-Content (Join-Path $root 'scenarios/scripts.json') -Raw | ConvertFrom-Json
    foreach ($scenario in $scenarios) {
        $synth.Rate = $scenario.rate
        for ($i = 0; $i -lt $scenario.parts.Count; $i++) {
            $synth.SetOutputToWaveFile((Join-Path $raw "$($scenario.id)-$i.wav"), $format)
            $synth.Speak($scenario.parts[$i])
        }
    }
    $synth.Rate = 0
    $synth.SetOutputToWaveFile((Join-Path $raw 'ack.wav'), $format)
    $synth.Speak('Mm hmm')
} finally { $synth.Dispose() }
Write-Output 'Raw fixtures generated. Run python scripts/prepare_audio.py next.'
