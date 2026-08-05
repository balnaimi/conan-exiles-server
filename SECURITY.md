# Security Policy

## Supported versions

Security fixes are provided for the current `2.8.x` release line. Critical fixes may also be backported to `2.7.x`. Native is recommended for new servers; Wine remains supported for existing deployments and backward compatibility.

## Reporting a vulnerability

Please use GitHub's **Report a vulnerability** private advisory form for this repository. Do not open a public issue for a vulnerability that could expose server data, credentials, host files, container escape paths, or destructive volume behavior.

Include, when available:

- Affected image tag and immutable digest.
- Wine or Native Linux runtime.
- Docker and Compose versions.
- Reproduction steps using synthetic data.
- Expected and actual behavior.
- Impact and any known workaround.

Never include a real `.env`, password file, RCON password, server password, token, private key, unredacted process arguments, world database, or backup archive. Rotate any credential that was exposed before reporting it.

You should receive an acknowledgement within seven days. Public disclosure is coordinated after a fix or mitigation is available.
