# Safety net for the overnight run (Windows task "A-eye restore", 07:25): unload everything, warm the app's models.
$o = 'http://127.0.0.1:11434'
if (-not $env:NIGHT_LOG_DIR) { throw 'Set NIGHT_LOG_DIR before running night_restore.ps1' }
$log = Join-Path $env:NIGHT_LOG_DIR 'restore_task.txt'
try { (Invoke-RestMethod "$o/api/ps").models | ForEach-Object { Invoke-RestMethod "$o/api/generate" -Method Post -Body (@{model=$_.name; keep_alive=0} | ConvertTo-Json) | Out-Null } } catch {}
Start-Sleep 3
foreach ($m in 'qwen3.5:9b-q4_K_M','qwen2.5vl:7b-q4_K_M') {
  $b = @{model=$m; stream=$false; keep_alive='30m'; think=$false; options=@{temperature=0; num_ctx=4096; num_predict=1}; messages=@(@{role='user'; content='Reply OK.'})} | ConvertTo-Json -Depth 5
  try { Invoke-RestMethod "$o/api/chat" -Method Post -Body $b | Out-Null } catch {}
}
try { Invoke-RestMethod "$o/api/embed" -Method Post -Body (@{model='embeddinggemma:300m'; input='warm up'; keep_alive='24h'} | ConvertTo-Json) | Out-Null } catch {}
$b = @{model='qwen3.6:35b-a3b-q4_K_M'; stream=$false; keep_alive='2h'; think=$false; options=@{num_ctx=4096; num_gpu=0; num_predict=1}; messages=@(@{role='user'; content='Reply OK.'})} | ConvertTo-Json -Depth 5
try { Invoke-RestMethod "$o/api/chat" -Method Post -Body $b | Out-Null } catch {}
"$(Get-Date -Format HH:mm:ss) restored: " + (((Invoke-RestMethod "$o/api/ps").models | ForEach-Object { $_.name }) -join ', ') | Out-File -Append $log
