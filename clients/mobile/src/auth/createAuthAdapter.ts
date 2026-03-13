import { ExternalAuthAdapter, TokenAuthAdapter } from '@mozaiks/chat-ui/core';
import { storage } from '../platform/mmkvInstance';
import appConfig, { getMobilePlatformConfig } from '../platform/appConfig';
import { getNativeAuthProvider, type NativeAuthProvider } from './nativeAuthBridge';
import { getConfiguredNativeAuthProvider } from './nativeOidcProvider';

const DEFAULT_TOKEN_KEY = 'mozaiks_access_token';

function getApiUrl(): string {
  return appConfig.apiUrl ?? 'http://localhost:8000';
}

function requireNativeProvider(providerName: string): NativeAuthProvider {
  if (providerName === 'oidc' || providerName === 'keycloak-native') {
    return getConfiguredNativeAuthProvider(providerName);
  }

  const provider = getNativeAuthProvider();
  if (!provider) {
    throw new Error(
      `platforms.mobile.auth.provider="${providerName}" requires a registered native auth provider. ` +
      'Register one via registerNativeAuthProvider() before the app mounts.',
    );
  }

  const requiredMethods = [
    'getAccessToken',
    'getCurrentUser',
    'login',
    'logout',
    'refreshToken',
    'onAuthStateChange',
  ];

  for (const methodName of requiredMethods) {
    if (typeof (provider as Record<string, unknown>)[methodName] !== 'function') {
      throw new Error(
        `Native auth provider is missing required method "${methodName}" for auth provider "${providerName}".`,
      );
    }
  }

  return provider;
}

export function createMobilePlatformAuthBridge() {
  const mobileConfig = getMobilePlatformConfig();
  const authProvider = mobileConfig.auth.provider;

  switch (authProvider) {
    case 'token':
      return {
        getAccessToken: () => storage.getString(DEFAULT_TOKEN_KEY) ?? null,
      };

    case 'external':
      return requireNativeProvider(authProvider);

    case 'oidc':
    case 'keycloak-native':
      return requireNativeProvider(authProvider);

    default:
      throw new Error(`Unsupported mobile auth provider: ${String(authProvider)}`);
  }
}

export function createMobileAuthAdapter() {
  const mobileConfig = getMobilePlatformConfig();
  const authProvider = mobileConfig.auth.provider;

  switch (authProvider) {
    case 'token':
      return new TokenAuthAdapter(getApiUrl(), DEFAULT_TOKEN_KEY);

    case 'external':
      return new ExternalAuthAdapter(requireNativeProvider(authProvider));

    case 'oidc':
    case 'keycloak-native':
      return new ExternalAuthAdapter(requireNativeProvider(authProvider));

    default:
      throw new Error(`Unsupported mobile auth provider: ${String(authProvider)}`);
  }
}