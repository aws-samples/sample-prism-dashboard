import { createContext, useContext } from 'react';

export interface UserContextType {
  role: string;
  email: string;
  userRecord: any;
}

export const UserContext = createContext<UserContextType>({
  role: 'user',
  email: '',
  userRecord: null,
});

export function useUserContext() {
  return useContext(UserContext);
}
