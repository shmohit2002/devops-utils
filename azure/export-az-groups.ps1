# Insert the names of the azure AD groups you want to export members from like $groupList = @("sg.group1", "sg.group2")
$groupList = @()

# Function to get members recursively
function Get-MembersRecursively {
    param (
        [string]$groupId
    )
    $members = Get-AzureADGroupMember -ObjectId $groupId
    $memberList = @()
    foreach ($member in $members) {
        if ($member.ObjectType -eq "Group") {
            $memberList += Get-MembersRecursively -groupId $member.ObjectId
        } else {
            $memberObject = New-Object PSObject
            $memberObject | Add-Member -MemberType NoteProperty -Name "DisplayName" -Value $member.DisplayName
            $memberObject | Add-Member -MemberType NoteProperty -Name "Email" -Value $member.UserPrincipalName
            $memberList += $memberObject
        }
    }
    return $memberList
}

# Get all groups and collect detailed information
$groups = Get-AzureADGroup -All $true
foreach ($group in $groups) {
    if ($groupList -contains $group.DisplayName) {
        # Get group members recursively
        $memberList = Get-MembersRecursively -groupId $group.ObjectId

        # Export members to CSV with group name
        $csvPath = $group.DisplayName + "_members.csv"
        $memberList | Export-Csv -Path $csvPath -Encoding UTF8 -Delimiter "," -NoTypeInformation
    }
}

Write-Host "Group members exported successfully!" -ForegroundColor Green
