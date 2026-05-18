## Task EXECUTION role — used by Fargate itself to pull the image, write
## logs, and fetch the secrets referenced in the task definition.
##
## This is distinct from the task ROLE (below), which is the identity the
## OpsMender process inside the container assumes at runtime. Keep the
## task role minimal — OpsMender operates on infrastructure through MCP
## servers, not directly through the task's AWS credentials.

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Allow the execution role to read every secret the task references.
data "aws_iam_policy_document" "execution_secrets" {
  statement {
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = concat(
      [var.jwt_secret_arn, var.database_url_secret_arn],
      values(var.provider_secret_arns),
    )
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "${var.name}-secrets-read"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

## Task RUNTIME role — assumed by the OpsMender process at runtime.
## Empty by default; attach a managed/inline policy if your MCP servers
## need to call AWS APIs from inside the task (uncommon — most MCP
## servers run as separate processes the task connects out to).

resource "aws_iam_role" "task" {
  name               = "${var.name}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = var.tags
}
