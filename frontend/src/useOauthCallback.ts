/**
 * Handles the data-source OAuth redirect landing (?code&state on any path):
 * exchanges the code via the backend, cleans the URL, and exposes a status
 * message plus a refresh key that bumps when a token exchange succeeds
 * (consumers re-fetch datasources / remount the Data Sources page on it).
 */
import { useEffect, useState } from 'react';
import { datasourcesApi } from './api';

export function useOauthCallback(onNavigateToDataSources: () => void) {
  const [oauthStatus, setOauthStatus] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    const state = params.get('state');
    if (code && state) {
      const redirectUri = sessionStorage.getItem('oauth_redirect_uri') || window.location.origin + '/oauth-callback';
      window.history.replaceState({}, '', window.location.pathname);
      sessionStorage.removeItem('oauth_ds_id');
      sessionStorage.removeItem('oauth_redirect_uri');
      onNavigateToDataSources();

      datasourcesApi.oauthCallback(state, code, redirectUri)
        .then((result: any) => {
          setOauthStatus(result.hasRefreshToken ? 'OAuth authorization successful' : 'OAuth completed but no refresh token received');
          setRefreshKey((k) => k + 1);
        })
        .catch(() => setOauthStatus('OAuth callback failed'));
    }
    // eslint-disable-next-line
  }, []);

  return { oauthStatus, refreshKey };
}
