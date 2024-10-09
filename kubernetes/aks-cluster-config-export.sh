# List of subscription names
subscriptions=("Subscription1" "Subscription2" "Subscription3")

for sub in "${subscriptions[@]}"; do
  # Set the current subscription
  az account set --subscription "$sub"
  
  # List all AKS clusters with "test-aks" in their name. Replace "keyword to search" with your cluster name
  clusters=$(az aks list --query "[?contains(name, 'test-aks')].{name:name, rg:resourceGroup}" -o tsv)
  
clusters_array=($clusters) # Convert string to array
for ((i=0; i<${#clusters_array[@]}; i+=2)); do
    cluster_name=${clusters_array[$i]}
    resource_group=${clusters_array[$i+1]}
    # Export kubeconfig to a separate file
    az aks get-credentials --resource-group $resource_group --name $cluster_name --file ./kube-config
  done
done