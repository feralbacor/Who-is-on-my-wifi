# Barre la red de este PC y devuelve en JSON los vecinos que acaban de contestar a ARP.
$cfg = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | Select-Object -First 1
if (-not $cfg) { '{"error":"Sin conexion de red"}'; exit }
$ip = @($cfg.IPv4Address)[0].IPAddress
$base = $ip -replace '\.\d+$', ''
# shortcut: solo barre la /24 de este PC; ampliar si la red de casa es más grande
$pings = 1..254 | ForEach-Object { (New-Object System.Net.NetworkInformation.Ping).SendPingAsync("$base.$_", 1000) }
try { [Threading.Tasks.Task]::WaitAll($pings) } catch {}
Start-Sleep -Seconds 6  # deja a Windows terminar las sondas ARP para que el estado sea fresco
$vecinos = Get-NetNeighbor -InterfaceIndex $cfg.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Where-Object { "$($_.State)" -eq 'Reachable' -and $_.IPAddress -like "$base.*" } |
  ForEach-Object { @{ ip = $_.IPAddress; mac = $_.LinkLayerAddress } }
@{
  ip = $ip
  mac = (Get-NetAdapter -InterfaceIndex $cfg.InterfaceIndex).MacAddress
  gateway = @($cfg.IPv4DefaultGateway)[0].NextHop
  red = "$base.0/24"
  vecinos = @($vecinos)
} | ConvertTo-Json -Depth 3 -Compress
