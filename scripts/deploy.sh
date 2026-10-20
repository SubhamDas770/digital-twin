#!/bin/bash
set -e

ENVIRONMENT=${1:-dev}          # dev | test | prod
PROJECT_NAME=${2:-twin}

run_checked() {
  local description="$1"
  shift
  if "$@"; then
    return 0
  else
    local status=$?
    echo "Error: ${description} failed (exit code ${status})." >&2
    exit "$status"
  fi
}

capture_checked() {
  local output_name="$1"
  local description="$2"
  shift 2
  local output
  if output=$("$@"); then
    printf -v "$output_name" '%s' "$output"
  else
    local status=$?
    echo "Error: ${description} failed (exit code ${status})." >&2
    exit "$status"
  fi
}

echo "🚀 Deploying ${PROJECT_NAME} to ${ENVIRONMENT}..."

# 1. Build Lambda package
cd "$(dirname "$0")/.."        # project root
echo "📦 Building Lambda package..."
run_checked "Building Lambda package" bash -c 'cd backend && uv run deploy.py'

# 2. Terraform workspace & apply
cd terraform
run_checked "Terraform init" terraform init -input=false

capture_checked WORKSPACES "Listing Terraform workspaces" terraform workspace list
if ! printf '%s\n' "$WORKSPACES" | grep -Eq "^[[:space:]]*\\*?[[:space:]]*${ENVIRONMENT}[[:space:]]*$"; then
  run_checked "Creating Terraform workspace" terraform workspace new "$ENVIRONMENT"
else
  run_checked "Selecting Terraform workspace" terraform workspace select "$ENVIRONMENT"
fi

# Use prod.tfvars for production environment
if [ "$ENVIRONMENT" = "prod" ]; then
  TF_APPLY_CMD=(terraform apply -var-file=prod.tfvars -var="project_name=$PROJECT_NAME" -var="environment=$ENVIRONMENT" -auto-approve)
else
  TF_APPLY_CMD=(terraform apply -var="project_name=$PROJECT_NAME" -var="environment=$ENVIRONMENT" -auto-approve)
fi

echo "🎯 Applying Terraform..."
run_checked "Terraform apply" "${TF_APPLY_CMD[@]}"

capture_checked API_URL "Reading API Gateway URL" terraform output -raw api_gateway_url
capture_checked FRONTEND_BUCKET "Reading frontend bucket" terraform output -raw s3_frontend_bucket
capture_checked MEMORY_BUCKET "Reading memory bucket" terraform output -raw s3_memory_bucket
capture_checked FRONTEND_URL "Reading frontend URL" terraform output -raw frontend_url
capture_checked LEGACY_MEMORY_BUCKET "Reading legacy memory bucket" terraform output -raw legacy_s3_memory_bucket

if [[ -z "$API_URL" || -z "$FRONTEND_BUCKET" || -z "$MEMORY_BUCKET" || -z "$FRONTEND_URL" || -z "$LEGACY_MEMORY_BUCKET" ]]; then
  echo "Error: Terraform returned an empty required output." >&2
  exit 1
fi

# Copy existing memory data without deleting the source bucket.
run_checked "Migrating memory data to us-east-1" aws s3 sync \
  "s3://${LEGACY_MEMORY_BUCKET}" "s3://${MEMORY_BUCKET}" \
  --source-region eu-north-1 --region us-east-1 --only-show-errors

# 3. Build + deploy frontend
cd ../frontend

# Create production environment file with API URL
echo "📝 Setting API URL for production..."
echo "NEXT_PUBLIC_API_URL=$API_URL" > .env.production

run_checked "Installing frontend dependencies" npm install
run_checked "Building frontend" npm run build
run_checked "Uploading frontend" aws s3 sync ./out "s3://${FRONTEND_BUCKET}/" --delete
cd ..

# 4. Final messages
echo -e "\n✅ Deployment complete!"
echo "🌐 Frontend URL   : $FRONTEND_URL"
echo "📡 API Gateway    : $API_URL"
