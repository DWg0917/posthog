# Custom Hobby deployment images

Run `sudo go run . --custom` from `bin/hobby-installer` in the custom branch checkout.
The installer records the app, node, and Feature Flags image references in `.env`.
Set `POSTHOG_FEATURE_FLAGS_IMAGE` to a published image compatible with the app database schema.
The deployment pulls that image from its registry before starting migrations and services.
It does not build Feature Flags on the deployment host or use the upstream `master` image.

For existing configurations, add `POSTHOG_FEATURE_FLAGS_IMAGE` before running `deploy.sh`.
Compose fails before deployment if that reference is missing.
An image tag alone does not prove compatibility: verify migrations, readiness, and an actual flag evaluation.

The current shared Rust Dockerfile expects proto files inside its build context.
CI copies the repository `proto/` directory into `rust/proto/` before building.
A bare build with only `rust/` and no proto files fails during protobuf compilation.
Do not substitute an upstream image to work around this failure.

To publish a replacement from committed sources on a build server:

```sh
bin/build-custom-feature-flags REGISTRY/IMAGE:TAG GIT_REF
```

This uses the repository Rust Dockerfile with the same proto layout as CI,
adds the source revision as an image label, and pushes the amd64 image.
It uses an isolated temporary context and leaves the checkout unchanged.
Deploy the published tag through the Go installer after verifying the build.
