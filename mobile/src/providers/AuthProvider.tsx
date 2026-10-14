import * as AuthSession from 'expo-auth-session';
import * as SecureStore from 'expo-secure-store';
import * as WebBrowser from 'expo-web-browser';
import {
  createContext,
  PropsWithChildren,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { Platform } from 'react-native';

WebBrowser.maybeCompleteAuthSession();

const domain = process.env.EXPO_PUBLIC_AUTH0_DOMAIN ?? '';
const nativeClientId = process.env.EXPO_PUBLIC_AUTH0_NATIVE_CLIENT_ID ?? '';
const webClientId = process.env.EXPO_PUBLIC_AUTH0_WEB_CLIENT_ID ?? '';
const clientId = Platform.OS === 'web' ? webClientId : nativeClientId;
const audience = process.env.EXPO_PUBLIC_AUTH0_API_AUDIENCE ?? '';
const tokenStorageKey = 'medica.access-session.v1';
const isRealValue = (value: string) => Boolean(value && !/^(replace-with-|your-tenant|https:\/\/medica-api\.example)/i.test(value));
const auth0DomainIsConfigured = isRealValue(domain);
const discoverySource = auth0DomainIsConfigured
  ? `https://${domain}/`
  : {
      authorizationEndpoint: 'https://invalid.example/authorize',
      tokenEndpoint: 'https://invalid.example/oauth/token',
    };

type StoredSession = {
  accessToken: string;
  expiresAt: number;
};

type AuthContextValue = {
  accessToken: string | null;
  isLoading: boolean;
  isConfigured: boolean;
  isSigningIn: boolean;
  error: string | null;
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

async function readSession(): Promise<StoredSession | null> {
  const raw = Platform.OS === 'web'
    ? window.localStorage.getItem(tokenStorageKey)
    : await SecureStore.getItemAsync(tokenStorageKey);
  if (!raw) return null;
  try {
    const stored = JSON.parse(raw) as StoredSession;
    if (!stored.accessToken || stored.expiresAt <= Date.now() + 30_000) return null;
    return stored;
  } catch {
    return null;
  }
}

async function writeSession(value: StoredSession | null) {
  if (Platform.OS === 'web') {
    if (value) window.localStorage.setItem(tokenStorageKey, JSON.stringify(value));
    else window.localStorage.removeItem(tokenStorageKey);
    return;
  }
  if (value) await SecureStore.setItemAsync(tokenStorageKey, JSON.stringify(value));
  else await SecureStore.deleteItemAsync(tokenStorageKey);
}

export function AuthProvider({ children }: PropsWithChildren) {
  const isConfigured = auth0DomainIsConfigured && isRealValue(clientId) && isRealValue(audience);
  const discovery = AuthSession.useAutoDiscovery(discoverySource);
  const redirectUri = AuthSession.makeRedirectUri({ scheme: 'medica', path: 'callback' });
  const [request, response, promptAsync] = AuthSession.useAuthRequest(
    {
      clientId,
      redirectUri,
      responseType: AuthSession.ResponseType.Code,
      scopes: ['openid', 'profile', 'email'],
      usePKCE: true,
      extraParams: audience ? { audience } : undefined,
    },
    discovery,
  );
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSigningIn, setIsSigningIn] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    readSession()
      .then(async (stored) => {
        if (!stored) await writeSession(null);
        if (active) setAccessToken(stored?.accessToken ?? null);
      })
      .catch(() => {
        if (active) setAccessToken(null);
      })
      .finally(() => {
        if (active) setIsLoading(false);
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!response) return;
    if (response.type === 'error') {
      setError(response.error?.message ?? 'Auth0 sign-in did not complete.');
      setIsSigningIn(false);
      return;
    }
    if (response.type !== 'success' || !response.params.code || !request?.codeVerifier || !discovery) {
      if (response.type === 'cancel' || response.type === 'dismiss') setIsSigningIn(false);
      return;
    }

    let active = true;
    AuthSession.exchangeCodeAsync(
      {
        clientId,
        code: response.params.code,
        redirectUri,
        extraParams: { code_verifier: request.codeVerifier },
      },
      discovery,
    )
      .then(async (tokenResult) => {
        if (!tokenResult.accessToken) throw new Error('Auth0 did not return an API access token.');
        const stored: StoredSession = {
          accessToken: tokenResult.accessToken,
          expiresAt: Date.now() + (tokenResult.expiresIn ?? 3600) * 1000,
        };
        await writeSession(stored);
        if (active) {
          setAccessToken(stored.accessToken);
          setError(null);
        }
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : 'Sign-in failed.');
      })
      .finally(() => {
        if (active) setIsSigningIn(false);
      });
    return () => { active = false; };
  }, [discovery, redirectUri, request, response]);

  const value = useMemo<AuthContextValue>(() => ({
    accessToken,
    isLoading,
    isConfigured,
    isSigningIn,
    error,
    async signIn() {
      if (!isConfigured || !request || !discovery) {
        setError('Set the Auth0 domain, platform client ID, and API audience in mobile/.env.');
        return;
      }
      setError(null);
      setIsSigningIn(true);
      try {
        const result = await promptAsync();
        if (result.type !== 'success') setIsSigningIn(false);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : 'Could not open Auth0 sign-in.');
        setIsSigningIn(false);
      }
    },
    async signOut() {
      await writeSession(null);
      setAccessToken(null);
      setError(null);
      if (isConfigured) {
        const returnUri = AuthSession.makeRedirectUri({ scheme: 'medica', path: 'signed-out' });
        const logoutUrl = new URL(`https://${domain}/v2/logout`);
        logoutUrl.searchParams.set('client_id', clientId);
        logoutUrl.searchParams.set('returnTo', returnUri);
        if (Platform.OS === 'web') {
          window.location.assign(logoutUrl.toString());
        } else {
          await WebBrowser.openAuthSessionAsync(logoutUrl.toString(), returnUri).catch(() => undefined);
        }
      }
    },
  }), [accessToken, discovery, error, isConfigured, isLoading, isSigningIn, promptAsync, request]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside AuthProvider.');
  return value;
}