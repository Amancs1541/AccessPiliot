# AccessPilot V1 Documentation

Read these documents in order.

```text
00_MASTER_CODER_PLAN.md
01_PRODUCT_REQUIREMENTS.md
02_UI_UX_SPECIFICATION.md
03_ARCHITECTURE.md
04_FINAL_APP_ROLES.md
05_AUTHORIZATION_MATRIX.md
06_ENTRA_SETUP.md
07_ENTRA_GRAPH_MAPPING.md
08_DATABASE_SCHEMA.md
09_API_CONTRACT.md
10_STATE_MACHINES.md
11_PROVIDER_CONNECTOR_CONTRACT.md
12_ENVIRONMENT_CONFIGURATION.md
13_ERROR_CONTRACT.md
14_BACKGROUND_WORKERS.md
15_AUDIT_LOGGING.md
16_SECURITY_THREAT_MODEL.md
17_TEST_PLAN.md
18_DEPLOYMENT.md
```

## Coder rule

Do not invent behavior that conflicts with these documents.

When a requirement is ambiguous:

1. Identify the ambiguity.
2. Do not silently invent a privileged behavior.
3. Prefer least privilege.
4. Record the decision.
5. Update the relevant contract before implementation.

## Recommended implementation order

```text
Phase 0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6
                                   |
                                   v
                             Read-only stable
                                   |
                                   v
                         7 -> 8 -> 9 -> 10
                                   |
                                   v
                             11 -> 12 -> 13 -> 14
```

The first milestone is a secure read-only Entra console, not PIM write access.
