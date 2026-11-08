# Security Policy

## Supported versions

Security fixes are intended for the latest version on the default branch. No
older versions are currently designated for security support.

## Reporting a vulnerability

Please do not report security vulnerabilities in public issues or discussions.
Once this project is published at
[luminouselements/pwb](https://github.com/luminouselements/pwb), use GitHub's
**Report a vulnerability** option on the repository if private vulnerability
reporting is enabled. Otherwise, contact the project maintainer privately
through GitHub and request a secure channel.

Include the affected version or commit, the impact, and concise steps to
reproduce the issue. Avoid including real API keys, private Meshtastic
credentials, or other sensitive data. The maintainer will review the report
and coordinate any fix and disclosure with the reporter.

## Deployment notes

- Keep `PIRATE_WEATHER_API_KEY` and other credentials in an untracked `.env`
  file or a secrets manager. Never publish real credentials in issues, logs,
  or commits.
- Restrict access to the Meshtastic node and network used by the bot to
  trusted devices and users.
- Keep Python and the installed dependencies up to date.

This project is distributed under the MIT License. The license does not replace
the private reporting process described above.
