export type NativeAuthUser = {
  user_id?: string;
  email?: string;
  name?: string;
  roles?: string[];
  authenticated?: boolean;
};

export type NativeAuthProvider = {
  getAccessToken: () => string | null;
  getCurrentUser: () => Promise<NativeAuthUser | null>;
  login: (credentials?: unknown) => Promise<unknown>;
  logout: () => Promise<unknown>;
  refreshToken: () => Promise<unknown>;
  onAuthStateChange: (callback: (user: NativeAuthUser | null) => void) => (() => void) | void;
};

let nativeAuthProvider: NativeAuthProvider | null = null;

export function registerNativeAuthProvider(provider: NativeAuthProvider): void {
  nativeAuthProvider = provider;
}

export function clearNativeAuthProvider(): void {
  nativeAuthProvider = null;
}

export function getNativeAuthProvider(): NativeAuthProvider | null {
  return nativeAuthProvider;
}