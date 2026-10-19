# Custom Hobby ClickHouse authentication

The custom installer generates a separate 256-bit random password for `default`.
The account remains restricted to loopback addresses. The deployment requires
`CLICKHOUSE_DEFAULT_PASSWORD`; Compose refuses to start without it.

PostHog connects as `app` using `CLICKHOUSE_APP_PASSWORD`. Override both
`CLICKHOUSE_USER` and `CLICKHOUSE_PASSWORD` in the private `.env` to use another
configured account. Logs connections have separate `CLICKHOUSE_LOGS_CLUSTER_USER`
and `CLICKHOUSE_LOGS_CLUSTER_PASSWORD` overrides. Changing a client variable does
not create a server-side user or change its password.

The `api` and `dict_reader` accounts retain their separate generated passwords.
Cluster replica connections and initialization use the authenticated `app`
account. The billing client uses `app` because this deployment has no `billing`
account. Keep generated credentials in the installer's mode-0600 environment files.

After changing credentials, recreate the affected containers and check authenticated
queries from an application container, a distributed query, and rejection of empty
and incorrect passwords. A successful `/ping` health check does not test authentication.
Never reset database volumes to fix authentication.
