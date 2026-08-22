import { assignments as initialAssignments, requests as initialRequests, type AccessRequest, type Assignment, type AssignmentStatus, type RequestStatus } from './mock';

export interface MockState { requests: AccessRequest[]; assignments: Assignment[]; }
let state: MockState = { requests: initialRequests.map(request => ({ ...request })), assignments: initialAssignments.map(assignment => ({ ...assignment })) };
const listeners = new Set<() => void>();
const requestTransitions: Record<RequestStatus, RequestStatus[]> = { PENDING: ['APPROVED', 'REJECTED', 'CANCELLED'], APPROVED: [], REJECTED: [], CANCELLED: [], ACTIVE: [], EXPIRED: [] };
const assignmentTransitions: Record<AssignmentStatus, AssignmentStatus[]> = { ELIGIBLE: ['ACTIVE'], ACTIVE: ['REVOKED'], EXPIRED: [], REVOKED: [] };

export function getMockState() { return state; }
export function subscribeMockState(listener: () => void) { listeners.add(listener); return () => listeners.delete(listener); }
function notify() { listeners.forEach(listener => listener()); }
function canTransition<T extends string>(allowed: Record<T, T[]>, from: T, to: T) { return allowed[from].includes(to); }
export function transitionRequest(id: string, status: RequestStatus) { const request = state.requests.find(item => item.id === id); if (!request || !canTransition(requestTransitions, request.status, status)) return false; state = { ...state, requests: state.requests.map(item => item.id === id ? { ...item, status } : item) }; notify(); return true; }
export function transitionAssignment(id: string, status: AssignmentStatus) { const assignment = state.assignments.find(item => item.id === id); if (!assignment || !canTransition(assignmentTransitions, assignment.status, status)) return false; state = { ...state, assignments: state.assignments.map(item => item.id === id ? { ...item, status, ...(status === 'ACTIVE' ? { start: 'Just now', expiration: 'Today, 14:00', remaining: '04:00:00' } : {}), ...(status === 'REVOKED' ? { remaining: '—' } : {}) } : item) }; notify(); return true; }
