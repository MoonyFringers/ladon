# Contribution Guidelines

Welcome to our project! We're excited that you're
interested in contributing. To ensure a smooth
collaboration, please read and follow the guidelines
below.

## Getting Started

1. **Fork the repository**: Create your own copy of the
   repository by clicking the "Fork" button.

2. **Clone the repository**: Clone your fork to your local
   machine:

   ```bash
   git clone git@github.com:MoonyFringers/ladon.git
   ```

3. **Set up upstream**: Add the original repository as an
   upstream remote:

   ```bash
   git remote add upstream git@github.com:MoonyFringers/ladon.git
   ```

## Before You Start

1. **Check open issues**: Look at the existing issues to find
   one you'd like to work on or open a new issue if needed.
2. **Discuss your idea**: Comment on an issue or create a new
   one to communicate your proposed changes before starting.
3. **Assign yourself**: If applicable, assign the issue to
   yourself.

## Making Changes

1. **Create a branch**: Create a new branch for your work.
   Use a descriptive name:

   ```bash
   git checkout -b feature/your-feature-name
   ```

   Please note that the branch name should follow a precise
   convention:

   **[TYPE]/[BRIEF_DESCRIPTION]**

   where **TYPE** is one of the following:

   | Type       | Description                                      |
   | ---------- | ------------------------------------------------ |
   | `feature`  | New functionality or enhancements                |
   | `bug`      | Bug fixes and corrections                        |
   | `docs`     | Documentation updates                            |
   | `test`     | Adding or updating tests                         |
   | `refactor` | Code restructuring without functionality changes |

   **Examples:**

   - feature/user-authentication
   - bug/fix-login-validation
   - docs/api-documentation
   - test/unit-tests-auth
   - refactor/cleanup-user-service

   **Naming Guidelines:**

   - Use lowercase letters and hyphens (kebab-case)
   - Keep descriptions brief but descriptive (2-4 words)
   - Avoid special characters except hyphens

   **Examples:**

   ```bash
   git checkout -b feature/password-reset
   git checkout -b bug/fix-memory-leak
   git checkout -b docs/contributing-guidelines
   git checkout -b test/add-auth-tests
   git checkout -b refactor/user-service-cleanup
   ```

2. **Make changes**: Implement your changes in the new branch.

3. **Write clear and standard compliant commit messages**: Use meaningful commit
   messages that describe your changes and please ensure that your commit
   message is compliant with Conventional Commit Standard,
   you can find some reference on the
   [official website](https://www.conventionalcommits.org/en/v1.0.0).

   A good examples is:

   ```bash
   git commit -m "docs: correct typo in README.md"
   ```

   **Commit Message Guidelines:**

   - Use a clear, descriptive subject line (50 characters or
     less)
   - Start with a prefix describing the type of your change, like
     `fix:`, `feat:`, `docs:`, etc. (more on the [official documentation](https://www.conventionalcommits.org/en/v1.0.0))
   - (Optional) If present, reference the related issue at the end of the message
     using GitHub keywords:

      ```bash
      git commit -m "fix: resolve login validation error

      Fixes #42"
      ```

      and use keywords like `Fixes`, `Closes`, `Resolves` followed
      by the issue number. This allows GitHub to automatically link and close
      issues when the PR is merged.

   - **NOTE:** A commit-template file is available in the _docs_ directory.
   You can copy it to your home directory (optionally as a hidden file) and
   add the following to your .gitconfig:

   ```bash
   [init]
    templateDir = ~/.commit-template
   ```

4. **Follow coding standards**: Adhere to the project's code
   style guidelines. Run tests and linters where applicable.

5. **Push your branch**: Push your changes to your fork:

   ```bash
   git push origin feature/your-feature-name
   ```

## Submitting Your Contribution

1. **Create a pull request (PR)**: Go to the original
   repository and open a PR. Include the following:

   - A clear and descriptive title.
   - A summary of your changes.
   - Link to related issues. If no issue is present, please create one.

   **Important**: After opening the PR, make sure to link the
   related issue(s) using GitHub's linking feature in the PR
   sidebar or by including keywords like `Fixes #issue-number`,
   `Closes #issue-number`, or `Resolves #issue-number` in the
   PR description. This ensures that issues are automatically closed when the
   PR is merged into the main branch. Please also note that it's important to
   have an issue related to the PR you are submitting because it makes clear
   the reason for your contribution and helps the tracking of changes. This is
   also the reason why we specifically asked to create an issue if not already
   present.

2. **Respond to feedback**: Address comments and suggestions
   from reviewers promptly.

## Code of Conduct

Please adhere to the [Code of Conduct](CODE_OF_CONDUCT.md) to
ensure a welcoming environment for all contributors.

## Contribution Tips

1. **Keep PRs small**: Smaller, focused PRs are easier to
   review and merge.
2. **Document your changes**: Update documentation, comments,
   or tests as needed.
3. **Test your code**: Ensure that your changes do not break
   existing functionality by running tests locally.

## Licensing and Contributor License Agreement

Ladon is dual-licensed under the
[GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0-only, open source)
and a [proprietary commercial license](LICENSE-COMMERCIAL) offered by Moony
Fringers.

To enable this dual-licensing model, **all contributors must sign the
Ladon [Contributor License Agreement](CLA.md) (CLA)** before their
contributions can be accepted. The CLA grants Moony Fringers a perpetual,
worldwide, sublicensable copyright and patent license over your contributions.
**You retain full copyright ownership.**

### How to sign

The CLA is enforced automatically on every pull request via the CLA Assistant
bot. When you open a PR, the bot will post a comment if you have not yet signed.
To sign, read [CLA.md](CLA.md) and reply to the bot comment with:

> I have read the CLA Document and I hereby sign the CLA

Your GitHub username is then recorded in `.github/cla_signatures.json`.

**Employed contributors:** If you are contributing as part of your employment
and your employer may have rights to your work, please review Section 5(a) of
the CLA before signing and ensure you have the appropriate authorization.

See [ADR-004](docs/decisions/adr-004-dual-license-model.md) for the rationale
behind this licensing model.

Thank you for contributing! Together, we can make this project
even better. If you have any questions, feel free to reach out.
