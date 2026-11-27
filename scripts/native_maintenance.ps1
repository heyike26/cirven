param([Parameter(Mandatory=$true)][string]$Installation,
      [ValidateSet('zh-CN','en')][string]$Language='zh-CN')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$script:english=$Language -eq 'en'
function T([string]$zh,[string]$en) { if($script:english){$en}else{$zh} }
function Quote-Argument([string]$value) {
    if($value.Contains('"') -or $value.Contains([char]0)){throw 'Invalid Windows path argument'}
    '"'+($value -replace '(\\+)$','$1$1')+'"'
}
$root=[IO.Path]::GetFullPath($Installation)
$python=Join-Path $root 'runtime\python\python.exe'
$tool=Join-Path $root 'scripts\native_user_state.py'
if(-not(Test-Path -LiteralPath $python -PathType Leaf) -or -not(Test-Path -LiteralPath $tool -PathType Leaf)){throw 'Maintenance payload missing'}
$script:job=$null
$script:diagnostic=''
$script:canOpenApp=$false
$script:openInstallation=$root
$script:openManifestSha=$null
$form=[Windows.Forms.Form]::new()
$form.Text=T 'Cirven · 备份与恢复' 'Cirven · Backup and restore'
$form.StartPosition='CenterScreen'
$form.ClientSize=[Drawing.Size]::new(640,494)
$form.MinimumSize=[Drawing.Size]::new(656,533)
$form.Font=[Drawing.Font]::new('Segoe UI',11)
$form.BackColor=[Drawing.Color]::FromArgb(250,250,250)
$form.ForeColor=[Drawing.Color]::FromArgb(63,69,72)
$form.AutoScaleMode='Dpi'
$layout=[Windows.Forms.TableLayoutPanel]::new()
$layout.Dock='Fill';$layout.Padding=[Windows.Forms.Padding]::new(28,20,28,20)
$layout.ColumnCount=1;$layout.RowCount=8
foreach($height in @(62,58,60,14,66,70,0,44)){
    if($height -eq 0){$style=[Windows.Forms.RowStyle]::new('Percent',100)}
    else{$style=[Windows.Forms.RowStyle]::new('Absolute',$height)}
    [void]$layout.RowStyles.Add($style)
}
$form.Controls.Add($layout)
$brand=[Windows.Forms.Label]::new();$brand.Text='Cirven';$brand.Dock='Fill'
$brand.Font=[Drawing.Font]::new('Segoe Script',27);$brand.Margin=[Windows.Forms.Padding]::Empty
$layout.Controls.Add($brand,0,0)
$intro=[Windows.Forms.Label]::new();$intro.Dock='Fill'
$intro.Text=T '备份保留历史、草稿和账号资料。先从托盘正常退出当前安装；恢复请在尚未启动的新副本中进行。' 'Keep history, drafts and account files. Exit this installation from its tray before backing up. Restore into a fresh copy before its first start.'
$layout.Controls.Add($intro,0,1)
$actions=[Windows.Forms.TableLayoutPanel]::new();$actions.Dock='Fill';$actions.ColumnCount=3;$actions.RowCount=1
1..3|ForEach-Object{[void]$actions.ColumnStyles.Add([Windows.Forms.ColumnStyle]::new('Percent',33.333))}
$backup=[Windows.Forms.Button]::new();$backup.Text=T '创建备份' 'Create backup'
$verify=[Windows.Forms.Button]::new();$verify.Text=T '校验备份' 'Check backup'
$restore=[Windows.Forms.Button]::new();$restore.Text=T '恢复备份' 'Restore backup'
$buttons=@($backup,$verify,$restore)
for($i=0;$i -lt 3;$i++){
    $button=$buttons[$i];$button.Dock='Fill';$button.Margin=[Windows.Forms.Padding]::new(0,4,8,8)
    $button.FlatStyle='Flat';$button.FlatAppearance.BorderColor=[Drawing.Color]::FromArgb(213,219,222)
    $button.BackColor=[Drawing.Color]::White;$button.TabIndex=$i
    $actions.Controls.Add($button,$i,0)
}
$backup.BackColor=$form.ForeColor;$backup.ForeColor=[Drawing.Color]::White
$layout.Controls.Add($actions,0,2)
$progress=[Windows.Forms.ProgressBar]::new();$progress.Dock='Fill';$progress.Visible=$false;$progress.Style='Marquee'
$layout.Controls.Add($progress,0,3)
$status=[Windows.Forms.Label]::new();$status.Dock='Fill';$status.Padding=[Windows.Forms.Padding]::new(0,12,0,0)
$status.Text=T '请选择维护操作。备份仅供同一 Windows 用户在本机恢复。' 'Choose an action. Private backups are for this Windows user on this computer.'
$status.AccessibleDescription=T '维护状态' 'Maintenance status';$layout.Controls.Add($status,0,4)
$location=[Windows.Forms.TextBox]::new();$location.ReadOnly=$true;$location.Multiline=$true
$location.ScrollBars='Vertical';$location.Dock='Fill';$location.BorderStyle='None';$location.BackColor=$form.BackColor
$location.Text=(T '当前安装：' 'Installation: ')+$root
$location.AccessibleName=T '当前安装或结果位置' 'Installation or result location'
$layout.Controls.Add($location,0,5)
$detail=[Windows.Forms.Label]::new();$detail.Dock='Fill';$detail.TextAlign='BottomLeft'
$detail.Text=T '备份与恢复本身不会启动模型或重发任务。已有备份和用户数据均不会被覆盖。' 'Backup and restore do not start models or resend tasks. Existing backups and user data are never replaced.'
$layout.Controls.Add($detail,0,6)
$footer=[Windows.Forms.FlowLayoutPanel]::new();$footer.Dock='Fill';$footer.FlowDirection='RightToLeft'
$close=[Windows.Forms.Button]::new();$close.Text=T '关闭' 'Close';$close.Size=[Drawing.Size]::new(92,34);$close.Add_Click({$form.Close()})
$copy=[Windows.Forms.Button]::new();$copy.Text=T '复制诊断' 'Copy details';$copy.Size=[Drawing.Size]::new(132,34);$copy.Visible=$false
$copy.Add_Click({[Windows.Forms.Clipboard]::SetText($script:diagnostic)})
$openApp=[Windows.Forms.Button]::new();$openApp.Text=T '打开 App' 'Open app';$openApp.Size=[Drawing.Size]::new(144,34);$openApp.Visible=$false;$openApp.FlatStyle='Flat'
$openApp.Add_Click({Open-CurrentApp})
$program=[Windows.Forms.Button]::new();$program.Text=T '程序回退' 'Other version';$program.Size=[Drawing.Size]::new(132,34);$program.FlatStyle='Flat'
$program.Add_Click({Start-Maintenance 'program'})
$buttons+= $program
$footer.Controls.Add($close);$footer.Controls.Add($copy);$footer.Controls.Add($openApp);$footer.Controls.Add($program);$layout.Controls.Add($footer,0,7)
$form.CancelButton=$close
$timer=[Windows.Forms.Timer]::new();$timer.Interval=250
function Get-InstallationRunning([string]$installation=$root) {
    $prefix=$installation.TrimEnd([char]92)+[char]92
    foreach($process in @(Get-Process -Name electron,python,pythonw,opencode -ErrorAction SilentlyContinue)){
        try {$path=$process.Path;if($path -and $path.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)){return $true}}
        catch {} finally {$process.Dispose()}
    }
    return $false
}
function Update-Context {
    $data=Join-Path $root 'user-data'
    try {$exists=Test-Path -LiteralPath $data;$directory=Test-Path -LiteralPath $data -PathType Container}
    catch {$exists=$true;$directory=$false}
    $backup.Enabled=$exists -and $directory
    $restore.Enabled=-not $exists
    $verify.Enabled=$true
    $program.Enabled=$true
    $script:installationRunning=Get-InstallationRunning
    foreach($button in @($backup,$verify,$restore,$openApp,$program)){$button.BackColor=[Drawing.Color]::White;$button.ForeColor=$form.ForeColor}
    if(-not $exists){
        $script:dataPresence='FRESH'
        $primary=$restore
        $intro.Text=T '此副本尚无用户数据，可恢复本机已有的备份。首次启动 App 后，也可以在此创建备份。' 'This copy has no user data yet. Restore a local backup, or start the app first to create data to back up.'
    } elseif($directory){
        $script:dataPresence='PRESENT'
        $primary=$backup
        $intro.Text=T '备份前，先从托盘正常退出当前安装。若要恢复，请在尚未启动的新副本中打开维护工具。' 'Exit this installation from its tray before backing up. To restore, open maintenance in a fresh copy.'
    } else {
        $script:dataPresence='UNAVAILABLE'
        $primary=$verify
        $intro.Text=T '用户数据位置不是可用的文件夹。请保留当前目录供检查；仍可校验已有备份。' 'The user-data location is not an available folder. Keep it for investigation. You can still check a backup.'
    }
    if($script:installationRunning){
        $backup.Enabled=$false;$restore.Enabled=$false;$primary=$verify
        $intro.Text=T '当前安装仍在运行。先从托盘正常退出，再返回此窗口备份或恢复；现在仍可校验已有备份。' 'This installation is running. Exit from its tray, then return here to back up or restore. You can still check a backup.'
    }
    if($script:canOpenApp){
        $openData=Join-Path $script:openInstallation 'user-data'
        if(-not(Test-Path -LiteralPath $openData -PathType Container) -or (Get-InstallationRunning $script:openInstallation)){$script:canOpenApp=$false}
    }
    $openApp.Visible=$script:canOpenApp
    if($script:canOpenApp){$primary=$openApp}
    $primary.BackColor=$form.ForeColor;$primary.ForeColor=[Drawing.Color]::White
    $form.AcceptButton=$primary
}
function Open-CurrentApp {
    if($script:job -or -not $script:canOpenApp){return}
    Update-Context
    if(-not $script:canOpenApp){
        $status.Text=T '安装的运行状态或数据位置已改变，请先核对当前安装。' 'The installation state or data location changed. Check this installation first.'
        return
    }
    try {
        $selected=$script:openInstallation
        if(-not $script:openManifestSha -or (Get-FileHash -LiteralPath (Join-Path $selected 'PACKAGE_MANIFEST.json') -Algorithm SHA256).Hash.ToLowerInvariant() -cne $script:openManifestSha){
            throw 'Selected program version changed after the maintenance result'
        }
        # Older installations need the maintenance launcher to enforce the selected manifest.
        $info=[Diagnostics.ProcessStartInfo]::new();$info.FileName=Join-Path $selected 'runtime\python\pythonw.exe'
        $info.Arguments='-B '+(Quote-Argument (Join-Path $root 'scripts\native_package_bootstrap.py'))+' --selected-installation '+(Quote-Argument $selected)+' --electron --expected-manifest-sha256 '+$script:openManifestSha
        $info.WorkingDirectory=$selected;$info.UseShellExecute=$false;$info.CreateNoWindow=$true
        $process=Start-NativeProcess $info;$process.Dispose()
        $script:canOpenApp=$false;$form.Close()
    } catch {
        $script:canOpenApp=$false;$script:openManifestSha=$null;$openApp.Visible=$false
        $status.Text=T '未能打开 App；已备份或恢复的数据保留。可复制诊断供检查。' 'The app could not open. Saved data remains. Copy details for investigation.'
        $script:diagnostic=$_.Exception.Message;$copy.Visible=$true
    }
}
function Select-Folder([string]$description,[bool]$create) {
    $dialog=[Windows.Forms.FolderBrowserDialog]::new()
    try {
        $dialog.Description=$description;$dialog.ShowNewFolderButton=$create
        $dialog.SelectedPath=[IO.Path]::GetPathRoot($root)
        if($dialog.ShowDialog($form) -eq 'OK'){return $dialog.SelectedPath}
        return $null
    } finally {$dialog.Dispose()}
}
function Start-NativeProcess([Diagnostics.ProcessStartInfo]$info) { return [Diagnostics.Process]::Start($info) }
function Same-Location([string]$left,[string]$right) {
    return [IO.Path]::GetFullPath($left).TrimEnd([char]92).Equals([IO.Path]::GetFullPath($right).TrimEnd([char]92),[StringComparison]::OrdinalIgnoreCase)
}
function Confirm-Restore([string]$folder,[string]$target) {
    $answer=[Windows.Forms.MessageBox]::Show($form,
        (T "备份：`n$folder`n`n恢复到：`n$target`n`n兼容检查已通过。当前安装和原备份保留；恢复后由你打开目标 App。" "Backup:`n$folder`n`nRestore to:`n$target`n`nCompatibility checked. Keep the current installation and backup. Open the target app when ready."),
        (T '确认恢复到此程序' 'Restore to this program'),'OKCancel','Question','Button2')
    return $answer -eq 'OK'
}
function Start-MaintenanceJob([string]$action,[string]$installation,[string]$folder,[string]$backupSha='',[string]$programSha='',[string]$flow='') {
    if($script:job){throw 'A maintenance operation is already active'}
    try {
        $info=[Diagnostics.ProcessStartInfo]::new();$info.FileName=$python
        $info.Arguments='-B -Xutf8 '+(Quote-Argument $tool)+' '+$action+' --installation '+(Quote-Argument $installation)+' --backup '+(Quote-Argument $folder)+' --json'
        if($backupSha){
            if($backupSha -cnotmatch '^[a-f0-9]{64}$'){throw 'Invalid checked backup identity'}
            $info.Arguments+=' --expected-sha256 '+$backupSha
        }
        if($programSha){
            if($programSha -cnotmatch '^[a-f0-9]{64}$'){throw 'Invalid checked program identity'}
            $info.Arguments+=' --expected-program-sha256 '+$programSha
        }
        $info.WorkingDirectory=$root;$info.UseShellExecute=$false;$info.CreateNoWindow=$true
        $info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
        $info.StandardOutputEncoding=[Text.UTF8Encoding]::new($false);$info.StandardErrorEncoding=[Text.UTF8Encoding]::new($false)
        $script:job=Start-NativeProcess $info
        $script:request=[pscustomobject]@{action=$action;installation=$installation;folder=$folder;backupSha=$backupSha;programSha=$programSha;flow=$flow}
        $script:canOpenApp=$false;$script:openManifestSha=$null;$openApp.Visible=$false
        $script:outTask=$script:job.StandardOutput.ReadToEndAsync();$script:errTask=$script:job.StandardError.ReadToEndAsync()
        $script:started=[DateTime]::UtcNow
        $buttons|ForEach-Object{$_.Enabled=$false};$copy.Visible=$false;$close.Enabled=$false
        $progress.Visible=$true
        $location.Text=(T '目标程序：' 'Target program: ')+$installation+"`r`n"+(T '备份：' 'Backup: ')+$folder
        $timer.Start()
    } catch {
        $status.Text=T '未能启动维护操作。请检查安装是否完整。' 'Maintenance could not start. Check the installation files.'
        $script:diagnostic=$_.Exception.Message;$copy.Visible=$true
    }
}
function Start-Maintenance([string]$action) {
    if($script:job){return}
    Update-Context
    if($action -in @('backup','restore') -and $script:installationRunning){
        $status.Text=T '当前安装仍在运行。请从托盘正常退出后重试。' 'This installation is running. Exit from its tray, then try again.'
        return
    }
    if($action -eq 'backup' -and $script:dataPresence -ne 'PRESENT'){
        if($script:dataPresence -eq 'FRESH'){$status.Text=T '此副本尚无可备份的数据。可先恢复备份，或首次启动 App。' 'This copy has no data to back up. Restore a backup, or start the app first.'}
        else{$status.Text=T '用户数据文件夹当前不可用。请保留当前目录并检查访问权限。' 'The user-data folder is unavailable. Keep it and check access permissions.'}
        return
    }
    if($action -eq 'restore' -and $script:dataPresence -ne 'FRESH'){
        if($script:dataPresence -eq 'PRESENT'){$status.Text=T '此安装已有用户数据。请在尚未启动的新副本中恢复。' 'This installation already has user data. Restore in a fresh copy before its first start.'}
        else{$status.Text=T '用户数据位置不可用。请保留当前目录，并在新的完整副本中恢复。' 'The user-data location is unavailable. Keep it and restore into a fresh complete copy.'}
        return
    }
    $target=$root
    if($action -eq 'program'){
        $target=Select-Folder (T '选择已保留的完整 Cirven 程序目录；当前安装保持不变。' 'Choose a retained complete Cirven program. Keep the current installation.') $false
        if(-not $target){return}
        $target=[IO.Path]::GetFullPath($target)
        if((Same-Location $target $root) -or -not(Test-Path -LiteralPath (Join-Path $target 'PACKAGE_MANIFEST.json') -PathType Leaf)){
            $status.Text=T '请选择另一份完整程序；当前安装保持不变。' 'Choose another complete program. Keep this installation.'
            return
        }
        if((Test-Path -LiteralPath (Join-Path $target 'user-data')) -or (Get-InstallationRunning $target)){
            $status.Text=T '目标程序已运行或已有数据。请选择尚未启动的新副本。' 'The target is running or has data. Choose a fresh copy before its first start.'
            return
        }
    }
    if($action -eq 'backup'){
        $folder=Select-Folder (T '选择备份存放位置；将自动创建新的私有备份文件夹。' 'Choose a backup location. A new private backup folder will be created.') $true
        if(-not $folder){return}
        $folder=Join-Path $folder ('Cirven-backup-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,6))
    } else {
        $folder=Select-Folder (T '选择已有的 Cirven 私有备份文件夹。' 'Choose an existing Cirven private backup folder.') $false
        if(-not $folder){return}
    }
    if($action -in @('restore','program')){
        Start-MaintenanceJob 'check-target' $target $folder '' '' $action
    } else {
        Start-MaintenanceJob $action $target $folder '' '' $action
    }
}
$backup.Add_Click({Start-Maintenance 'backup'});$verify.Add_Click({Start-Maintenance 'verify'});$restore.Add_Click({Start-Maintenance 'restore'})
function Update-MaintenanceJob {
    if(-not $script:job){return}
    $seconds=[int]([DateTime]::UtcNow-$script:started).TotalSeconds
    $status.Text=T "正在校验安装与处理文件，已用 $seconds 秒…" "Checking installation and processing files · $seconds seconds…"
    if(-not $script:job.HasExited -or -not $script:outTask.IsCompleted -or -not $script:errTask.IsCompleted){return}
    $timer.Stop();$progress.Visible=$false
    $next=$null;$request=$script:request
    try {
        $raw=$script:outTask.Result;$errorText=$script:errTask.Result
        $script:diagnostic=$errorText
        if($script:job.ExitCode -ne 0){
            $message=T '操作未完成；原数据与已有备份保留。可复制诊断以供检查。' 'Not completed. Original data and existing backups remain. Copy details for investigation.'
            if($errorText -match 'Exit this installation|data is in use'){$message=T '目标程序仍在运行。请正常退出后重试。' 'The target program is running. Exit normally, then try again.'}
            elseif($errorText -match 'different OS user'){$message=T '此备份属于另一 Windows 用户，无法在当前账号恢复。' 'This backup belongs to another Windows user.'}
            elseif($errorText -match 'free space'){$message=T '磁盘空间不足。原数据保留，请清理空间后重试。' 'Not enough disk space. Original data remains. Free space and try again.'}
            elseif($errorText -match 'state protocol|saved migration|Runtime compatibility'){$message=T '目标程序不支持此备份的状态。原程序和备份保留，请选择兼容版本。' 'The target cannot read this backup. Keep the original program and backup, and choose a compatible version.'}
            elseif($errorText -match 'explicitly checked|Target program changed'){$message=T '目标程序已变化，请重新核对；尚未确认的操作不会自动重做。' 'The target program changed. Check it again; no unconfirmed operation is replayed.'}
            elseif($errorText -match 'differs|missing|changed|Invalid|Unsupported'){$message=T '备份或安装校验未通过。请检查所选备份与完整安装。' 'Backup or installation validation failed. Check the selected backup and complete installation.'}
            $status.Text=$message;$copy.Visible=$true
        } else {
            $result=$raw|ConvertFrom-Json
            $expected=@{backup='BACKUP_COMPLETE';verify='BACKUP_VERIFIED';restore='RESTORE_COMPLETE';'check-target'='RESTORE_TARGET_COMPATIBLE'}[$request.action]
            if($result -is [Array] -or $result.status -cne $expected){throw 'Maintenance result does not match its operation'}
            if($result.manifest_sha256 -isnot [string] -or $result.manifest_sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'Invalid backup receipt identity'}
            if($request.backupSha -and $result.manifest_sha256 -cne $request.backupSha){throw 'Backup result does not match its checked snapshot'}
            if($request.action -in @('check-target','restore','backup')){
                if($result.program_manifest_sha256 -isnot [string] -or $result.program_manifest_sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'Invalid program receipt identity'}
                if($request.programSha -and $result.program_manifest_sha256 -cne $request.programSha){throw 'Program result does not match its checked version'}
                if((Get-FileHash -LiteralPath (Join-Path $request.installation 'PACKAGE_MANIFEST.json') -Algorithm SHA256).Hash.ToLowerInvariant() -cne $result.program_manifest_sha256){throw 'Target program changed after its result'}
            }
            if($request.action -eq 'check-target'){
                if(-not(Same-Location ([string]$result.path) $request.installation) -or $result.user_data_written -isnot [bool] -or $result.user_data_written -or $result.model_or_agent_started -isnot [bool] -or $result.model_or_agent_started){throw 'Invalid readonly target result'}
                if(Confirm-Restore $request.folder $request.installation){
                    $next=[pscustomobject]@{installation=$request.installation;folder=$request.folder;backupSha=[string]$result.manifest_sha256;programSha=[string]$result.program_manifest_sha256;flow=$request.flow}
                } else {
                    $status.Text=T '已取消恢复。兼容核对未写入目标数据，原程序与备份保留。' 'Restore cancelled. The check wrote no target data. Keep the original program and backup.'
                }
            } elseif($request.action -eq 'verify'){
                $status.Text=T '备份校验通过。' 'Backup verified.';$location.Text=$request.folder
            } else {
                $expectedPath=if($request.action -eq 'restore'){Join-Path $request.installation 'user-data'}else{$request.folder}
                if(-not(Same-Location ([string]$result.path) $expectedPath)){throw 'Maintenance result points to another location'}
                if($request.action -eq 'restore' -and ($result.model_or_agent_started -isnot [bool] -or $result.model_or_agent_started)){throw 'Restore unexpectedly started business work'}
                $script:canOpenApp=$true;$script:openInstallation=$request.installation;$script:openManifestSha=[string]$result.program_manifest_sha256
                $openApp.Text=if($request.flow -eq 'program'){T '打开目标 App' 'Open target app'}else{T '打开 App' 'Open app'}
                if($request.action -eq 'backup'){$status.Text=T '备份完成。请将此私有备份保留在本机，可打开 App 继续工作。' 'Backup complete. Keep this private backup on this computer. Open the app to continue.'}
                elseif($request.flow -eq 'program'){$status.Text=T '目标程序恢复完成。当前安装保持不变，可打开目标 App 检查历史与草稿。' 'Target restored. Keep this installation. Open the target app to check history and drafts.'}
                else{$status.Text=T '恢复完成。可打开 App 检查历史与草稿。' 'Restore complete. Open the app to check history and drafts.'}
                $location.Text=[string]$result.path
            }
        }
    } catch {
        $script:canOpenApp=$false;$script:openManifestSha=$null
        $status.Text=T '未能确认操作结果。保留当前目录，请勿重复提交；复制诊断供检查。' 'The result could not be confirmed. Keep the current folders and copy details before submitting again.'
        $script:diagnostic=$_.Exception.Message;$copy.Visible=$true
    } finally {
        $script:job.Dispose();$script:job=$null;$script:request=$null;Update-Context;$close.Enabled=$true
    }
    if($next){Start-MaintenanceJob 'restore' $next.installation $next.folder $next.backupSha $next.programSha $next.flow}
}
$timer.Add_Tick({Update-MaintenanceJob})

$form.Add_FormClosing({param($sender,$eventArgs)
    if($script:job){$eventArgs.Cancel=$true;$status.Text=T '维护正在进行，请等待完成；可以最小化窗口。' 'Maintenance is in progress. Wait for completion; you can minimize this window.'}
})
Update-Context
$form.Add_Activated({if(-not $script:job){Update-Context}})
$form.Add_Shown({$form.ActiveControl=$form.AcceptButton})
if($MyInvocation.InvocationName -ne '.') {
    try {[Windows.Forms.Application]::Run($form)} finally {$timer.Dispose();$form.Dispose()}
}
