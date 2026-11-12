# Changelog

All notable changes to DDEV Dashboard. The format follows [Keep a Changelog](https://keepachangelog.com/) and versions follow [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-12

First release.

### Added

- Project list with status, CPU and RAM per project, search and a running/stopped filter.
- Start, stop and restart per project, and stop all (`ddev poweroff`).
- New project wizard: empty folder or clone from git, type, PHP, web server, database, extra domains, optional phpMyAdmin.
- Domains: `*.ddev.site` hostnames and custom domains, with the hosts-file line to copy.
- Project settings: type, PHP version, web server, docroot, Xdebug on/off.
- Database: phpMyAdmin link and install, `.sql.gz` export, dump import, snapshots (create, restore, delete).
- Commands tab: one-click composer, npm, artisan, spark, wp and drush commands by project type, plus custom commands in the web container.
- Add-ons tab: install popular official add-ons or any `owner/repo`, and remove installed ones.
- System status: DDEV version and update check, Docker status and disk usage.
- Open the project folder in the file manager, or in VS Code.
- Logs for the web and db containers.
- Delete a project, with an optional safety snapshot.
- Live output for every running task.
- Greek and English interface, light and dark theme.
- Settings: language, where DDEV runs (WSL, Linux, macOS, Windows), default projects folder, ready-made autostart commands for each system.
- Deep links (`#project=name&tab=db`, `#new`, `#settings`).
- Demo mode (`DDEV_DASHBOARD_DEMO=1`) with sample data, used for screenshots and tests.
- Security: localhost only, Host header check, custom header for actions, validated input, no shell.

[1.0.0]: https://github.com/npsaltakis/ddev-dashboard/releases/tag/v1.0.0
