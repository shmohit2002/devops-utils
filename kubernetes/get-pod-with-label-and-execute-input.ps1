$context = Read-Host "Enter the Kubernetes cluster context (or press Enter for default)"
if ($context -eq "") { $context = "" }

$namespace = Read-Host "Enter the namespace (or press Enter for default)"
if ($namespace -eq "") { $namespace = "default" }

$label = Read-Host "Enter the label"
$command = Read-Host "Enter the command for the pod (or press Enter for default)"
if ($command -eq "") { $command = "bash" }

$delay = 5 # or the delay you want
$context_text = if ($context -ne "") { "--context=$context" } else { "" }
$podNames = (kubectl get pod $context_text -l $label -n $namespace --output=jsonpath='{.items[*].metadata.name}').Split()
foreach ($podName in $podNames) { 
    echo "Executing command on pod $podName"
    kubectl exec -it $podName -n $namespace $context_text -- $command;
    sleep $delay
}