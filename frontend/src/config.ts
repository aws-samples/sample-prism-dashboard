export interface AppConfig {
  userPoolId: string;
  userPoolClientId: string;
  identityPoolId: string;
  region: string;
  apiUrl: string;
  oauthDomain: string;
  redirectUrl: string;
}

let cachedConfig: AppConfig | null = null;

export async function loadConfig(): Promise<AppConfig> {
  if (cachedConfig) return cachedConfig;

  const response = await fetch('/config.json');
  cachedConfig = await response.json();
  return cachedConfig!;
}
