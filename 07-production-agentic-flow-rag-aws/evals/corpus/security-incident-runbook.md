# Security Incident Response Runbook

This runbook is for the Acme engineering on-call rotation. It describes what to do in the first hour of a suspected security incident.

## Severity levels

A SEV1 incident is any confirmed data breach or loss of customer data. A SEV2 incident is a suspected compromise of a production system with no confirmed data loss. A SEV3 incident is a vulnerability report that has not been exploited.

## First response

For a SEV1 incident, page the security lead immediately and open a bridge call within 15 minutes. Preserve evidence: do not reboot or terminate affected instances; take EBS snapshots and isolate them by moving them into the quarantine security group.

## Credential rotation

If credentials may have leaked, rotate the affected IAM access keys and database passwords through AWS Secrets Manager. Revoke active sessions in Cognito for impacted users.

## Communication

Customer notification for a confirmed SEV1 breach must be approved by Legal and sent within 72 hours of confirmation, in line with GDPR obligations.
