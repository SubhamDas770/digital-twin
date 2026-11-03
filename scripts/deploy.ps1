param(
    [string]$Environment = "dev",   # dev | test | prod
    [string]$ProjectName = "twin"
)
$ErrorActionPreference = "Stop"

function Invoke-CheckedNativeCommand {
    param(
        [Parameter(Mandatory)][scriptblock]$Command,
        [Parameter(Mandatory)][string]$Description
    )

    $result = & $Command
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$Description failed with exit code $exitCode."
    }

    return $result
}

Write-Host "Deploying $ProjectName to $Environment ..." -ForegroundColor Green

# 1. Build Lambda package
Set-Location (Split-Path $PSScriptRoot -Parent)   # project root
Write-Host "Building Lambda package..." -ForegroundColor Yellow
Set-Location backend
Invoke-CheckedNativeCommand { uv run deploy.py } "Building Lambda package"
Set-Location ..

# 2. Terraform workspace & apply
Set-Location terraform
$AwsAccountId = Invoke-CheckedNativeCommand { aws sts get-caller-identity --query Account --output text } "Getting AWS account ID"
$AwsRegion = if ($env:DEFAULT_AWS_REGION) { $env:DEFAULT_AWS_REGION } else { "us-east-1" }
Invoke-CheckedNativeCommand {
    terraform init -input=false `
        -backend-config="bucket=twin-terraform-state-$AwsAccountId" `
        -backend-config="key=$Environment/terraform.tfstate" `
        -backend-config="region=$AwsRegion" `
        -backend-config="dynamodb_table=twin-terraform-locks" `
        -backend-config="encrypt=true"
} "Terraform init"

$WorkspaceList = Invoke-CheckedNativeCommand { terraform workspace list } "Listing Terraform workspaces"
$WorkspaceListText = $WorkspaceList -join "`n"
if ($WorkspaceListText -notmatch "(?m)^\s*(\*\s*)?$([regex]::Escape($Environment))\s*$") {
    Invoke-CheckedNativeCommand { terraform workspace new $Environment } "Creating Terraform workspace"
} else {
    Invoke-CheckedNativeCommand { terraform workspace select $Environment } "Selecting Terraform workspace"
}

if ($Environment -eq "prod") {
    Invoke-CheckedNativeCommand { terraform apply -var-file="prod.tfvars" -var="project_name=$ProjectName" -var="environment=$Environment" -auto-approve } "Terraform apply"
} else {
    Invoke-CheckedNativeCommand { terraform apply -var="project_name=$ProjectName" -var="environment=$Environment" -auto-approve } "Terraform apply"
}

$ApiUrl = Invoke-CheckedNativeCommand { terraform output -raw api_gateway_url } "Reading API Gateway URL"
$FrontendBucket = Invoke-CheckedNativeCommand { terraform output -raw s3_frontend_bucket } "Reading frontend bucket"
$MemoryBucket = Invoke-CheckedNativeCommand { terraform output -raw s3_memory_bucket } "Reading memory bucket"
$FrontendUrl = Invoke-CheckedNativeCommand { terraform output -raw frontend_url } "Reading frontend URL"
$LegacyMemoryBucket = Invoke-CheckedNativeCommand { terraform output -raw legacy_s3_memory_bucket } "Reading legacy memory bucket"

foreach ($Output in @(
    @{ Name = "API Gateway URL"; Value = $ApiUrl },
    @{ Name = "frontend bucket"; Value = $FrontendBucket },
    @{ Name = "memory bucket"; Value = $MemoryBucket },
    @{ Name = "frontend URL"; Value = $FrontendUrl },
    @{ Name = "legacy memory bucket"; Value = $LegacyMemoryBucket }
)) {
    if ([string]::IsNullOrWhiteSpace($Output.Value)) {
        throw "Terraform returned an empty $($Output.Name)."
    }
}

# Copy existing memory data without deleting the source bucket.
Invoke-CheckedNativeCommand {
    aws s3 sync "s3://$LegacyMemoryBucket" "s3://$MemoryBucket" --source-region eu-north-1 --region us-east-1 --only-show-errors
} "Migrating memory data to us-east-1"

# 3. Build + deploy frontend
Set-Location ..\frontend

# Create production environment file with API URL
Write-Host "Setting API URL for production..." -ForegroundColor Yellow
"NEXT_PUBLIC_API_URL=$ApiUrl" | Out-File .env.production -Encoding utf8

Invoke-CheckedNativeCommand { npm install } "Installing frontend dependencies"
Invoke-CheckedNativeCommand { npm run build } "Building frontend"
Invoke-CheckedNativeCommand { aws s3 sync .\out "s3://$FrontendBucket/" --delete } "Uploading frontend"
Set-Location ..

# 4. Final summary
Write-Host "Deployment complete!" -ForegroundColor Green
Write-Host "Frontend URL   : $FrontendUrl" -ForegroundColor Cyan
Write-Host "API Gateway    : $ApiUrl" -ForegroundColor Cyan
