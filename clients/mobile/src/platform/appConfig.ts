import manifest from '../../../../platform/app.json';

type PlatformAuthConfig = {
  provider?: 'token' | 'external' | 'oidc' | 'keycloak-native';
  redirectScheme?: string;
  redirectPath?: string;
  scopes?: string[];
};

type MobileVersionConfig = {
  name?: string;
  code?: number;
};

type MobileIosConfig = {
  bundleId?: string;
};

type MobileAndroidConfig = {
  applicationId?: string;
  namespace?: string;
};

type MobilePlatformConfig = {
  enabled?: boolean;
  displayName?: string;
  version?: MobileVersionConfig;
  ios?: MobileIosConfig;
  android?: MobileAndroidConfig;
  auth?: PlatformAuthConfig;
};

type AppManifest = {
  appName?: string;
  appId?: string;
  apiUrl?: string;
  wsUrl?: string;
  auth?: {
    provider?: string;
    keycloak?: Record<string, unknown>;
  };
  platforms?: {
    web?: { enabled?: boolean };
    mobile?: MobilePlatformConfig;
    desktop?: { enabled?: boolean };
  };
};

const appConfig = manifest as AppManifest;

export default appConfig;

function sanitizeIdentifierPart(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '.')
    .replace(/^\.+|\.+$/g, '')
    .replace(/\.{2,}/g, '.');
}

function deriveReverseDomainId(appId: string | undefined): string {
  const slug = sanitizeIdentifierPart(appId ?? 'app') || 'app';
  return `com.mozaiks.${slug}`;
}

export function getMobilePlatformConfig(): Required<MobilePlatformConfig> {
  const mobile = appConfig.platforms?.mobile ?? {};
  const derivedId = deriveReverseDomainId(appConfig.appId);
  return {
    enabled: mobile.enabled ?? false,
    displayName: mobile.displayName ?? appConfig.appName ?? 'Mozaiks',
    version: {
      name: mobile.version?.name ?? '1.0.0',
      code: mobile.version?.code ?? 1,
    },
    ios: {
      bundleId: mobile.ios?.bundleId ?? derivedId,
    },
    android: {
      applicationId: mobile.android?.applicationId ?? derivedId,
      namespace: mobile.android?.namespace ?? mobile.android?.applicationId ?? derivedId,
    },
    auth: {
      provider: mobile.auth?.provider ?? 'token',
      redirectScheme: mobile.auth?.redirectScheme ?? `${(appConfig.appId ?? 'mozaiks').replace(/[^a-zA-Z0-9]/g, '').toLowerCase()}`,
      redirectPath: mobile.auth?.redirectPath ?? 'oauthredirect',
      scopes: mobile.auth?.scopes ?? ['openid', 'profile', 'email'],
    },
  };
}
