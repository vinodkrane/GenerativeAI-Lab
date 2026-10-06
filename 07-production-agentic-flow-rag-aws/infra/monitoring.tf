resource "aws_sns_topic" "alarms" {
  name = "${local.name}-alarms"
}

resource "aws_sns_topic_subscription" "alarm_email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  app_namespace = "LeanAgenticRAG"
  alarm_actions = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${local.name}-ingestion-dlq-not-empty"
  alarm_description   = "Ingestion messages exhausted their retries. See docs/operations.md#dlq."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.ingestion_dlq.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "queue_backlog_age" {
  alarm_name          = "${local.name}-ingestion-backlog-age"
  alarm_description   = "Oldest ingestion message is older than 30 minutes."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = aws_sqs_queue.ingestion.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 1800
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${local.name}-api-5xx"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  dimensions          = { LoadBalancer = aws_lb.api.arn_suffix }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "query_latency_p95" {
  alarm_name          = "${local.name}-query-latency-p95"
  namespace           = local.app_namespace
  metric_name         = "QueryLatency"
  dimensions          = { Service = local.name }
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 3
  threshold           = 20000
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "ingestion_failures" {
  alarm_name          = "${local.name}-ingestion-failures"
  namespace           = local.app_namespace
  metric_name         = "IngestionFailures"
  dimensions          = { Service = local.name }
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 1
  threshold           = 3
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "acl_post_filter" {
  alarm_name          = "${local.name}-acl-post-filter-blocked"
  alarm_description   = "The index returned chunks the caller may not read. The post-filter blocked them; investigate the index filter immediately."
  namespace           = local.app_namespace
  metric_name         = "AclViolationsBlocked"
  dimensions          = { Service = local.name }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.name
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title = "Query latency (p50 / p95)", region = var.aws_region, view = "timeSeries"
          metrics = [
            [local.app_namespace, "QueryLatency", "Service", local.name, { stat = "p50" }],
            ["...", { stat = "p95" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title = "Queries, abstentions, citation failures", region = var.aws_region, view = "timeSeries", stat = "Sum"
          metrics = [
            [local.app_namespace, "Queries", "Service", local.name],
            [local.app_namespace, "Abstentions", "Service", local.name],
            [local.app_namespace, "CitationValidationFailures", "Service", local.name],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title = "Ingestion queue and DLQ", region = var.aws_region, view = "timeSeries", stat = "Maximum"
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.ingestion.name],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.ingestion_dlq.name],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title = "Ingestion outcomes and probe pass rate", region = var.aws_region, view = "timeSeries"
          metrics = [
            [local.app_namespace, "IngestionCompleted", "Service", local.name, { stat = "Sum" }],
            [local.app_namespace, "IngestionFailures", "Service", local.name, { stat = "Sum" }],
            [local.app_namespace, "ProbePassRate", "Service", local.name, { stat = "Average", yAxis = "right" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 12, height = 6
        properties = {
          title = "Agent calls and tokens", region = var.aws_region, view = "timeSeries", stat = "Sum"
          metrics = [
            [local.app_namespace, "AgentCalls", "Service", local.name],
            [local.app_namespace, "Tokens", "Service", local.name, { yAxis = "right" }],
            [local.app_namespace, "AgentInvalidOutput", "Service", local.name],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 12, width = 12, height = 6
        properties = {
          title   = "Estimated model cost (micro-USD)", region = var.aws_region, view = "timeSeries", stat = "Sum"
          metrics = [[local.app_namespace, "QueryCostMicroUSD", "Service", local.name]]
        }
      },
    ]
  })
}
