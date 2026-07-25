"""
Data source registry. Maps DataSourceType to implementation classes.
To add a new data source, import it here and add to DATASOURCE_REGISTRY.
"""

from datasources import DataSourceType
from datasources.github import GitHubDataSource
from datasources.gitlab import GitLabDataSource
from datasources.jira import JiraDataSource
from datasources.asana import AsanaDataSource
from datasources.kiro import KiroDataSource
from datasources.codeburn import CodeburnDataSource
from datasources.claude_code_analytics import ClaudeCodeAnalyticsDataSource

# Registry of all available data source plugins
DATASOURCE_REGISTRY = {
    DataSourceType.GITHUB: GitHubDataSource,
    DataSourceType.GITLAB: GitLabDataSource,
    DataSourceType.JIRA: JiraDataSource,
    DataSourceType.ASANA: AsanaDataSource,
    DataSourceType.KIRO: KiroDataSource,
    DataSourceType.CODEBURN: CodeburnDataSource,
    DataSourceType.CLAUDE_CODE_ANALYTICS: ClaudeCodeAnalyticsDataSource,
}
