## Security groups
##
## Two SGs: one for the ALB (allows internet on 80/443), one for the
## Fargate tasks (allows ingress from the ALB SG only on the container
## port). The task SG has no public ingress — every request hits the ALB
## first.

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "OpsMender ALB ingress (80, 443) and egress to tasks."
  vpc_id      = var.vpc_id
  tags        = var.tags
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  for_each = toset(var.allowed_ingress_cidrs)

  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.value
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
  description       = "HTTP from operator-supplied CIDRs"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  for_each = var.acm_certificate_arn != "" ? toset(var.allowed_ingress_cidrs) : toset([])

  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.value
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  description       = "HTTPS from operator-supplied CIDRs"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_tasks" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.tasks.id
  from_port                    = var.container_port
  to_port                      = var.container_port
  ip_protocol                  = "tcp"
  description                  = "Forward to Fargate tasks on the container port"
}

resource "aws_security_group" "tasks" {
  name        = "${var.name}-tasks"
  description = "OpsMender Fargate task ingress (from ALB only) and egress (anywhere)."
  vpc_id      = var.vpc_id
  tags        = var.tags
}

resource "aws_vpc_security_group_ingress_rule" "tasks_from_alb" {
  security_group_id            = aws_security_group.tasks.id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = var.container_port
  to_port                      = var.container_port
  ip_protocol                  = "tcp"
  description                  = "Accept traffic from the ALB only"
}

# Tasks need outbound to GHCR (pull image), to the LLM provider, to
# Postgres, to MCP servers — anywhere. Tighten in production.
resource "aws_vpc_security_group_egress_rule" "tasks_egress_all" {
  security_group_id = aws_security_group.tasks.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "Egress to GHCR, LLM provider, Postgres, MCP servers"
}
