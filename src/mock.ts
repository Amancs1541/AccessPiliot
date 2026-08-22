export type Role = 'user' | 'admin';
export type RequestStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'CANCELLED' | 'ACTIVE' | 'EXPIRED';
export type AssignmentStatus = 'ELIGIBLE' | 'ACTIVE' | 'EXPIRED' | 'REVOKED';

export interface User { id: string; name: string; initials: string; email: string; department: string; title: string; status: 'Active' | 'Inactive'; groups: number; privileged: boolean; lastSignIn: string; }
export interface AccessRequest { id: string; requester: string; resource: string; type: string; provider: string; duration: string; risk: 'Low' | 'Medium' | 'High'; status: RequestStatus; created: string; approval: string; justification: string; }
export interface Assignment { id: string; user: string; resource: string; type: string; provider: string; status: AssignmentStatus; start: string; expiration: string; remaining: string; }

export const currentUser = { name: 'Jordan Lee', email: 'jordan.lee@northstar.io', initials: 'JL', department: 'Platform Engineering', title: 'Senior Cloud Engineer' };
export const users: User[] = [
  { id: 'u-1', name: 'Jordan Lee', initials: 'JL', email: 'jordan.lee@northstar.io', department: 'Platform Engineering', title: 'Senior Cloud Engineer', status: 'Active', groups: 8, privileged: true, lastSignIn: 'Today, 09:42' },
  { id: 'u-2', name: 'Priya Nair', initials: 'PN', email: 'priya.nair@northstar.io', department: 'Security', title: 'Security Architect', status: 'Active', groups: 12, privileged: true, lastSignIn: 'Today, 08:17' },
  { id: 'u-3', name: 'Marcus Chen', initials: 'MC', email: 'marcus.chen@northstar.io', department: 'Finance', title: 'Financial Analyst', status: 'Active', groups: 5, privileged: false, lastSignIn: 'Yesterday, 16:31' },
  { id: 'u-4', name: 'Elena Rodriguez', initials: 'ER', email: 'elena.rodriguez@northstar.io', department: 'Product', title: 'Product Manager', status: 'Active', groups: 7, privileged: false, lastSignIn: 'Yesterday, 14:08' },
  { id: 'u-5', name: 'David Okafor', initials: 'DO', email: 'david.okafor@northstar.io', department: 'IT Operations', title: 'Systems Administrator', status: 'Inactive', groups: 9, privileged: true, lastSignIn: 'Aug 18, 11:20' },
];
export const requests: AccessRequest[] = [
  { id: 'REQ-1048', requester: 'Jordan Lee', resource: 'Production Administrator', type: 'DIRECTORY_ROLE', provider: 'Microsoft Entra ID', duration: '2 hours', risk: 'High', status: 'PENDING', created: 'Today, 10:14', approval: 'Priya Nair', justification: 'Investigate elevated error rates in the production API cluster.', },
  { id: 'REQ-1047', requester: 'Elena Rodriguez', resource: 'Product Analytics', type: 'GROUP', provider: 'Microsoft Entra ID', duration: '8 hours', risk: 'Medium', status: 'APPROVED', created: 'Today, 09:02', approval: 'Priya Nair', justification: 'Review launch funnel metrics for Q3 planning.', },
  { id: 'REQ-1046', requester: 'Marcus Chen', resource: 'Finance Reporting', type: 'GROUP', provider: 'Microsoft Entra ID', duration: '4 hours', risk: 'Low', status: 'ACTIVE', created: 'Yesterday, 15:44', approval: 'Automatic', justification: 'Prepare monthly close reporting package.', },
  { id: 'REQ-1045', requester: 'David Okafor', resource: 'Global Administrator', type: 'DIRECTORY_ROLE', provider: 'Microsoft Entra ID', duration: '1 hour', risk: 'High', status: 'REJECTED', created: 'Yesterday, 12:20', approval: 'Priya Nair', justification: 'Resolve a tenant configuration issue.', },
];
export const assignments: Assignment[] = [
  { id: 'ASG-2201', user: 'Jordan Lee', resource: 'Production Administrator', type: 'DIRECTORY_ROLE', provider: 'Microsoft Entra ID', status: 'ELIGIBLE', start: '—', expiration: '—', remaining: '—' },
  { id: 'ASG-2202', user: 'Marcus Chen', resource: 'Finance Reporting', type: 'GROUP', provider: 'Microsoft Entra ID', status: 'ACTIVE', start: 'Today, 09:31', expiration: 'Today, 13:31', remaining: '03:12:08' },
  { id: 'ASG-2203', user: 'Priya Nair', resource: 'Security Administrator', type: 'DIRECTORY_ROLE', provider: 'Microsoft Entra ID', status: 'ACTIVE', start: 'Today, 08:04', expiration: 'Today, 18:04', remaining: '07:45:22' },
  { id: 'ASG-2204', user: 'Elena Rodriguez', resource: 'Product Analytics', type: 'GROUP', provider: 'Microsoft Entra ID', status: 'EXPIRED', start: 'Aug 20, 09:00', expiration: 'Aug 20, 17:00', remaining: '—' },
];
export const groups = ['Platform Engineering', 'Security Operations', 'Finance Reporting', 'Product Analytics', 'AccessPilot-Admins'];
export const roles = ['Global Administrator', 'Security Administrator', 'Production Administrator', 'User Administrator', 'Reports Reader'];
export const policies = [
  { name: 'Privileged role activation', description: 'Controls just-in-time elevation for directory roles.', scope: 'Directory roles', max: '4 hours', approval: 'Required', mfa: 'Required', ticket: 'Required', status: 'Active' },
  { name: 'Standard group access', description: 'Self-service access to approved business groups.', scope: 'Groups', max: '8 hours', approval: 'Not required', mfa: 'Required', ticket: 'Optional', status: 'Active' },
  { name: 'Break-glass elevation', description: 'Emergency access for incident response only.', scope: 'Privileged roles', max: '1 hour', approval: 'Required', mfa: 'Required', ticket: 'Required', status: 'Disabled' },
];
export const auditEvents = [
  ['10:14:22 UTC', 'Jordan Lee', 'ACCESS_REQUEST_CREATED', 'Production Administrator', 'Microsoft Entra ID', 'SUCCESS', 'REQ-1048'],
  ['09:31:05 UTC', 'Marcus Chen', 'ACCESS_ACTIVATED', 'Finance Reporting', 'Microsoft Entra ID', 'SUCCESS', 'ASG-2202'],
  ['09:02:18 UTC', 'Priya Nair', 'ACCESS_REQUEST_APPROVED', 'Product Analytics', 'Microsoft Entra ID', 'SUCCESS', 'REQ-1047'],
  ['08:17:44 UTC', 'Priya Nair', 'LOGIN_SUCCESS', 'Priya Nair', 'Microsoft Entra ID', 'SUCCESS', 'EVT-8812'],
  ['Yesterday 17:00', 'AccessPilot Worker', 'ACCESS_EXPIRED', 'Product Analytics', 'Microsoft Entra ID', 'SUCCESS', 'ASG-2198'],
  ['Yesterday 15:42', 'Priya Nair', 'POLICY_UPDATED', 'Privileged role activation', 'AccessPilot', 'SUCCESS', 'POL-0042'],
];
