FROM postgres:15.12-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv && \
    rm -rf /var/lib/apt/lists/* && \
    python3 -m venv /opt/patroni && \
    /opt/patroni/bin/pip install --no-cache-dir 'patroni[etcd3]==4.1.5'

ENV PATH="/opt/patroni/bin:/usr/lib/postgresql/15/bin:${PATH}"

COPY --chmod=0755 docker/postgres-ha/post-init.sh /usr/local/bin/posthog-post-init
COPY docker/postgres-init-scripts /usr/local/share/posthog/postgres-init-scripts
COPY products/db_routing.yaml /products/db_routing.yaml

USER postgres

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["patroni", "/etc/patroni.yml"]
