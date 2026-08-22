# AccessPilot V1 — PostgreSQL Schema

## Core tables

```text
identity_providers
users
groups
roles
user_groups
role_assignments
access_assignments
access_requests
approval_steps
policies
policy_targets
audit_logs
sync_runs
sync_errors
provider_resources
```

## identity_providers

```text
id UUID PK
name
type
status
tenant_id
organization_url
configuration_ref
last_sync_at
created_at
updated_at
```

`configuration_ref` is a reference to secure credential storage, not a secret.

## users

```text
id UUID PK
provider_id FK
external_id
email
display_name
given_name
surname
department
job_title
status
created_at
updated_at
last_synced_at
```

Unique:

```text
(provider_id, external_id)
```

## groups

```text
id UUID PK
provider_id FK
external_id
name
description
is_privileged
status
created_at
updated_at
last_synced_at
```

## roles

```text
id UUID PK
provider_id FK
external_id
name
description
role_type
is_privileged
status
created_at
updated_at
last_synced_at
```

## user_groups

```text
id UUID PK
user_id FK
group_id FK
source
created_at
updated_at
```

Unique:

```text
(user_id, group_id)
```

## role_assignments

Provider-side normalized assignment state:

```text
id UUID PK
provider_id FK
user_id FK
role_id FK
external_id
assignment_type
status
start_time
expiration_time
created_at
updated_at
last_synced_at
```

## access_assignments

AccessPilot governance lifecycle:

```text
id UUID PK
provider_id FK
user_id FK
resource_type
resource_id
assignment_type
status
start_time
expiration_time
justification
ticket_number
requested_by FK
approved_by FK
activated_at
revoked_at
created_at
updated_at
```

## access_requests

```text
id UUID PK
requester_id FK
provider_id FK
resource_type
resource_id
requested_duration_minutes
requested_start_time
requested_expiration_time
justification
ticket_number
status
risk_level
created_at
updated_at
approved_at
rejected_at
cancelled_at
```

## approval_steps

```text
id UUID PK
access_request_id FK
step_number
approver_user_id FK
status
comment
acted_at
created_at
updated_at
```

## policies

```text
id UUID PK
name
description
max_duration_minutes
require_mfa
require_approval
require_justification
require_ticket
risk_level
status
created_at
updated_at
```

## policy_targets

```text
id UUID PK
policy_id FK
provider_id FK
resource_type
resource_id
created_at
```

## audit_logs

```text
id UUID PK
timestamp
actor_user_id FK
action
target_type
target_id
provider_id FK
request_id
result
ip_address
user_agent
metadata JSONB
created_at
```

## sync_runs

```text
id UUID PK
provider_id FK
status
started_at
completed_at
users_processed
groups_processed
roles_processed
errors_count
created_at
```

## sync_errors

```text
id UUID PK
sync_run_id FK
resource_type
external_id
error_code
error_message
metadata JSONB
created_at
```

## provider_resources

```text
id UUID PK
provider_id FK
resource_type
external_id
display_name
metadata JSONB
created_at
updated_at
```

## Important indexes

Index:

```text
users(provider_id, external_id)
groups(provider_id, external_id)
roles(provider_id, external_id)
user_groups(user_id, group_id)
role_assignments(user_id)
role_assignments(expiration_time)
access_assignments(user_id)
access_assignments(status)
access_assignments(expiration_time)
access_requests(requester_id)
access_requests(status)
audit_logs(timestamp)
audit_logs(actor_user_id)
audit_logs(request_id)
sync_runs(provider_id)
```

## Database rules

- Use UUID internal IDs.
- Store provider external IDs separately.
- Use UTC timestamps.
- Use Alembic migrations.
- Avoid hard deleting audit records.
- Do not store secrets/tokens.
- Use transactions for privileged mutations.
- Protect against duplicate operations.
