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

}

# Main script
$namespace = "default"  # Replace with your namespace
$podName = "<pod-name>"  # Replace with your pod name

Monitor-Pod -Namespace $namespace -PodName $podName
