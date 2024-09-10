# Function to get pod metrics
function Get-PodMetrics {
    param (
        [string]$Namespace,
        [string]$PodName
    )

    $result = kubectl top pod $PodName -n $Namespace --no-headers
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Error fetching metrics: $result"
        return $null
    }

    $metrics = $result -split '\s+'
    $cpuUsage = [int]($metrics[1] -replace "[^\d]")  # CPU in millicores
    $memoryUsage = [int]($metrics[2] -replace "[^\d]")  # Memory in MiB
    return @($cpuUsage, $memoryUsage)
}

# Function to monitor pod
function Monitor-Pod {
    param (
        [string]$Namespace,
        [string]$PodName
    )

    $metrics = @()
    $initialMetric = $null

    Write-Host "Monitoring pod $PodName in namespace $Namespace. Press Ctrl+C to stop."

    while (true) {
        $metric = Get-PodMetrics -Namespace $Namespace -PodName $PodName
        if ($metric) {
            $metrics += , $metric
            if (-not $initialMetric) {
                $initialMetric = $metric
            }

            $cpuUsages = $metrics | ForEach-Object { $_[0] }
            $memoryUsages = $metrics | ForEach-Object { $_[1] }

            $initialCpu = $initialMetric[0]
            $initialMemory = $initialMetric[1]
            $avgCpu = ($cpuUsages | Measure-Object -Average).Average
            $peakCpu = ($cpuUsages | Measure-Object -Maximum).Maximum
            $avgMemory = ($memoryUsages | Measure-Object -Average).Average
            $peakMemory = ($memoryUsages | Measure-Object -Maximum).Maximum

            Clear-Host
            Write-Host "Current CPU: $($metric[0]) millicores, Current Memory: $($metric[1]) MiB"
            Write-Host "Initial CPU: $initialCpu millicores"
            Write-Host "Average CPU: $([math]::Round($avgCpu, 2)) millicores"
            Write-Host "Peak CPU: $peakCpu millicores"
            Write-Host "Initial Memory: $initialMemory MiB"
            Write-Host "Average Memory: $([math]::Round($avgMemory, 2)) MiB"
            Write-Host "Peak Memory: $peakMemory MiB"
        }
    }
}

# Main script
$namespace = "default"  # Replace with your namespace
$podName = "<pod-name>"  # Replace with your pod name

Monitor-Pod -Namespace $namespace -PodName $podName
