output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer. Point a CNAME / ALIAS at this from Route 53 (or your DNS provider) once you've confirmed the service is healthy."
  value       = aws_lb.this.dns_name
}

output "alb_zone_id" {
  description = "ALB hosted zone ID — pair with the DNS name when creating a Route 53 ALIAS record."
  value       = aws_lb.this.zone_id
}

output "alb_arn" {
  description = "ALB ARN. Useful for attaching WAF v2 web ACLs."
  value       = aws_lb.this.arn
}

output "cluster_name" {
  description = "ECS cluster name. Pass to `aws ecs update-service` if you redeploy out of band."
  value       = aws_ecs_cluster.this.name
}

output "service_name" {
  description = "ECS service name. Use with `aws ecs describe-services` and `aws logs tail` for live debugging."
  value       = aws_ecs_service.this.name
}

output "log_group_name" {
  description = "CloudWatch log group containing every container stream. Tail with `aws logs tail $LOG_GROUP --follow`."
  value       = aws_cloudwatch_log_group.this.name
}

output "task_role_arn" {
  description = "IAM role assumed by the container at runtime. Attach additional policies here if your MCP servers need AWS API access."
  value       = aws_iam_role.task.arn
}
