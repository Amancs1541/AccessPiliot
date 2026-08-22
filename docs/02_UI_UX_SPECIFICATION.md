# AccessPilot V1 — UI/UX Specification

## 1. Design direction

The UI should feel like an enterprise security product:

- Clean
- Professional
- Dense but readable
- Strong information hierarchy
- Clear security states
- Minimal decorative elements
- Responsive desktop-first layout
- Accessible keyboard navigation
- Consistent tables, drawers, dialogs and badges

Recommended stack:

```text
React
TypeScript
Vite
Tailwind CSS
shadcn/ui
React Router
TanStack Query
MSAL React
```

## 2. Application shell

```text
+-------------------------------------------------------+
| AccessPilot | Search                 User / Tenant     |
+------------+------------------------------------------+
| Dashboard  |                                          |
| My Access  |              Page Content                |
| Requests   |                                          |
|            |                                          |
| ADMIN      |                                          |
| Users      |                                          |
| Groups     |                                          |
| Roles      |                                          |
| Policies   |                                          |
| Audit      |                                          |
| Providers  |                                          |
+------------+------------------------------------------+
```

## 3. Normal User pages

### Dashboard

Show:

- Active access
- Eligible access
- Pending requests
- Expiring soon
- Recent activity

### My Access

Columns:

- Resource
- Type
- Provider
- Status
- Activated
- Expires
- Remaining time
- Actions

### Request Access

Steps:

1. Select resource
2. Select duration
3. Enter justification
4. Enter ticket if required
5. Show policy requirements
6. Submit

### My Requests

Columns:

- Resource
- Requested duration
- Risk
- Status
- Created
- Approval
- Actions

## 4. Admin pages

### Dashboard

Cards:

- Users
- Groups
- Roles
- Active privileged assignments
- Pending requests
- Expiring assignments
- Provider health

### Users

Features:

- Search
- Filter
- Pagination
- User detail drawer/page
- Groups
- Roles
- Assignments
- Audit

### Groups

Features:

- Search
- Members
- Privileged indicator
- Add member
- Remove member
- Assignment/policy visibility

### Roles

Features:

- Search
- Role detail
- Assignment visibility
- Privileged indicator

### Access Requests

Features:

- Filters
- Risk badge
- Request detail
- Approve
- Reject
- Audit timeline

### Assignments

Features:

- Active/eligible/expired/revoked
- Expiration countdown
- Revoke
- Extend when permitted

### Policies

Features:

- List
- Create
- Edit
- Disable
- Preview policy impact

### Audit

Features:

- Search
- Action filter
- Actor
- Target
- Provider
- Result
- Date range
- Event detail

### Providers

Features:

- Provider status
- Last sync
- Health
- Sync
- Configuration metadata
- Connection test

## 5. UI states

Every API-driven page must implement:

```text
Loading
Success
Empty
Error
Unauthorized
Forbidden
Retry
```

## 6. Security UI rule

The UI may hide unavailable actions, but hiding a button is not authorization.

The backend must enforce the same permission.

## 7. Countdown

The countdown is informational.

Backend expiration timestamp is authoritative.

## 8. Confirmation dialogs

Require confirmation for:

- Revoke
- Remove group member
- Reject request
- Disable policy
- Disconnect provider

High-risk operations should require a reason.
