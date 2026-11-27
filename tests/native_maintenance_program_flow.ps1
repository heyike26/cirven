param([Parameter(Mandatory=$true)][string]$SourcePath,
      [Parameter(Mandatory=$true)][string]$EvidenceDirectory)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
function Require([bool]$value,[string]$reason){if(-not $value){throw $reason}}
Require (-not(Test-Path -LiteralPath $EvidenceDirectory)) 'Use fresh evidence'
[void][IO.Directory]::CreateDirectory($EvidenceDirectory)
$results=[Collections.Generic.List[object]]::new()
foreach($languageCase in @('zh-CN','en')){
    $fixture=Join-Path $EvidenceDirectory ('flow-'+$languageCase)
    $current=Join-Path $fixture 'current & 原安装'
    $target=Join-Path $fixture 'retained & 目标程序'
    $snapshot=Join-Path $fixture 'backup 原快照'
    foreach($installation in @($current,$target)){
        foreach($name in @('runtime\python\python.exe','runtime\python\pythonw.exe','scripts\native_user_state.py','scripts\native_package_bootstrap.py')){
            $path=Join-Path $installation $name;[void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($path))
            [IO.File]::WriteAllText($path,'SYNTHETIC NONEXECUTABLE BOUNDARY MARKER')
        }
        [IO.File]::WriteAllText((Join-Path $installation 'PACKAGE_MANIFEST.json'),'{"fixture":"structural-ui-boundary-only"}')
    }
    [void][IO.Directory]::CreateDirectory($snapshot)
    [void][IO.Directory]::CreateDirectory((Join-Path $current 'user-data'))
    $originalMarker=Join-Path $current 'user-data\original.txt'
    [IO.File]::WriteAllText($originalMarker,'原安装的有效手稿与未知操作不修改。')
    $originalSha=(Get-FileHash -LiteralPath $originalMarker -Algorithm SHA256).Hash
    . $SourcePath -Installation $current -Language $languageCase
    $timer.Interval=60000 # Controlled scheduling; production callback invoked below.
    $script:uiTarget=$target;$script:uiSnapshot=$snapshot
    $script:spawned=[Collections.Generic.List[object]]::new()
    $script:responses=[Collections.Generic.Queue[object]]::new()
    $script:folders=[Collections.Generic.Queue[string]]::new()
    $script:folderCalls=0;$script:confirmationCalls=0;$script:confirmAnswer=$false
    $script:runningTarget=$false
    function Select-Folder([string]$description,[bool]$create){
        $script:folderCalls++
        Require (-not $create) 'Program recovery must select existing inputs'
        Require ($script:folders.Count -gt 0) 'Unexpected extra folder prompt'
        return $script:folders.Dequeue()
    }
    function Get-InstallationRunning([string]$installation=$root){
        return $script:runningTarget -and (Same-Location $installation $script:uiTarget)
    }
    function Confirm-Restore([string]$folder,[string]$installation){
        Require (Same-Location $folder $script:uiSnapshot) 'Confirmation changed backup'
        Require (Same-Location $installation $script:uiTarget) 'Confirmation changed target'
        $script:confirmationCalls++
        return $script:confirmAnswer
    }
    function New-Reader([string]$value){
        $reader=[pscustomobject]@{value=$value}
        $reader|Add-Member ScriptMethod ReadToEndAsync {
            $completion=[Threading.Tasks.TaskCompletionSource[string]]::new()
            $completion.SetResult($this.value)
            return $completion.Task
        }
        return $reader
    }
    function Start-NativeProcess([Diagnostics.ProcessStartInfo]$info){
        $script:spawned.Add(@{exe=$info.FileName;arguments=$info.Arguments;cwd=$info.WorkingDirectory;shell=$info.UseShellExecute})
        if($info.Arguments -match ' --electron '){
            $job=[pscustomobject]@{disposed=$false};$job|Add-Member ScriptMethod Dispose {$this.disposed=$true};return $job
        }
        Require ($script:responses.Count -gt 0) 'Unexpected extra maintenance child'
        $response=$script:responses.Dequeue()
        if($response.publish){
            $data=Join-Path $script:uiTarget 'user-data';[void][IO.Directory]::CreateDirectory($data)
            [IO.File]::WriteAllText((Join-Path $data 'controlled-worker.txt'),'CONTROLLED PROCESS RESULT ONLY; actual recovery qualified separately')
        }
        $output=if($null -eq $response.result){''}else{$response.result|ConvertTo-Json -Depth 8 -Compress}
        $job=[pscustomobject]@{HasExited=$true;ExitCode=$response.code;StandardOutput=(New-Reader $output);StandardError=(New-Reader $response.error);disposed=$false}
        $job|Add-Member ScriptMethod Dispose {$this.disposed=$true}
        return $job
    }
    function Preflight {
        return @{status='RESTORE_TARGET_COMPATIBLE';path=$script:uiTarget;manifest_sha256=('b'*64);
          program_manifest_sha256=(Get-FileHash -LiteralPath (Join-Path $script:uiTarget 'PACKAGE_MANIFEST.json') -Algorithm SHA256).Hash.ToLowerInvariant();
          user_data_written=$false;model_or_agent_started=$false}
    }
    function Add-Response($value,[int]$code=0,[string]$errorText='',[bool]$publish=$false){
        $script:responses.Enqueue(@{result=$value;code=$code;error=$errorText;publish=$publish})
    }
    function Select-Program {
        $script:folders.Enqueue($script:uiTarget);$script:folders.Enqueue($script:uiSnapshot)
        Start-Maintenance 'program'
    }
    function Reset-Scenario {
        Require ($null -eq $script:job) 'Previous worker not completed'
        $timer.Stop();$script:responses.Clear();$script:folders.Clear();$script:spawned.Clear()
        $script:folderCalls=0;$script:confirmationCalls=0;$script:confirmAnswer=$false;$script:runningTarget=$false
        $script:canOpenApp=$false;$script:openManifestSha=$null;Update-Context
    }
    function Save-Frame([string]$name){
        [Windows.Forms.Application]::DoEvents()
        Require ($form.Visible -and -not $form.IsDisposed -and $form.Handle -ne [IntPtr]::Zero) 'Native form is not visible'
        $bitmap=[Drawing.Bitmap]::new($form.Width,$form.Height)
        try {$form.DrawToBitmap($bitmap,[Drawing.Rectangle]::new(0,0,$form.Width,$form.Height));$bitmap.Save((Join-Path $fixture ($name+'.png')),[Drawing.Imaging.ImageFormat]::Png)}finally{$bitmap.Dispose()}
        return @{pid=$PID;birth=[Diagnostics.Process]::GetCurrentProcess().StartTime.ToUniversalTime().ToString('o');hwnd=$form.Handle.ToInt64();visible=$form.Visible;minimized=$form.WindowState -eq 'Minimized';client_width=$form.ClientSize.Width;client_height=$form.ClientSize.Height;capture='OWN_FORM_DrawToBitmap_NOT_FOREGROUND_SCREEN_PROOF'}
    }
    try {
        $form.Show();[Windows.Forms.Application]::DoEvents()
        Require ($backup.Enabled -and -not $restore.Enabled -and $program.Enabled) 'Version action must coexist with preserved current data'
        $identity=Save-Frame 'initial'

        # Cancellation happens after the real completion callback validates a
        # readonly target receipt. No restore child or App is authorized.
        Add-Response (Preflight);Select-Program
        Require ($script:spawned.Count -eq 1 -and $script:spawned[0].arguments -match ' check-target ') 'Must preflight before confirmation'
        $count=$script:folderCalls;Start-Maintenance 'program'
        Require ($script:folderCalls -eq $count -and $script:spawned.Count -eq 1) 'Busy action duplicated work'
        Update-MaintenanceJob
        Require ($script:confirmationCalls -eq 1 -and $script:spawned.Count -eq 1 -and -not $script:job) 'Cancelled check started restore'
        Require (-not(Test-Path -LiteralPath (Join-Path $target 'user-data')) -and -not $openApp.Visible) 'Cancelled check wrote target data or offered launch'
        $results.Add(@{language=$languageCase;case='CANCEL_AFTER_PREFLIGHT';children=1;restore_children=0;confirmations=1;target_data_written=$false})

        foreach($damage in @('outside-target','claims-write','invalid-backup-sha','wrong-program-sha')){
            Reset-Scenario;$report=Preflight
            switch($damage){
                'outside-target' {$report.path=$current}
                'claims-write' {$report.user_data_written=$true}
                'invalid-backup-sha' {$report.manifest_sha256='invalid'}
                'wrong-program-sha' {$report.program_manifest_sha256='c'*64}
            }
            Add-Response $report;Select-Program;Update-MaintenanceJob
            Require ($script:confirmationCalls -eq 0 -and $script:spawned.Count -eq 1 -and -not $script:canOpenApp) ('Invalid preflight admitted: '+$damage)
            Require (-not(Test-Path -LiteralPath (Join-Path $target 'user-data'))) 'Rejected preflight published target'
            $results.Add(@{language=$languageCase;case=$damage;children=1;confirmations=0;target_data_written=$false})
        }
        Reset-Scenario
        Add-Response $null 1 'Target program has no supported state protocol declaration'
        Select-Program;Update-MaintenanceJob
        Require ($script:confirmationCalls -eq 0 -and $script:spawned.Count -eq 1 -and $copy.Visible) 'Incompatible state reached confirmation or replay'
        $failureIdentity=Save-Frame 'incompatible-state'
        $results.Add(@{language=$languageCase;case='UNSUPPORTED_STATE';children=1;confirmations=0;identity=$failureIdentity})

        Reset-Scenario;$script:runningTarget=$true;$script:folders.Enqueue($target)
        Start-Maintenance 'program'
        Require ($script:folderCalls -eq 1 -and $script:spawned.Count -eq 0) 'Running target reached backup picker or worker'
        $results.Add(@{language=$languageCase;case='RUNNING_TARGET';children=0;folder_prompts=1})
        Reset-Scenario;[void][IO.Directory]::CreateDirectory((Join-Path $target 'user-data'));$script:folders.Enqueue($target)
        Start-Maintenance 'program'
        Require ($script:folderCalls -eq 1 -and $script:spawned.Count -eq 0) 'Existing target data reached worker'
        [IO.Directory]::Move((Join-Path $target 'user-data'),(Join-Path $fixture 'held-existing-target-data'))
        $results.Add(@{language=$languageCase;case='EXISTING_TARGET_DATA';children=0;folder_prompts=1})

        # Mismatched successful restore receipts cannot enable App launch.
        foreach($damage in @('wrong-backup','wrong-program','outside-data')){
            Reset-Scenario;$script:confirmAnswer=$true;$report=Preflight
            $restored=@{status='RESTORE_COMPLETE';path=(Join-Path $target 'user-data');manifest_sha256=$report.manifest_sha256;program_manifest_sha256=$report.program_manifest_sha256;model_or_agent_started=$false}
            switch($damage){'wrong-backup'{$restored.manifest_sha256='d'*64};'wrong-program'{$restored.program_manifest_sha256='e'*64};'outside-data'{$restored.path=Join-Path $current 'user-data'}}
            Add-Response $report;Add-Response $restored
            Select-Program;Update-MaintenanceJob
            Require ($script:spawned.Count -eq 2) 'Confirmed check did not start one restore'
            Update-MaintenanceJob
            Require (-not $script:canOpenApp -and -not $openApp.Visible -and $script:spawned.Count -eq 2) 'Mismatched restore enabled launch or replay'
            $results.Add(@{language=$languageCase;case=$damage;children=2;app_started=$false})
        }

        # A changed selected program also blocks the explicit App handoff.
        Reset-Scenario;$script:confirmAnswer=$true;$report=Preflight
        $restored=@{status='RESTORE_COMPLETE';path=(Join-Path $target 'user-data');manifest_sha256=$report.manifest_sha256;program_manifest_sha256=$report.program_manifest_sha256;model_or_agent_started=$false}
        Add-Response $report;Add-Response $restored 0 '' $true
        Select-Program;Update-MaintenanceJob;Update-MaintenanceJob
        Require ($script:canOpenApp -and $openApp.Visible) 'Verified restored target did not offer explicit launch'
        $originalProgram=[IO.File]::ReadAllBytes((Join-Path $target 'PACKAGE_MANIFEST.json'))
        [IO.File]::WriteAllText((Join-Path $target 'PACKAGE_MANIFEST.json'),'{"changed_after_restore":true}')
        Open-CurrentApp
        Require ($script:spawned.Count -eq 2 -and -not $script:canOpenApp -and -not $form.IsDisposed) 'Changed program reached launch or destroyed maintenance'
        [IO.File]::WriteAllBytes((Join-Path $target 'PACKAGE_MANIFEST.json'),$originalProgram)
        [IO.Directory]::Move((Join-Path $target 'user-data'),(Join-Path $fixture 'held-prelaunch-data'))
        $results.Add(@{language=$languageCase;case='CHANGED_BEFORE_OPEN';children=2;app_started=$false})

        # Final happy path keeps both version and backup identity across the
        # confirmation; the executable is a controlled non-executed boundary.
        Reset-Scenario;$script:confirmAnswer=$true;$report=Preflight
        $restored=@{status='RESTORE_COMPLETE';path=(Join-Path $target 'user-data');manifest_sha256=$report.manifest_sha256;program_manifest_sha256=$report.program_manifest_sha256;model_or_agent_started=$false}
        Add-Response $report;Add-Response $restored 0 '' $true
        Select-Program;Update-MaintenanceJob
        $restoreArgs=$script:spawned[1].arguments
        Require ($restoreArgs.Contains(' --expected-sha256 '+$report.manifest_sha256) -and $restoreArgs.Contains(' --expected-program-sha256 '+$report.program_manifest_sha256)) 'Confirmed restore lost exact identities'
        Update-MaintenanceJob
        Require (Same-Location $script:openInstallation $target) 'Successful other-version restore points to original installation'
        Require ($script:canOpenApp -and $form.AcceptButton -eq $openApp) 'Target launch is not the explicit next action'
        Require ($script:confirmationCalls -eq 1 -and $script:folderCalls -eq 2) 'Normal path duplicated choice or confirmation'
        $doneIdentity=Save-Frame 'ready-to-open-target'
        foreach($label in @($intro,$status,$detail)){
            $measure=[Windows.Forms.TextRenderer]::MeasureText($label.Text,$label.Font,[Drawing.Size]::new($label.ClientSize.Width-$label.Padding.Horizontal,1000),[Windows.Forms.TextFormatFlags]::WordBreak)
            Require ($measure.Height -le ($label.ClientSize.Height-$label.Padding.Vertical)) 'Native context or result text clipped'
        }
        foreach($button in @($backup,$verify,$restore,$program,$openApp,$close)){
            $measure=[Windows.Forms.TextRenderer]::MeasureText($button.Text,$button.Font)
            Require ($measure.Width -le $button.ClientSize.Width) ('Button label clipped: '+$button.Text)
        }
        Require ($status.AccessibilityObject.Name -ceq $status.Text) 'Visible status is not accessible'
        Require ((Get-FileHash -LiteralPath $originalMarker -Algorithm SHA256).Hash -ceq $originalSha) 'Current installation data changed'
        Open-CurrentApp
        Require ($script:spawned.Count -eq 3 -and $form.IsDisposed) 'Explicit target launch did not finish own form'
        $last=$script:spawned[2]
        Require ((Same-Location $last.exe (Join-Path $target 'runtime\python\pythonw.exe')) -and (Same-Location $last.cwd $target)) 'Launch used original root or wrong working directory'
        Require ($last.arguments.Contains((Join-Path $root 'scripts\native_package_bootstrap.py')) -and $last.arguments.Contains(' --selected-installation '+(Quote-Argument $target))) 'Open used legacy argument parser or wrong selected installation'
        Require ($last.arguments.Contains(' --electron --expected-manifest-sha256 '+$report.program_manifest_sha256) -and -not $last.shell) 'Launch lost selected version or used shell'
        $results.Add(@{language=$languageCase;case='COMPLETE_TARGET_HANDOFF';children=3;folder_prompts=2;confirmations=1;current_marker_unchanged=$true;native_UI=$doneIdentity;process_calls=$script:spawned.ToArray()})
    } finally {
        $timer.Stop()
        if(-not $form.IsDisposed){$form.Close();$form.Dispose()}
        $timer.Dispose()
    }
}
@{status='PASS_NATIVE_WINFORMS_PROGRAM_FLOW_CONTROLLED_PROCESS_BOUNDARIES';source_sha256=(Get-FileHash -LiteralPath $SourcePath -Algorithm SHA256).Hash.ToLowerInvariant();cases=$results.ToArray();folder_picker_substituted=$true;process_start_substituted=$true;confirmation_substituted=$true;actual_recovery='SEPARATE_REAL_PACKAGED_CORE_EVIDENCE';actual_App_launch='NOT_EXECUTED';new_D_outputs=0}|ConvertTo-Json -Depth 12|Set-Content -LiteralPath (Join-Path $EvidenceDirectory 'FLOW_RECEIPT.json') -Encoding UTF8
Write-Output 'Native WinForms target recovery flow verified; process/folder/confirmation boundaries controlled'
