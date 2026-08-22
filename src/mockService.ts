import { useSyncExternalStore } from 'react';
import { getMockState, subscribeMockState, transitionAssignment, transitionRequest } from './mockStore';
import type { AssignmentStatus, RequestStatus } from './mock';

export function useMockState() { return useSyncExternalStore(subscribeMockState, getMockState, getMockState); }
export const mockService = {
  approveRequest: (id: string) => transitionRequest(id, 'APPROVED'),
  rejectRequest: (id: string) => transitionRequest(id, 'REJECTED'),
  cancelRequest: (id: string) => transitionRequest(id, 'CANCELLED'),
  activateAssignment: (id: string) => transitionAssignment(id, 'ACTIVE'),
  revokeAssignment: (id: string) => transitionAssignment(id, 'REVOKED'),
  transitionRequest: (id: string, status: RequestStatus) => transitionRequest(id, status),
  transitionAssignment: (id: string, status: AssignmentStatus) => transitionAssignment(id, status),
};
