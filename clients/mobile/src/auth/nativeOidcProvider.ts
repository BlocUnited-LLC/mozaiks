import { authorize, refresh, revoke, type AuthConfiguration } from 'react-native-app-auth';
import { jwtDecode } from 'jwt-decode';
import appConfig, { getMobilePlatformConfig } from '../platform/appConfig';
import { clearToken, getToken, persistToken } from './tokenStore';
import { getNativeAuthProvider, type NativeAuthProvider, type NativeAuthUser } from './nativeAuthBridge';
import { storage } from '../platform/mmkvInstance';

const REFRESH_TOKEN_KEY = 'mozaiks_refresh_token';
const ID_TOKEN_KEY = 'mozaiks_id_token';
const USER_KEY = 'mozaiks_current_user';

let builtInProvider: NativeAuthProvider | null = null;
let authStateSubscribers = new Set<(user: NativeAuthUser | null) => void>();

function getIssuer(): string {
  const authority = appConfig.auth?.keycloak?.authority as string | undefined;
  const realm = appConfig.auth?.keycloak?.realm as string | undefined;

  if (!authority || !realm) {
    throw new Error('keycloak-native requires auth.keycloak.authority and auth.keycloak.realm in platform/app.json.');
  }

  return `${authority.replace(/\/+$/, '')}/realms/${realm}`;
}

function getRedirectUrl(): string {
  const mobileConfig = getMobilePlatformConfig();
  const scheme = mobileConfig.auth.redirectScheme;
  const path = mobileConfig.auth.redirectPath;
  return `${scheme}:/${path}`;
}

function getOidcConfig(): AuthConfiguration {
  const clientId = appConfig.auth?.keycloak?.clientId as string | undefined;
  if (!clientId) {
    throw new Error('keycloak-native requires auth.keycloak.clientId in platform/app.json.');
  }

  return {
    issuer: getIssuer(),
    clientId,
    redirectUrl: getRedirectUrl(),
    scopes: getMobilePlatformConfig().auth.scopes ?? ['openid', 'profile', 'email'],
    dangerouslyAllowInsecureHttpRequests: /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?/i.test(getIssuer()),
  };
}

function notifyAuthState(user: NativeAuthUser | null): void {
  for (const callback of authStateSubscribers) {
    callback(user);
  }
}

function readStoredUser(): NativeAuthUser | null {
  const raw = storage.getString(USER_KEY);
  if (!raw) return null;

  try {
    return JSON.parse(raw) as NativeAuthUser;
  } catch {
    return null;
  }
}

function writeStoredUser(user: NativeAuthUser | null): void {
  if (!user) {
    storage.delete(USER_KEY);
    return;
  }

  storage.set(USER_KEY, JSON.stringify(user));
}

function decodeJwtClaims(token: string | null): Record<string, unknown> | null {
  if (!token) return null;

  const parts = token.split('.');
  if (parts.length < 2) return null;

  try {
    return jwtDecode<Record<string, unknown>>(token);
  } catch {
    return null;
  }
}

function buildUserFromToken(token: string | null): NativeAuthUser | null {
  const claims = decodeJwtClaims(token);
  if (!claims) return null;

  const realmAccess = claims.realm_access as { roles?: string[] } | undefined;
  return {
    user_id: typeof claims.sub === 'string' ? claims.sub : undefined,
    email: typeof claims.email === 'string' ? claims.email : undefined,
    name: (claims.name as string | undefined) || (claims.preferred_username as string | undefined),
    roles: Array.isArray(realmAccess?.roles) ? realmAccess.roles : [],
    authenticated: true,
  };
}

function persistAuthSession(tokens: { accessToken: string; refreshToken?: string; idToken?: string }): NativeAuthUser | null {
  persistToken(tokens.accessToken);
  if (tokens.refreshToken) storage.set(REFRESH_TOKEN_KEY, tokens.refreshToken);
  if (tokens.idToken) storage.set(ID_TOKEN_KEY, tokens.idToken);

  const user = buildUserFromToken(tokens.accessToken);
  writeStoredUser(user);
  notifyAuthState(user);
  return user;
}

function clearAuthSession(): void {
  clearToken();
  storage.delete(REFRESH_TOKEN_KEY);
  storage.delete(ID_TOKEN_KEY);
  writeStoredUser(null);
  notifyAuthState(null);
}

function createBuiltInOidcProvider(): NativeAuthProvider {
  return {
    getAccessToken() {
      return getToken();
    },

    async getCurrentUser() {
      return readStoredUser();
    },

    async login() {
      const result = await authorize(getOidcConfig());
      return persistAuthSession({
        accessToken: result.accessToken,
        refreshToken: result.refreshToken,
        idToken: result.idToken,
      });
    },

    async logout() {
      const accessToken = getToken();
      const refreshTokenValue = storage.getString(REFRESH_TOKEN_KEY) ?? undefined;

      try {
        if (accessToken) {
          await revoke(getOidcConfig(), {
            tokenToRevoke: refreshTokenValue ?? accessToken,
            sendClientId: true,
          });
        }
      } catch {
        // Session revocation failures should not strand local logout.
      }

      clearAuthSession();
      return { success: true };
    },

    async refreshToken() {
      const refreshTokenValue = storage.getString(REFRESH_TOKEN_KEY);
      if (!refreshTokenValue) {
        throw new Error('No refresh token available for keycloak-native auth.');
      }

      const result = await refresh(getOidcConfig(), {
        refreshToken: refreshTokenValue,
      });

      persistAuthSession({
        accessToken: result.accessToken,
        refreshToken: result.refreshToken ?? refreshTokenValue,
        idToken: result.idToken,
      });

      return { success: true };
    },

    onAuthStateChange(callback) {
      authStateSubscribers.add(callback);
      callback(readStoredUser());
      return () => authStateSubscribers.delete(callback);
    },
  };
}

export function getConfiguredNativeAuthProvider(providerName: string): NativeAuthProvider {
  if (providerName === 'external') {
    const provider = getNativeAuthProvider();
    if (!provider) {
      throw new Error('platforms.mobile.auth.provider="external" requires a registered native auth provider.');
    }
    return provider;
  }

  if (providerName === 'oidc' || providerName === 'keycloak-native') {
    if (!builtInProvider) {
      builtInProvider = createBuiltInOidcProvider();
    }
    return builtInProvider;
  }

  throw new Error(`Unsupported configured native auth provider: ${providerName}`);
}