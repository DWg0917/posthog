# Data warehouse workers in custom deployments

Select the `worker-data-warehouse` profile to enable external data source sync. Add it to the existing comma-separated `COMPOSE_PROFILES` value in `.env` and run the deployment script. The profile starts both the data warehouse Temporal worker group and the warehouse sources load service. Removing the profile stops both services on the next deployment.

The services use the deployment's Redis password, encryption salt, and object storage credentials. Keep these values consistent across deployments. The data warehouse Temporal group also handles metadata, modeling, and DuckLake queues.
