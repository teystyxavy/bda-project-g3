# Local AWS login

Run this in your own PowerShell terminal so that you handle the login directly.
You do not need to paste credentials into Codex or create a project secrets file.

1. Install [AWS CLI v2 for Windows](https://awscli.amazonaws.com/AWSCLIV2.msi),
   then reopen PowerShell. Browser login needs version 2.32.0 or newer.
2. From `bda-project-g3/`, run:

   ```powershell
   .\scripts\aws\configure-aws.ps1
   ```

3. On AWS's browser page, sign in with your project IAM user or approved role
   and complete MFA. The helper then asks for the budget/failure alert email
   in PowerShell. Account ID comes from AWS automatically.
4. Tell Codex: `Login ready; use profile is459-g3-tools`.

For a university/company AWS access portal backed by IAM Identity Center, use:

```powershell
.\scripts\aws\configure-aws.ps1 -LoginMethod SSO
```

Use the SSO start URL and SSO region provided by the administrator. These may
differ from the project's Singapore deployment region. If browser login reports
missing `SignInLocalDevelopmentAccess`, ask the account administrator about that
permission; the helper does not change IAM permissions. Course lab environments
may require their own temporary-credential workflow instead.

The helper saves non-secret Terraform inputs (account, region, alert email and
budget) to `%LOCALAPPDATA%\IS459-G3\batch.tfvars.json`, outside OneDrive and the
repository. AWS manages its own profile configuration and cached temporary
credentials under `%USERPROFILE%\.aws`. Do not share or commit that folder.
Passwords and MFA codes are entered only into AWS's sign-in page.

The named profile `is459-g3` holds the login; `is459-g3-tools` forwards temporary
credentials to Terraform through AWS CLI's credential process.
The helper now includes the Singapore region directly in that credential
process command. A boto3 session's region does not propagate into the child
CLI process, which otherwise can fail with `NoRegion` during login refresh.
The credential process uses `aws.exe` from PATH to avoid Windows SDK handling
of a quoted executable path containing spaces; both setup/plan helpers prepend
the detected AWS CLI folder to PATH for their session.
These names are reserved for this project. To use different names, pass `-ProfileName your-name`;
the tools profile will be `your-name-tools`. The helper retains an existing
settings file and refuses an account or region mismatch.

In a new terminal, select the profile before running Terraform:

```powershell
$env:AWS_PROFILE = 'is459-g3-tools'
$env:AWS_REGION = 'ap-southeast-1'
```

For the deployment runbook, use `batch.tfvars.json` instead of its manual
`batch.tfvars` example when passing `-var-file`. Login grants no new permissions
and does not deploy resources. Re-run the helper when a login session expires.

References: [AWS browser login and credential-process compatibility](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html),
[IAM Identity Center login](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html).
