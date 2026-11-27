param([Parameter(Mandatory=$true)][string]$SourcePath,
      [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
      [string]$RunningInstallation)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
function Require([bool]$value,[string]$reason){if(-not $value){throw $reason}}
Require (-not(Test-Path -LiteralPath $EvidenceDirectory)) 'Use a new evidence directory'
New-Item -ItemType Directory -Path $EvidenceDirectory|Out-Null
$results=[Collections.Generic.List[object]]::new()
foreach($languageCase in @('zh-CN','en')){
    $fixtureRoot=Join-Path $EvidenceDirectory ('资料 & context-'+$languageCase)
    New-Item -ItemType Directory -Path (Join-Path $fixtureRoot 'runtime\python'),(Join-Path $fixtureRoot 'scripts')|Out-Null
    [IO.File]::WriteAllText((Join-Path $fixtureRoot 'runtime\python\python.exe'),'SYNTHETIC NONEXECUTABLE MARKER')
    [IO.File]::WriteAllText((Join-Path $fixtureRoot 'scripts\native_user_state.py'),'SYNTHETIC NONEXECUTABLE MARKER')
    . $SourcePath -Installation $fixtureRoot -Language $languageCase
    $script:folderCalls=0
    function Select-Folder([string]$description,[bool]$create){$script:folderCalls++;throw 'Unavailable action reached the picker'}
    try {
        $form.Show();[Windows.Forms.Application]::DoEvents()
        Require ($script:dataPresence -eq 'FRESH' -and -not $backup.Enabled -and $restore.Enabled -and $verify.Enabled) 'Fresh target action availability is wrong'
        Require ($form.AcceptButton -eq $restore -and $form.ActiveControl -eq $restore) 'Fresh target should focus restore'
        Require (-not $openApp.Visible) 'App handoff must not appear before a successful data operation'
        Open-CurrentApp
        Require (-not $form.IsDisposed) 'Unavailable app handoff changed the window'
        Start-Maintenance 'backup'
        Require ($script:folderCalls -eq 0 -and -not $script:job) 'Empty backup opened a picker or started work'
        Require ($status.AccessibilityObject.Name -ceq $status.Text) 'Status content is hidden by a static accessibility name'
        $results.Add(@{language=$languageCase;state='FRESH';primary=$form.AcceptButton.Text;picker_calls=0;child_started=$false})

        [void][IO.Directory]::CreateDirectory((Join-Path $fixtureRoot 'user-data'))
        Update-Context
        Require ($script:dataPresence -eq 'PRESENT' -and $backup.Enabled -and -not $restore.Enabled -and $form.AcceptButton -eq $backup) 'Existing data should offer backup and refuse restore'
        Start-Maintenance 'restore'
        Require ($script:folderCalls -eq 0 -and -not $script:job) 'Existing data reached restore picker'
        $results.Add(@{language=$languageCase;state='PRESENT';primary=$form.AcceptButton.Text;picker_calls=0;child_started=$false})

        # Only the presence detector is substituted for this state fixture.
        # The real detector is independently exercised below on the running App.
        $realDetector=(Get-Item Function:\Get-InstallationRunning).ScriptBlock
        Set-Item -Path Function:\Get-InstallationRunning -Value {return $true}
        try {
            Update-Context
            Require (-not $backup.Enabled -and -not $restore.Enabled -and $verify.Enabled -and $form.AcceptButton -eq $verify) 'Running installation still offers a data operation'
            Start-Maintenance 'backup';Start-Maintenance 'restore'
            Require ($script:folderCalls -eq 0 -and -not $script:job) 'Running installation reached a data operation'
            $results.Add(@{language=$languageCase;state='RUNNING_DETECTOR_FIXTURE';primary=$form.AcceptButton.Text;picker_calls=0;child_started=$false})
        } finally {Set-Item -Path Function:\Get-InstallationRunning -Value $realDetector}

        [IO.Directory]::Move((Join-Path $fixtureRoot 'user-data'),(Join-Path $fixtureRoot 'held-empty-data-directory'))
        [IO.File]::WriteAllText((Join-Path $fixtureRoot 'user-data'),'SYNTHETIC blocked location')
        Update-Context
        Require ($script:dataPresence -eq 'UNAVAILABLE' -and -not $backup.Enabled -and -not $restore.Enabled -and $verify.Enabled -and $form.AcceptButton -eq $verify) 'Invalid data location offers a write action'
        Start-Maintenance 'backup';Start-Maintenance 'restore'
        Require ($script:folderCalls -eq 0 -and -not $script:job) 'Invalid data location reached a picker'
        Require ([IO.File]::ReadAllText((Join-Path $fixtureRoot 'user-data')) -ceq 'SYNTHETIC blocked location') 'Invalid location was overwritten'
        Require ($status.AccessibilityObject.Name -ceq $status.Text) 'Error status content is not accessible'
        $results.Add(@{language=$languageCase;state='UNAVAILABLE';primary=$form.AcceptButton.Text;picker_calls=0;original_marker_preserved=$true})

        # Re-evaluate the same live form after an external location change.
        [IO.File]::Move((Join-Path $fixtureRoot 'user-data'),(Join-Path $fixtureRoot 'held-data-location-marker'))
        Update-Context
        Require ($script:dataPresence -eq 'FRESH' -and $restore.Enabled -and -not $backup.Enabled -and $form.AcceptButton -eq $restore) 'Same form did not return to fresh state'
        $results.Add(@{language=$languageCase;state='SAME_FORM_FRESH_AGAIN';primary=$form.AcceptButton.Text})

        # A restored directory must switch the same form to its existing-data
        # state. The full actual core roundtrip is qualified separately.
        [void][IO.Directory]::CreateDirectory((Join-Path $fixtureRoot 'user-data'))
        Update-Context
        Require ($backup.Enabled -and -not $restore.Enabled -and $form.AcceptButton -eq $backup) 'Newly restored directory did not change available actions'
        $results.Add(@{language=$languageCase;state='SAME_FORM_DATA_PRESENT_AGAIN';primary=$form.AcceptButton.Text})
        foreach($label in @($intro,$status,$detail)){
            $measure=[Windows.Forms.TextRenderer]::MeasureText($label.Text,$label.Font,[Drawing.Size]::new($label.ClientSize.Width-$label.Padding.Horizontal,1000),[Windows.Forms.TextFormatFlags]::WordBreak)
            Require ($measure.Height -le ($label.ClientSize.Height-$label.Padding.Vertical)) 'Visible context or status text is clipped'
        }
    } finally {$form.Close();$form.Dispose();$timer.Dispose()}
}
$runningDetectorResult=$null
if($RunningInstallation){
    $root=[IO.Path]::GetFullPath($RunningInstallation)
    $runningDetectorResult=Get-InstallationRunning
    Require $runningDetectorResult 'Real running installation not detected'
}
@{status='PASS_NATIVE_CONTEXT_ACTIONS_SAME_FORM_GUARDS_AND_ACCESSIBLE_STATUS';source_sha256=(Get-FileHash -LiteralPath $SourcePath -Algorithm SHA256).Hash.ToLowerInvariant();states=$results.ToArray();actual_running_installation_detected=$runningDetectorResult;folder_picker_substituted=$true;models=0;actual_backup_restore='SEPARATE_NATIVE_CORE_AND_GUI_EVIDENCE';first_stage_complete=$false}|ConvertTo-Json -Depth 6|Set-Content -LiteralPath (Join-Path $EvidenceDirectory 'CONTEXT_RECEIPT.r1.json') -Encoding UTF8
Write-Output 'Native contextual actions verified in two languages; no source data or model work'
