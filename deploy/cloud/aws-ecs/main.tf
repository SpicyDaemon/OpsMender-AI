## ECS cluster + Fargate task definition + service + CloudWatch logs.
##
## The task definition references three flavors of config:
##   1. extra_environment  — plain env vars (tier, log level, public URL).
##   2. *_secret_arn       — secrets pulled by the execution role from
##                            Secrets Manager at task-start time.
##   3. provider_secret_arns — same, one entry per LLM provider key.
##
## OpsMender's container entrypoint runs Alembic migrations on every
## task start. That's safe on Postgres but means a deploy briefly holds
## the DB lock — for zero-downtime upgrades, run migrations out of band
## and override the entrypoint to skip them.

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/ecs/${var.name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_ecs_cluster" "this" {
  name = var.name
  tags = var.tags

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 1
  }
}

locals {
  base_secrets = [
    {
      name      = "OPSMENDER_JWT_SECRET"
      valueFrom = var.jwt_secret_arn
    },
    {
      name      = "OPSMENDER_DATABASE_URL"
      valueFrom = var.database_url_secret_arn
    },
  ]

  provider_secrets = [
    for env_name, arn in var.provider_secret_arns : {
      name      = env_name
      valueFrom = arn
    }
  ]

  environment = [
    for k, v in var.extra_environment : {
      name  = k
      value = v
    }
  ]
}

resource "aws_ecs_task_definition" "this" {
  family                   = var.name
  cpu                      = var.container_cpu
  memory                   = var.container_memory
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = var.name
      image     = var.container_image
      essential = true

      portMappings = [
        {
          containerPort = var.container_port
          protocol      = "tcp"
        }
      ]

      environment = local.environment
      secrets     = concat(local.base_secrets, local.provider_secrets)

      healthCheck = {
        command     = ["CMD-SHELL", "curl -fsS http://localhost:${var.container_port}/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.this.name
          awslogs-region        = data.aws_region.current.region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])

  tags = var.tags
}

data "aws_region" "current" {}

resource "aws_ecs_service" "this" {
  name            = var.name
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = var.assign_public_ip
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.this.arn
    container_name   = var.name
    container_port   = var.container_port
  }

  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 200

  # Keep the service from oscillating during a redeploy when one task
  # is still draining and the other is starting up.
  health_check_grace_period_seconds = 60

  depends_on = [
    aws_lb_listener.http,
  ]

  tags = var.tags
}
