# Security Policy

## Supported Versions

Currently, we support the following versions with security updates:

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

If you discover a security vulnerability in gcli2api, please report it by:

1. **Email**: Contact the project maintainers directly (check repository for contact info)
2. **Private Issue**: If email is not available, create a security advisory on GitHub

### What to Include in Your Report

Please include the following information:

* Type of vulnerability
* Full paths of source file(s) related to the manifestation of the vulnerability
* Location of the affected source code (tag/branch/commit or direct URL)
* Step-by-step instructions to reproduce the issue
* Proof-of-concept or exploit code (if possible)
* Impact of the vulnerability, including how an attacker might exploit it

### Response Timeline

* We will acknowledge receipt of your vulnerability report within 48 hours
* We will provide a more detailed response within 7 days indicating the next steps
* We will keep you informed of the progress towards a fix

## Security Best Practices

When deploying gcli2api, follow these security recommendations:

### 1. Credential Management

* **Never commit credential files** (*.json, *.toml) to version control
* Store credentials securely using environment variables or secure storage backends
* Rotate credentials regularly
* Use the `AUTO_BAN` feature to automatically disable compromised credentials

### 2. Password Configuration

* **Change default passwords immediately** - The default password is `pwd`
* Use strong, unique passwords for both API and Panel access
* Use separate passwords (`API_PASSWORD` and `PANEL_PASSWORD`) in production
* Store passwords in environment variables, not in code or config files

### 3. Network Security

* Use HTTPS/TLS in production environments
* Configure proxy settings properly if using a proxy
* Limit access to the control panel (`/auth`) to trusted networks
* Use firewall rules to restrict access

### 4. Docker Security

* Don't run containers as root user when possible
* Use specific version tags instead of `latest` in production
* Regularly update base images for security patches
* Mount credential volumes with appropriate permissions

### 5. Storage Backend Security

#### Redis
* Use authentication (password)
* Use SSL/TLS connections (`rediss://`)
* Don't expose Redis port to public internet
* Use strong passwords

#### MongoDB
* Enable authentication
* Use connection strings with credentials
* Limit network access
* Keep MongoDB updated

#### PostgreSQL
* Use strong passwords
* Enable SSL connections
* Regularly update PostgreSQL
* Limit network exposure

### 6. API Security

* Validate all input data
* Use rate limiting for API endpoints
* Monitor for unusual activity
* Enable `AUTO_BAN` for automatic threat response
* Keep dependencies updated

### 7. Environment Variables

* Never commit `.env` files
* Use `.env.example` as template only
* Validate environment variable values
* Use secrets management in cloud deployments

### 8. Logging

* Review logs regularly for suspicious activity
* Don't log sensitive information (credentials, tokens)
* Set appropriate log levels for production
* Rotate and archive logs regularly

## Known Security Considerations

### OAuth Flow Limitation

The OAuth authentication flow currently **only supports localhost access**. This means:

* Initial OAuth must be completed on `http://127.0.0.1:7861/auth`
* For cloud/remote deployments, complete OAuth locally first
* Upload generated credential files through the web panel
* This is by design for security - OAuth tokens should not transit untrusted networks

### License Compliance

Using this software commercially or against license terms may expose you to legal risks. Ensure you comply with the CNC-1.0 license terms.

## Dependency Security

We monitor our dependencies for known vulnerabilities. To check dependencies:

```bash
# Install safety
pip install safety

# Check for known vulnerabilities
safety check -r requirements.txt
```

## Updates and Patches

* Subscribe to repository releases for security updates
* Test updates in a staging environment before production
* Review CHANGELOG.md for security-related changes
* Keep your installation up to date

## Disclaimer

This project is provided "as is" for educational and research purposes only. Users are responsible for:

* Securing their own deployments
* Complying with applicable laws and regulations
* Following Google's Terms of Service and API usage policies
* Protecting their own credentials and data

The maintainers are not responsible for any security incidents, data breaches, or misuse of this software.

## Contact

For security concerns, please reach out through:

* GitHub Security Advisories (preferred)
* Project maintainer contact information in repository

Thank you for helping keep gcli2api and its users safe!
