/**
 * Which data source types are configured — drives side-nav visibility of the
 * per-source activity pages. Admins get the real answer from the datasources
 * API; non-admins can't call it, so all pages are shown and handle 403s
 * gracefully themselves.
 */
import { useEffect, useState } from 'react';
import { datasourcesApi } from './api';

export function useDataSourceFlags(isAdmin: boolean, refreshKey: number) {
  const [flags, setFlags] = useState({ hasGithub: false, hasAsana: false, hasGitlab: false, hasJira: false });

  useEffect(() => {
    if (isAdmin) {
      datasourcesApi.list().then((sources: any[]) => {
        setFlags({
          hasGithub: sources.some((s) => s.dataSourceType === 'github'),
          hasAsana: sources.some((s) => s.dataSourceType === 'asana'),
          hasGitlab: sources.some((s) => s.dataSourceType === 'gitlab'),
          hasJira: sources.some((s) => s.dataSourceType === 'jira'),
        });
      }).catch(() => {});
    } else {
      setFlags({ hasGithub: true, hasAsana: true, hasGitlab: true, hasJira: true });
    }
  }, [refreshKey, isAdmin]);

  return flags;
}
