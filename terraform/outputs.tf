output "api_gateway_url" {
  description = "URL of the API Gateway"
  value       = aws_apigatewayv2_api.main.api_endpoint
}

output "frontend_url" {
  description = "HTTP website URL of the S3 frontend bucket"
  value       = "http://${aws_s3_bucket_website_configuration.frontend_us.website_endpoint}"
}

output "s3_frontend_bucket" {
  description = "Name of the S3 bucket for frontend"
  value       = aws_s3_bucket.frontend_us.id
}

output "s3_memory_bucket" {
  description = "Name of the S3 bucket for memory storage"
  value       = aws_s3_bucket.memory_us.id
}

output "legacy_s3_memory_bucket" {
  description = "Name of the existing memory bucket in eu-north-1"
  value       = aws_s3_bucket.memory.id
}

output "legacy_s3_frontend_bucket" {
  description = "Name of the existing frontend bucket in eu-north-1"
  value       = aws_s3_bucket.frontend.id
}

output "lambda_function_name" {
  description = "Name of the Lambda function"
  value       = aws_lambda_function.api.function_name
}
