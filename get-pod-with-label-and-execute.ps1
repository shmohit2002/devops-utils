$context= "" # or your kubernetes cluster context
$namespace = "default" # or your namespace
$label = "" # or your label
$command = "bash" # or your command
$delay = 5 # or your delay
$context_text = if ($context -ne "") { "--context=$context" } else { "" }
$podNames = (kubectl get pod $context_text -l $label -n $namespace --output=jsonpath='{.items[*].metadata.name}').Split()
foreach ($podName in $podNames) { 
    kubectl exec -it $podName -n $namespace $context_text -- $command;
    sleep $delay
}
